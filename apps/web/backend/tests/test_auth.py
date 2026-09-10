"""认证测试：token 解析/签发、PBKDF2 密码、认证 API 集成。"""
import base64
import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import (
    _secret,
    hash_password,
    issue_token,
    parse_token,
    verify_password,
)
from app.core.settings import Settings
from app.db.store import create_store
from app.main import create_app


def _settings(**kw) -> Settings:
    """构造测试配置（显式固定 auth 相关字段，避免环境变量干扰）。

    Args:
        kw: 覆盖字段。

    Returns:
        测试用 Settings 实例。
    """
    base = {"auth_secret": "unit-test-secret", "auth_enabled": True}
    base.update(kw)
    return Settings(**base)


def _sign_raw(payload: dict, settings: Settings) -> str:
    """绕过 issue_token 的自动 exp，直接按格式手工签发。

    Args:
        payload: 完整 payload（须自带 exp）。
        settings: 应用配置。

    Returns:
        `{payload_b64}.{hmac_hex}` 格式 token。
    """
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


# ---------- 单元测试 ----------


def test_issue_parse_roundtrip():
    """签发 → 解析应还原 sub/username/role，并自动补 iat/exp。"""
    settings = _settings()
    token = issue_token({"sub": "u1", "username": "alice", "role": "admin"}, settings)
    payload = parse_token(token, settings)
    assert payload is not None
    assert payload["sub"] == "u1"
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"
    assert payload["exp"] > payload["iat"]


def test_tampered_signature_rejected():
    """篡改签名末位字符应被拒绝。"""
    settings = _settings()
    token = issue_token({"sub": "u1", "username": "a", "role": "user"}, settings)
    bad = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert parse_token(bad, settings) is None


def test_malformed_token_rejected():
    """缺少分隔符的 token 应返回 None。"""
    settings = _settings()
    assert parse_token("not-a-token", settings) is None


def test_expired_token_rejected():
    """exp 已过期的 token 应被拒绝。"""
    settings = _settings()
    now = int(time.time())
    token = _sign_raw(
        {"sub": "u1", "username": "a", "role": "user",
         "iat": now - 8 * 86400, "exp": now - 100},
        settings,
    )
    assert parse_token(token, settings) is None


def test_invalid_role_rejected():
    """role 不在 (admin, user) 内应被拒绝。"""
    settings = _settings()
    now = int(time.time())
    token = _sign_raw(
        {"sub": "u1", "username": "a", "role": "guest",
         "iat": now, "exp": now + 3600},
        settings,
    )
    assert parse_token(token, settings) is None


def test_password_hash_roundtrip():
    """PBKDF2 哈希/校验往返；错误密码 False；固定盐可复现。"""
    stored = hash_password("s3cret")
    assert stored.startswith("pbkdf2_sha256$260000$")
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)
    # 同盐同密码 → 同哈希（与 AI4MS 门户格式逐字兼容）
    salt_hex = stored.split("$")[2]
    assert hash_password("s3cret", salt_hex=salt_hex) == stored


def test_verify_password_malformed_stored():
    """存储格式损坏时校验返回 False 而非抛异常。"""
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")


# ---------- API 集成测试 ----------


@pytest.fixture
async def auth_app(tmp_path):
    """带临时配置与 sqlite store 的 app。

    Yields:
        (app, settings) 元组。
    """
    settings = _settings(dev_auth_token="devtok", storage_backend="sqlite")
    app = create_app()
    app.state.settings = settings
    store = create_store("sqlite", sqlite_path=str(tmp_path / "auth.db"))
    await store.init()
    app.state.store = store
    yield app, settings
    await store.close()


@pytest.fixture
async def client(auth_app):
    """httpx 异步客户端（ASGI 直连）。

    Yields:
        AsyncClient 实例。
    """
    app, _ = auth_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_me_with_dev_token(client):
    """DEV_AUTH_TOKEN 命中时 /me 返回 dev 管理员。"""
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer devtok"})
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "dev"
    assert body["username"] == "dev"
    assert body["role"] == "admin"


async def test_me_without_token_401(client):
    """无 token 访问 /me 应 401。"""
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_me_with_bad_token_401(client):
    """无效 token 访问 /me 应 401。"""
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk.junk"})
    assert r.status_code == 401


async def test_sqlite_login_flow(auth_app, client):
    """sqlite 登录：正确密码拿 token 且 /me 回显；错误密码/未知用户 401。"""
    app, settings = auth_app
    await app.state.store.insert("local_users", {
        "_id": "u-local-1",
        "username": "alice",
        "password_hash": hash_password("pw"),
    })
    r = await client.post("/api/v1/auth/login",
                          json={"username": "alice", "password": "pw"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert parse_token(token, settings) is not None

    me = await client.get("/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    bad = await client.post("/api/v1/auth/login",
                            json={"username": "alice", "password": "nope"})
    assert bad.status_code == 401
    missing = await client.post("/api/v1/auth/login",
                                json={"username": "nobody", "password": "pw"})
    assert missing.status_code == 401


async def test_require_admin(auth_app, client):
    """require_admin：admin 通过、role=user 403。"""
    from fastapi import Depends

    from app.api.deps import require_admin

    app, settings = auth_app

    @app.get("/api/v1/__test_admin")
    async def _admin(user=Depends(require_admin)) -> dict:
        """临时管理员端点（仅本测试注册）。"""
        return user

    ok = await client.get("/api/v1/__test_admin",
                          headers={"Authorization": "Bearer devtok"})
    assert ok.status_code == 200
    assert ok.json()["role"] == "admin"

    user_token = issue_token({"sub": "u2", "username": "bob", "role": "user"}, settings)
    forbidden = await client.get("/api/v1/__test_admin",
                                 headers={"Authorization": f"Bearer {user_token}"})
    assert forbidden.status_code == 403


async def test_auth_disabled_anonymous(tmp_path):
    """auth_enabled=false 时匿名放行（anon 管理员）。"""
    app = create_app()
    app.state.settings = _settings(auth_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["sub"] == "anon"
    assert r.json()["role"] == "admin"


async def test_login_store_not_ready_503(tmp_path):
    """sqlite 模式 store 未初始化时 login 返回 503。"""
    app = create_app()
    app.state.settings = _settings()
    app.state.store = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/auth/login",
                         json={"username": "a", "password": "b"})
    assert r.status_code == 503
