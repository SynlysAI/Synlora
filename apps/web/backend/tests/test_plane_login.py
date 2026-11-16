"""Plane 账号登录：代理验证、身份归一、/me 豁免、delegated 回退 sub 归一。"""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Response

from app.api.auth_api import PLANE_LOGIN_TTL_HOURS
from app.core.auth import issue_token, parse_token
from app.core.settings import Settings
from app.db.store import create_store
from app.main import create_app
from app.services.plane_auth import (
    PlaneAuthError,
    PlaneProfile,
    authenticate as plane_authenticate,
)


def _settings(**kw) -> Settings:
    """构造测试配置（sqlite 后端，避免环境变量干扰）。"""
    base = {
        "auth_secret": "unit-test-secret",
        "auth_enabled": True,
        "storage_backend": "sqlite",
        "plane_base_url": "http://plane.test",
    }
    base.update(kw)
    return Settings(**base)


def _redirect(code: str | None = None) -> Response:
    """构造 sign-in 的 302 响应（带/不带 error_code）。"""
    location = (
        f"http://plane.test/?error_code={code}&error_message=X"
        if code
        else "http://plane.test/spaces"
    )
    return Response(302, headers={"Location": location})


def _mock_plane_transport(
    *, login_ok: bool = True, error_code: str | None = None
) -> MockTransport:
    """模拟 Plane 三步登录流（cookie 状态由 httpx MockTransport 内部维持）。

    MockTransport 不做真实 Set-Cookie 处理，sessionid cookie 由本工厂在
    sign-in 成功时显式种到响应上（httpx 会把它写进客户端 cookie jar）。
    """

    def handler(request) -> Response:
        path = request.url.path
        if path == "/auth/get-csrf-token/":
            resp = Response(200, json={"csrf_token": "tok123"})
            resp.headers["Set-Cookie"] = "csrftoken=abc; Path=/"
            return resp
        if path == "/auth/sign-in/":
            if not login_ok:
                return _redirect(error_code or "5065")
            resp = _redirect()
            # Plane 定制会话 cookie 名是 session-id（settings/common.py 缺省值）
            resp.headers["Set-Cookie"] = "session-id=sess1; Path=/"
            return resp
        if path == "/api/users/me/":
            return Response(
                200,
                json={"id": "uuid-1", "email": "alice@plane.test",
                      "display_name": "Alice"},
            )
        return Response(404)

    return MockTransport(handler)


@pytest.fixture
async def plane_app(tmp_path):
    """带 plane 配置与 sqlite store 的 app。"""
    app = create_app()
    app.state.settings = _settings()
    store = create_store("sqlite", sqlite_path=str(tmp_path / "plane.db"))
    await store.init()
    app.state.store = store
    yield app, store
    await store.close()


@pytest.fixture
async def client(plane_app):
    """httpx 异步客户端（ASGI 直连）。"""
    app, _ = plane_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def _patch_transport(monkeypatch, transport_factory):
    """把 authenticate 的 transport 固定为测试工厂。"""
    async def fake_authenticate(email, password, settings, transport=None):
        return await plane_authenticate(
            email, password, settings, transport=transport_factory()
        )
    monkeypatch.setattr("app.api.auth_api.plane_authenticate", fake_authenticate)


# ---------- 服务层（三步登录流） ----------


async def test_authenticate_success_flow():
    """三步流成功：CSRF -> sign-in 302 无错误且种 session -> me 取档案。"""
    profile = await plane_authenticate(
        "alice@plane.test", "pw", _settings(),
        transport=_mock_plane_transport(),
    )
    assert profile == PlaneProfile(
        plane_user_id="uuid-1", email="alice@plane.test", display_name="Alice"
    )


async def test_authenticate_maps_error_codes():
    """错误码映射：限流 429、实例未配置 503、账号禁用 401、其余 401。"""
    cases = [
        ("5900", 429),
        ("5000", 503),
        ("5019", 401),
        ("5060", 401),  # USER_DOES_NOT_EXIST
        ("5065", 401),  # AUTHENTICATION_FAILED_SIGN_IN
    ]
    for code, expected in cases:
        with pytest.raises(PlaneAuthError) as exc:
            await plane_authenticate(
                "a@b.c", "pw", _settings(),
                transport=_mock_plane_transport(login_ok=False, error_code=code),
            )
        assert exc.value.status_code == expected


async def test_authenticate_not_configured_503():
    """未配置 Plane 地址时返回 503 而非抛网络错误。"""
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", _settings(plane_base_url=""))
    assert exc.value.status_code == 503


async def test_authenticate_redirect_without_session_is_502():
    """302 无 error_code 但未种会话 cookie：按服务异常（不当作成功）。"""

    def handler(request) -> Response:
        if request.url.path == "/auth/get-csrf-token/":
            return Response(200, json={"csrf_token": "tok"})
        if request.url.path == "/auth/sign-in/":
            return _redirect()  # 成功跳转但没种 cookie
        return Response(404)

    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate(
            "a@b.c", "pw", _settings(), transport=MockTransport(handler)
        )
    assert exc.value.status_code == 502


async def test_authenticate_accepts_stock_django_session_cookie():
    """兼容原生 Django 会话 cookie 名 sessionid 的 Plane 部署。"""

    def handler(request) -> Response:
        if request.url.path == "/auth/get-csrf-token/":
            return Response(200, json={"csrf_token": "tok"})
        if request.url.path == "/auth/sign-in/":
            resp = _redirect()
            resp.headers["Set-Cookie"] = "sessionid=sess1; Path=/"
            return resp
        if request.url.path == "/api/users/me/":
            return Response(200, json={"id": "uuid-1", "email": "a@b.c"})
        return Response(404)

    profile = await plane_authenticate(
        "a@b.c", "pw", _settings(), transport=MockTransport(handler)
    )
    assert profile.plane_user_id == "uuid-1"


# ---------- 端点集成 ----------


async def test_plane_login_issues_token_with_plane_sub(plane_app, client, monkeypatch):
    """未绑定账户时 sub=plane:<uuid>，payload 带 auth/plane_user_id/email。"""
    app, _ = plane_app
    _patch_transport(monkeypatch, _mock_plane_transport)
    r = await client.post(
        "/api/v1/auth/plane-login",
        json={"email": "Alice@Plane.test", "password": "pw"},  # 大小写归一
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "Alice"
    assert body["role"] == "user"
    payload = parse_token(body["token"], app.state.settings)
    assert payload is not None
    assert payload["sub"] == "plane:uuid-1"
    assert payload["auth"] == "plane"
    assert payload["plane_user_id"] == "uuid-1"
    assert payload["email"] == "alice@plane.test"
    # 短时效（24h），而非本方登录的 7 天
    assert payload["exp"] - payload["iat"] == PLANE_LOGIN_TTL_HOURS * 3600


async def test_plane_login_uses_account_link_when_bound(plane_app, client, monkeypatch):
    """已有 plane_account_links 绑定时收敛到绑定的 Synlora 账户。"""
    _, store = plane_app
    await store.insert("plane_account_links", {
        "_id": "link-1",
        "account_link_id": "link-1",
        "plane_workspace_id": "ws-1",
        "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu",
        "delegated_subject": "plane-delegated-ws-1-user-uuid-1",
        "created_at": 1,
    })
    _patch_transport(monkeypatch, _mock_plane_transport)
    r = await client.post(
        "/api/v1/auth/plane-login",
        json={"email": "alice@plane.test", "password": "pw"},
    )
    assert r.status_code == 200
    app, _ = plane_app
    payload = parse_token(r.json()["token"], app.state.settings)
    assert payload["sub"] == "xiaoxu"


async def test_plane_login_bad_credentials_401(plane_app, client, monkeypatch):
    """密码错误透传 401；无效应入参（缺 @ / 空密码）同样 401。"""
    _patch_transport(monkeypatch, lambda: _mock_plane_transport(login_ok=False))
    r = await client.post(
        "/api/v1/auth/plane-login",
        json={"email": "a@b.c", "password": "wrong"},
    )
    assert r.status_code == 401
    r2 = await client.post(
        "/api/v1/auth/plane-login",
        json={"email": "not-an-email", "password": "pw"},
    )
    assert r2.status_code == 401


async def test_plane_login_unreachable_maps_502(plane_app, client, monkeypatch):
    """Plane 不可达映射 502（不暴露内部异常细节）。"""

    def handler(request) -> Response:
        raise httpx.ConnectError("boom")

    _patch_transport(monkeypatch, lambda: MockTransport(handler))
    r = await client.post(
        "/api/v1/auth/plane-login",
        json={"email": "a@b.c", "password": "pw"},
    )
    assert r.status_code == 502


async def test_me_exempts_plane_identity_on_mongodb(plane_app, monkeypatch):
    """mongodb 模式 /me：plane 来源 token 跳过 ai4ms.users 回查。"""
    app, _ = plane_app
    app.state.settings = _settings(storage_backend="mongodb")
    token = issue_token(
        {"sub": "plane:uuid-1", "username": "Alice", "role": "user",
         "auth": "plane", "plane_user_id": "uuid-1"},
        app.state.settings,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["sub"] == "plane:uuid-1"


async def test_me_exempts_delegated_fallback_subject(plane_app):
    """旧格式 delegated 回退身份（plane-delegated-*）同样豁免回查。"""
    app, _ = plane_app
    app.state.settings = _settings(storage_backend="mongodb")
    token = issue_token(
        {"sub": "plane-delegated-ws-1-user-uuid-9", "username": "x", "role": "user"},
        app.state.settings,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_me_still_rechecks_local_identity_on_mongodb(plane_app, monkeypatch):
    """非 plane 身份在 mongodb 模式仍回查（用户不存在 -> 401）。"""
    app, _ = plane_app
    app.state.settings = _settings(storage_backend="mongodb")

    async def fake_find_user(request, username):
        return None

    monkeypatch.setattr("app.api.auth_api.find_user", fake_find_user)
    token = issue_token(
        {"sub": "local-1", "username": "ghost", "role": "user"},
        app.state.settings,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------- delegated-token 回退 sub 归一 ----------


def _delegated_body(**overrides) -> dict:
    """构造 plane-delegated-auth.v1 请求体。"""
    body = {
        "schema_version": "plane-delegated-auth.v1",
        "account_link_id": "plane-ws-1-uuid-1",
        "workspace_id": "ws-1",
        "plane_user_id": "uuid-1",
        "external_subject": "plane-delegated-ws-1-user-uuid-1",
        "subject_username": "57f58623223a4725",
    }
    body.update(overrides)
    return body


async def test_delegated_token_fallback_sub_normalized(plane_app, monkeypatch):
    """未匹配本方账户时回退 sub 归一为 plane:<plane_user_id>。"""
    app, _ = plane_app
    app.state.settings = _settings(plane_service_token="svc-tok")
    settings = app.state.settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/research/delegated-token",
            json=_delegated_body(),
            headers={"Authorization": "Bearer svc-tok"},
        )
    assert r.status_code == 200
    payload = parse_token(r.json()["token"], settings)
    assert payload is not None
    assert payload["sub"] == "plane:uuid-1"
    assert payload["auth"] == "plane"
    assert payload["plane_delegated"] is True


async def test_delegated_token_linked_still_uses_linked_user(plane_app):
    """匹配到本方账户（subject_username 命中 sqlite local_users）时仍发绑定身份。"""
    app, _ = plane_app
    app.state.settings = _settings(plane_service_token="svc-tok")
    settings = app.state.settings
    from app.core.auth import hash_password
    await app.state.store.insert("local_users", {
        "_id": "u-1", "username": "57f58623223a4725",
        "password_hash": hash_password("x"),  # delegated 不验密码，占位即可
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/research/delegated-token",
            json=_delegated_body(),
            headers={"Authorization": "Bearer svc-tok"},
        )
    assert r.status_code == 200
    body = r.json()
    payload = parse_token(body["token"], settings)
    assert payload is not None
    assert payload["sub"] == "u-1"
    assert body["linked_user_id"] == "u-1"
    assert payload["plane_delegated"] is True
