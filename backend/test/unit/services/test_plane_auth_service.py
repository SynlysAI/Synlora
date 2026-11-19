from __future__ import annotations

import hashlib
import importlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from yuxi.services.plane_auth_service import (
    PlaneAuthError,
    PlaneConfig,
    PlaneProfile,
    get_or_create_plane_user,
    get_plane_deployment_id,
    plane_authenticate,
    plane_claims_from_payload,
    repair_account_link,
    resolve_plane_delegated_user,
    resolve_plane_login_user,
)

service = importlib.import_module("yuxi.services.plane_auth_service")

CONFIG = PlaneConfig(
    plane_base_url="http://plane.test",
    plane_auth_base_url="",
    plane_service_token="svc-secret",
    plane_request_timeout_seconds=3.0,
    plane_default_department="OpenEVA用户",
)

PROFILE = PlaneProfile(plane_user_id="p1", email="a@b.c", display_name="Alice")


def _transport(sign_in_response: httpx.Response) -> httpx.MockTransport:
    """按路径分发：csrf → sign-in（可注入）→ users/me。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/get-csrf-token/":
            return httpx.Response(
                200,
                json={"csrf_token": "csrf-token"},
                headers={"set-cookie": "csrftoken=seed; Path=/"},
            )
        if request.url.path == "/auth/sign-in/":
            return sign_in_response
        if request.url.path == "/api/users/me/":
            return httpx.Response(200, json={"id": "plane-u1", "email": "Alice@Plane.test", "display_name": "Alice"})
        raise AssertionError(f"意外的请求路径: {request.url.path}")

    return httpx.MockTransport(handler)


def _sign_in_success() -> httpx.Response:
    return httpx.Response(
        302,
        headers={
            "location": "http://plane.test/workspace",
            "set-cookie": "session-id=sess; Path=/",
        },
    )


def _sign_in_error(error_code: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": f"http://plane.test/?error_code={error_code}"})


@pytest.mark.asyncio
async def test_authenticate_success_returns_profile_with_lowercased_email():
    profile = await plane_authenticate("alice@plane.test", "pw", CONFIG, _transport(_sign_in_success()))
    assert profile.plane_user_id == "plane-u1"
    assert profile.email == "alice@plane.test"
    assert profile.display_name == "Alice"


@pytest.mark.asyncio
async def test_authenticate_maps_rate_limit_error_to_429():
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", CONFIG, _transport(_sign_in_error("5900")))
    assert exc.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [("5000", 503), ("5018", 403), ("5019", 401), ("5065", 401)],
)
async def test_authenticate_maps_plane_error_codes(code: str, expected: int):
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", CONFIG, _transport(_sign_in_error(code)))
    assert exc.value.status_code == expected


@pytest.mark.asyncio
async def test_authenticate_rejects_redirect_without_session_cookie():
    response = httpx.Response(302, headers={"location": "http://plane.test/workspace"})
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", CONFIG, _transport(response))
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_authenticate_rejects_non_redirect_sign_in():
    response = httpx.Response(200, json={})
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", CONFIG, _transport(response))
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_authenticate_unconfigured_returns_503():
    with pytest.raises(PlaneAuthError) as exc:
        await plane_authenticate("a@b.c", "pw", PlaneConfig(), None)
    assert exc.value.status_code == 503


def test_deployment_id_prefers_configured_value(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNLORA_DEPLOYMENT_ID", "dep-42")
    assert get_plane_deployment_id() == "dep-42"


def test_deployment_id_derives_stable_fallback_from_instance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SYNLORA_DEPLOYMENT_ID", raising=False)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "inst-1")
    first = get_plane_deployment_id()
    second = get_plane_deployment_id()
    assert first == second
    assert first.startswith("yuxi-") and len(first) == len("yuxi-") + 16


def test_deployment_id_dev_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SYNLORA_DEPLOYMENT_ID", raising=False)
    monkeypatch.delenv("YUXI_INSTANCE_ID", raising=False)
    assert get_plane_deployment_id() == "yuxi-dev"


def test_plane_claims_extraction_ignores_non_plane_payload():
    assert plane_claims_from_payload({"sub": "7"}) == {}
    claims = plane_claims_from_payload(
        {
            "sub": "7",
            "auth": "plane",
            "plane_user_id": "p1",
            "email": "x@y.z",
            "plane_delegated": True,
            "other": "ignored",
        }
    )
    assert claims == {
        "auth": "plane",
        "plane_user_id": "p1",
        "email": "x@y.z",
        "plane_delegated": True,
    }


# ---------------------------------------------------------------------------
# 身份收敛 / 补账 / 建号（service 层语义测试）
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDb:
    """按序返回预置查询结果的服务层替身。"""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.committed = 0
        self.refreshed = []

    async def execute(self, _query):
        return FakeResult(self.results.pop(0) if self.results else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def rollback(self):
        pass


class FakeUserRepo:
    def __init__(self, by_id=None, by_username=None):
        self._by_id = by_id
        self._by_username = by_username

    async def get_active_by_id_with_db(self, _db, user_id: int):
        assert isinstance(user_id, int)
        return self._by_id

    async def get_active_by_username_with_db(self, _db, username: str):
        assert username == "alice"
        return self._by_username


class FakeLinkRepo:
    def __init__(self, links=(), existing_by_link=None, by_binding=(), by_synlora=()):
        self.links = list(links)
        self.existing_by_link = existing_by_link
        self.by_binding = list(by_binding)
        self.by_synlora = list(by_synlora)
        self.created = {}

    async def list_for_plane_user(self, plane_user_id: str):
        assert plane_user_id == "p1"
        return self.links

    async def get_by_account_link_id(self, account_link_id: str):
        assert account_link_id == "link-1"
        return self.existing_by_link

    async def list_for_binding(self, *, plane_workspace_id: str, plane_user_id: str):
        return self.by_binding

    async def list_for_synlora_user(self, synlora_user_id: int):
        assert synlora_user_id == 5
        return self.by_synlora

    async def create(self, values):
        self.created.update(values)
        return SimpleNamespace(**values)


def _link(core="ai4ms", **overrides):
    values = {
        "account_link_id": "link-1",
        "plane_workspace_id": "ws-1",
        "plane_user_id": "p1",
        "synlora_user_id": 5,
        "delegated_subject": "plane-ws-1-user-p1",
        "core": core,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(user_id=5, username="alice"):
    return SimpleNamespace(id=user_id, username=username, role="user", uid=f"plane:p{user_id}", last_login=None)


def _patch_repositories(monkeypatch, user_repo, link_repo):
    monkeypatch.setattr(service, "UserRepository", lambda: user_repo)
    monkeypatch.setattr(service, "PlaneAccountLinkRepository", lambda _db: link_repo)


def _repair_kwargs():
    return dict(
        account_link_id="link-1",
        workspace_id="ws-1",
        plane_user_id="p1",
        external_subject="plane-ws-1-user-p1",
        synlora_user_id="5",
        synlora_username="alice",
    )


@pytest.mark.asyncio
async def test_repair_rejects_user_mismatch(monkeypatch):
    db = FakeDb()
    _patch_repositories(monkeypatch, FakeUserRepo(by_username=_user(user_id=9)), FakeLinkRepo())
    with pytest.raises(HTTPException) as exc:
        await repair_account_link(db, **_repair_kwargs())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_repair_rejects_synlora_user_bound_to_other_plane(monkeypatch):
    db = FakeDb()
    repo = FakeLinkRepo(by_synlora=[_link(plane_user_id="p-other", account_link_id="link-2")])
    _patch_repositories(monkeypatch, FakeUserRepo(by_username=_user()), repo)
    with pytest.raises(HTTPException) as exc:
        await repair_account_link(db, **_repair_kwargs())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_repair_rejects_binding_identity_conflict(monkeypatch):
    db = FakeDb()
    existing = _link(delegated_subject="plane-ws-1-user-DIFFERENT")
    repo = FakeLinkRepo(existing_by_link=existing, by_binding=[existing], by_synlora=[existing])
    _patch_repositories(monkeypatch, FakeUserRepo(by_username=_user()), repo)
    with pytest.raises(HTTPException) as exc:
        await repair_account_link(db, **_repair_kwargs())
    assert exc.value.status_code == 409
    assert "冲突" in exc.value.detail


@pytest.mark.asyncio
async def test_repair_same_binding_returns_idempotent_without_write(monkeypatch):
    db = FakeDb()
    existing = _link()
    repo = FakeLinkRepo(existing_by_link=existing, by_binding=[existing], by_synlora=[existing])
    _patch_repositories(monkeypatch, FakeUserRepo(by_username=_user()), repo)
    result = await repair_account_link(db, **_repair_kwargs())
    assert result == {"idempotent": True}
    assert repo.created == {}
    assert db.committed == 0


@pytest.mark.asyncio
async def test_repair_creates_binding_and_commits(monkeypatch):
    db = FakeDb()
    repo = FakeLinkRepo()
    _patch_repositories(monkeypatch, FakeUserRepo(by_username=_user()), repo)
    result = await repair_account_link(db, **_repair_kwargs())
    assert result == {"idempotent": False}
    assert repo.created["synlora_user_id"] == 5
    assert repo.created["plane_user_id"] == "p1"
    assert repo.created["source"] == "repair"
    assert db.committed == 1


@pytest.mark.asyncio
async def test_login_resolution_converges_to_bound_account(monkeypatch):
    db = FakeDb()
    bound = _user()
    _patch_repositories(monkeypatch, FakeUserRepo(by_id=bound), FakeLinkRepo(links=[_link()]))

    async def fail_provision(*_args, **_kwargs):
        raise AssertionError("已收敛到绑定账户时不应建号")

    monkeypatch.setattr(service, "get_or_create_plane_user", fail_provision)
    user, link = await resolve_plane_login_user(db, [_link()], PROFILE, CONFIG)
    assert user is bound
    assert link is not None
    assert bound.last_login is not None
    assert db.committed == 1


@pytest.mark.asyncio
async def test_login_resolution_falls_back_when_bound_user_deleted(monkeypatch):
    db = FakeDb()
    _patch_repositories(monkeypatch, FakeUserRepo(by_id=None), FakeLinkRepo())

    async def fake_provision(_db, _profile, _config):
        return _user(username="plane_p1")

    monkeypatch.setattr(service, "get_or_create_plane_user", fake_provision)
    user, link = await resolve_plane_login_user(db, [_link()], PROFILE, CONFIG)
    assert user.username == "plane_p1"
    assert link is None


@pytest.mark.asyncio
async def test_login_resolution_skips_core_plane_binding(monkeypatch):
    db = FakeDb()
    _patch_repositories(monkeypatch, FakeUserRepo(), FakeLinkRepo())

    async def fake_provision(_db, _profile, _config):
        return _user(username="plane_p1")

    monkeypatch.setattr(service, "get_or_create_plane_user", fake_provision)
    user, link = await resolve_plane_login_user(db, [_link(core="plane")], PROFILE, CONFIG)
    assert user.username == "plane_p1"
    assert link is None


@pytest.mark.asyncio
async def test_delegated_resolution_converges_with_linked_user_id(monkeypatch):
    db = FakeDb()
    bound = _user()
    _patch_repositories(monkeypatch, FakeUserRepo(by_id=bound), FakeLinkRepo(links=[_link()]))
    user, linked_user_id = await resolve_plane_delegated_user(
        db, plane_user_id="p1", display_name="subject", config=CONFIG
    )
    assert user is bound
    assert linked_user_id == "5"
    assert bound.last_login is not None


@pytest.mark.asyncio
async def test_delegated_resolution_provisions_when_unbound(monkeypatch):
    db = FakeDb()
    _patch_repositories(monkeypatch, FakeUserRepo(), FakeLinkRepo(links=[]))

    async def fake_provision(_db, profile, _config):
        assert profile.plane_user_id == "p1"
        assert profile.display_name == "subject"
        return _user(username="plane_p1")

    monkeypatch.setattr(service, "get_or_create_plane_user", fake_provision)
    user, linked_user_id = await resolve_plane_delegated_user(
        db, plane_user_id="p1", display_name="subject", config=CONFIG
    )
    assert user.username == "plane_p1"
    assert linked_user_id == ""


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_user_and_touches_login(monkeypatch):
    existing = _user()
    db = FakeDb(results=[existing])

    async def fail_dept(*_args, **_kwargs):
        raise AssertionError("已存在用户不应再建部门")

    monkeypatch.setattr(service, "_get_or_create_plane_department", fail_dept)
    user = await get_or_create_plane_user(db, PROFILE, CONFIG)
    assert user is existing
    assert existing.last_login is not None
    assert db.committed == 1


@pytest.mark.asyncio
async def test_get_or_create_provisions_new_user(monkeypatch):
    db = FakeDb(results=[None])
    dept = SimpleNamespace(id=3)

    async def fake_dept(_db, _config):
        return dept

    async def fake_username(_db, base_name, plane_user_id):
        assert base_name == "Alice"
        return "Alice-a1b2c3"

    monkeypatch.setattr(service, "_get_or_create_plane_department", fake_dept)
    monkeypatch.setattr(service, "_unique_plane_username", fake_username)
    user = await get_or_create_plane_user(db, PROFILE, CONFIG)
    assert user.uid == "plane:p1"
    assert user.username == "Alice-a1b2c3"
    assert user.role == "user"
    assert user.department_id == 3
    assert db.added == [user]
    assert db.committed == 1


@pytest.mark.asyncio
async def test_unique_plane_username_appends_hash_suffix_on_collision():
    # 首个候选与 digest 后缀均被占用，落到 digest-2
    digest = hashlib.sha256(b"p1").hexdigest()[:6]
    db = FakeDb(results=[1, 1, None])
    username = await service._unique_plane_username(db, "Alice", "p1")
    assert username == f"Alice-{digest}-2"
    assert db.results == []


@pytest.mark.asyncio
async def test_unique_plane_username_falls_back_to_plane_prefix_for_unsafe_name():
    db = FakeDb(results=[None])
    username = await service._unique_plane_username(db, "中文名", "planeuser12345")
    assert username == "plane_planeuser123"


@pytest.mark.asyncio
async def test_unique_plane_username_exhaustion_raises():
    db = FakeDb(results=[1] * 21)
    with pytest.raises(PlaneAuthError) as exc:
        await service._unique_plane_username(db, "Alice", "p1")
    assert exc.value.status_code == 500
