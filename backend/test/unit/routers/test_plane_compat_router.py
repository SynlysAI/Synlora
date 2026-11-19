from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.services.plane_auth_service import PlaneConfig, PlaneProfile

router = importlib.import_module("server.routers.plane_compat_router")

SERVICE_HEADER = {"Authorization": "Bearer svc-secret"}


class FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class FakeDb:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class FakeLinkRepo:
    def __init__(self, _db):
        pass

    async def list_for_plane_user(self, plane_user_id: str):
        raise AssertionError(f"不应查询绑定: {plane_user_id}")


def _service_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(router, "plane_config", PlaneConfig(plane_service_token="svc-secret"))


def _user(user_id: int = 5, username: str = "alice"):
    return SimpleNamespace(id=user_id, username=username, role="user")


@pytest.mark.asyncio
async def test_health_returns_deployment_id_and_version():
    result = await router.plane_compat_health()
    assert result["status"] == "ok"
    assert result["deployment_id"]
    assert result["version"]


@pytest.mark.asyncio
async def test_me_rejects_missing_user():
    with pytest.raises(HTTPException) as exc:
        await router.plane_compat_me(authorization=None, user=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_sub_username_and_deployment_id_for_api_key():
    result = await router.plane_compat_me(authorization="Bearer yxkey_xxx", user=_user())
    assert result["sub"] == "5"
    assert result["username"] == "alice"
    assert result["role"] == "user"
    assert result["deployment_id"]
    assert "auth" not in result


@pytest.mark.asyncio
async def test_me_passes_through_plane_claims_for_jwt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        router.AuthUtils,
        "decode_token",
        lambda _token: {"auth": "plane", "plane_user_id": "p1", "email": "x@y.z"},
    )
    result = await router.plane_compat_me(authorization="Bearer jwt-token", user=_user())
    assert result["plane_user_id"] == "p1"
    assert result["email"] == "x@y.z"
    assert result["auth"] == "plane"


@pytest.mark.asyncio
async def test_plane_login_rejects_malformed_email():
    with pytest.raises(HTTPException) as exc:
        await router.plane_compat_login(router.PlaneLoginBody(email="nope", password="pw"), db=FakeDb())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_plane_login_propagates_auth_error(monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.plane_auth_service import PlaneAuthError

    async def fail_auth(_email, _password, _config):
        raise PlaneAuthError(429, "尝试过于频繁")

    monkeypatch.setattr(router, "plane_authenticate", fail_auth)
    with pytest.raises(HTTPException) as exc:
        await router.plane_compat_login(router.PlaneLoginBody(email="a@b.c", password="pw"), db=FakeDb())
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_plane_login_propagates_provisioning_error(monkeypatch: pytest.MonkeyPatch):
    """身份解析（含建号）抛出的 PlaneAuthError 也必须映射为带文案的 HTTP 错误。"""
    from yuxi.services.plane_auth_service import PlaneAuthError

    async def fail_resolve(*_args, **_kwargs):
        raise PlaneAuthError(502, "OpenEVA 账号登录失败，请重试")

    async def fake_auth(_email, _password, _config):
        return PlaneProfile(plane_user_id="p1", email="a@b.c", display_name="Alice")

    class EmptyRepo(FakeLinkRepo):
        async def list_for_plane_user(self, plane_user_id: str):
            return []

    monkeypatch.setattr(router, "plane_authenticate", fake_auth)
    monkeypatch.setattr(router, "resolve_plane_login_user", fail_resolve)
    monkeypatch.setattr(router, "PlaneAccountLinkRepository", EmptyRepo)
    with pytest.raises(HTTPException) as exc:
        await router.plane_compat_login(router.PlaneLoginBody(email="a@b.c", password="pw"), db=FakeDb())
    assert exc.value.status_code == 502
    assert "请重试" in exc.value.detail


@pytest.mark.asyncio
async def test_plane_login_resolves_identity_and_issues_token(monkeypatch: pytest.MonkeyPatch):
    profile = PlaneProfile(plane_user_id="p1", email="a@b.c", display_name="Alice")

    async def fake_auth(_email, _password, _config):
        return profile

    async def fake_resolve(_db, _links, _profile, _config):
        return _user(username="plane_alice"), None

    class EmptyRepo(FakeLinkRepo):
        async def list_for_plane_user(self, plane_user_id: str):
            return []

    monkeypatch.setattr(router, "plane_authenticate", fake_auth)
    monkeypatch.setattr(router, "resolve_plane_login_user", fake_resolve)
    monkeypatch.setattr(router, "PlaneAccountLinkRepository", EmptyRepo)
    result = await router.plane_compat_login(router.PlaneLoginBody(email="a@b.c", password="pw"), db=FakeDb())
    assert result["username"] == "plane_alice"
    assert result["role"] == "user"
    assert result["token"]


def _delegated_body() -> router.DelegatedTokenBody:
    return router.DelegatedTokenBody(
        schema_version="plane-delegated-auth.v1",
        account_link_id="link-1",
        workspace_id="ws-1",
        plane_user_id="p1",
        external_subject="plane-ws-1-user-p1",
    )


def test_delegated_body_rejects_unknown_schema_version():
    values = _delegated_body().model_dump()
    values["schema_version"] = "plane-delegated-auth.v2"
    with pytest.raises(ValidationError):
        router.DelegatedTokenBody(**values)


@pytest.mark.asyncio
async def test_delegated_token_rejects_missing_service_credential(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await router.plane_delegated_token(
            _delegated_body(), request=FakeRequest({"Authorization": "Bearer wrong"}), db=FakeDb()
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delegated_token_rejects_non_ascii_bearer_without_500(monkeypatch: pytest.MonkeyPatch):
    """非 ASCII 的 Bearer 值必须被字节级比较拒绝（403）而不是抛 TypeError 成 500。"""
    _service_config(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await router.plane_delegated_token(
            _delegated_body(), request=FakeRequest({"Authorization": "Bearer café"}), db=FakeDb()
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delegated_token_converges_to_bound_account(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)

    async def fake_resolve(_db, *, plane_user_id, display_name, config):
        assert plane_user_id == "p1"
        assert display_name == "plane-ws-1-user-p1"
        return _user(), "5"

    monkeypatch.setattr(router, "resolve_plane_delegated_user", fake_resolve)
    result = await router.plane_delegated_token(_delegated_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert result["linked_user_id"] == "5"
    assert result["subject"] == "plane-ws-1-user-p1"
    assert result["schema_version"] == "plane-delegated-auth.v1"
    assert result["expires_in"] == 3600
    assert result["token"]


@pytest.mark.asyncio
async def test_delegated_token_provisions_plane_identity_when_unbound(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)

    async def fake_resolve(_db, *, plane_user_id, display_name, config):
        assert display_name == "plane-ws-1-user-p1"
        return _user(username="plane_p1"), ""

    monkeypatch.setattr(router, "resolve_plane_delegated_user", fake_resolve)
    result = await router.plane_delegated_token(_delegated_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert result["linked_user_id"] == ""
    assert result["account_link_id"] == "link-1"
    assert result["token"]


@pytest.mark.asyncio
async def test_delegated_token_propagates_provisioning_error(monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.plane_auth_service import PlaneAuthError

    _service_config(monkeypatch)

    async def fail_resolve(*_args, **_kwargs):
        raise PlaneAuthError(502, "OpenEVA 账号登录失败，请重试")

    monkeypatch.setattr(router, "resolve_plane_delegated_user", fail_resolve)
    with pytest.raises(HTTPException) as exc:
        await router.plane_delegated_token(_delegated_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert exc.value.status_code == 502


def _repair_body(**overrides) -> router.AccountLinkRepairBody:
    values = {
        "schema_version": "plane-account-link-repair.v1",
        "account_link_id": "link-1",
        "workspace_id": "ws-1",
        "plane_user_id": "p1",
        "external_subject": "plane-ws-1-user-p1",
        "synlora_user_id": "5",
        "synlora_username": "alice",
        "workspace_slug": "ws-slug",
        "workspace_name": "工作区",
    }
    values.update(overrides)
    return router.AccountLinkRepairBody(**values)


def test_repair_body_rejects_unknown_schema_version():
    values = _repair_body().model_dump()
    values["schema_version"] = "plane-account-link-repair.v2"
    with pytest.raises(ValidationError):
        router.AccountLinkRepairBody(**values)


def test_repair_body_defaults_schema_version():
    values = _repair_body().model_dump()
    values.pop("schema_version")
    assert router.AccountLinkRepairBody(**values).schema_version == "plane-account-link-repair.v1"


@pytest.mark.asyncio
async def test_repair_rejects_missing_service_credential(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await router.plane_repair_account_link(_repair_body(), request=FakeRequest({"Authorization": ""}), db=FakeDb())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_repair_propagates_user_validation_403(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)

    async def fail_repair(*_args, **_kwargs):
        raise HTTPException(403, "Synlora 用户校验失败")

    monkeypatch.setattr(router, "repair_account_link", fail_repair)
    with pytest.raises(HTTPException) as exc:
        await router.plane_repair_account_link(_repair_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_repair_returns_idempotent_200_for_same_binding(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)

    async def fake_repair(*_args, **_kwargs):
        return {"idempotent": True}

    monkeypatch.setattr(router, "repair_account_link", fake_repair)
    response = await router.plane_repair_account_link(_repair_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["idempotent"] is True
    assert payload["account_link_id"] == "link-1"
    assert payload["sessions_backfilled"] == 0


@pytest.mark.asyncio
async def test_repair_creates_binding_with_201(monkeypatch: pytest.MonkeyPatch):
    _service_config(monkeypatch)

    async def fake_repair(*_args, **_kwargs):
        return {"idempotent": False}

    monkeypatch.setattr(router, "repair_account_link", fake_repair)
    result = await router.plane_repair_account_link(_repair_body(), request=FakeRequest(SERVICE_HEADER), db=FakeDb())
    assert result["idempotent"] is False
    assert result["schema_version"] == "plane-account-link-repair.v1"
    assert result["sessions_backfilled"] == 0
