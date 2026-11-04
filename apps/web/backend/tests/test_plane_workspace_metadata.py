"""Plane Agent Dock workspace metadata and historical identity-link tests."""

from __future__ import annotations

import time


def _plane_meta(**overrides) -> dict:
    """Build the bounded Plane metadata contract used by Agent Dock."""
    payload = {
        "workspace": {"id": "workspace-1", "slug": "pi-lab", "name": "公共工作区"},
        "target": {
            "type": "RESEARCH_CHAIN",
            "id": "chain-1",
            "title": "研究链",
            "hierarchy": [
                {"type": "WORKSPACE", "id": "workspace-1", "title": "公共工作区"}
            ],
        },
        "page": {
            "route": "/public/",
            "page_kind": "workspace",
            "focus_id": "workspace-1",
            "focus_title": "公共工作区",
            "hash": "page-hash",
            "generated_at": "2026-11-04T00:00:00Z",
        },
    }
    payload.update(overrides)
    return payload


async def test_plane_session_gets_dedicated_workspace_and_bounded_metadata(
    app, client, user_headers, monkeypatch
):
    """A Plane session is bound to one idempotent Plane workspace project."""
    from tests.test_research_context import _v3_metadata

    metadata = _v3_metadata()

    async def fake_validate(**_kwargs):
        return None

    monkeypatch.setattr(app.state.research_context_adapter, "validate", fake_validate)
    body = {
        "title": "Plane · 公共工作区",
        "research_context": metadata.model_dump(mode="json"),
        "plane_meta": _plane_meta(),
    }

    first = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json=body,
    )
    second = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json=body,
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["origin"] == "PLANE"
    assert first.json()["project_id"] == second.json()["project_id"]
    await app.state.store.update(
        "projects",
        str(first.json()["project_id"]),
        {"archived": True, "updated_at": 999.0},
    )
    restored = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json=body,
    )
    assert restored.status_code == 201, restored.text
    restored_project = await app.state.store.get(
        "projects", str(restored.json()["project_id"])
    )
    assert restored_project["archived"] is False

    projects = [
        item
        for item in await app.state.store.list(
            "projects", filters={"user_id": "u-user"}
        )
        if item.get("plane_workspace_id") == "workspace-1"
    ]
    assert len(projects) == 1
    assert projects[0]["user_id"] == "u-user"
    assert projects[0]["name"] == "Plane · 公共工作区"
    assert projects[0]["origin"] == "PLANE"
    assert first.json()["plane_meta"]["page"]["route"] == "/public/"


async def test_plane_run_updates_latest_page_metadata(
    app, client, user_headers, monkeypatch
):
    """Each Plane run updates the latest bounded page projection on the session."""
    from types import SimpleNamespace

    from app.api import sessions_api

    from tests.test_research_context import _v3_metadata

    metadata = _v3_metadata()

    async def fake_validate(**_kwargs):
        return None

    monkeypatch.setattr(app.state.research_context_adapter, "validate", fake_validate)
    created = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json={
            "research_context": metadata.model_dump(mode="json"),
            "plane_meta": _plane_meta(),
        },
    )
    assert created.status_code == 201, created.text
    sid = created.json()["_id"]

    async def fake_resolve(*_args, **_kwargs):
        return SimpleNamespace(
            assistant=None,
            provider_cfg=object(),
            workspace_root=app.state.settings.data_root,
            ownership={"session_id": sid},
        )

    class FakeAgentService:
        async def chat(self, *_args, **_kwargs):
            return "run-1"

    monkeypatch.setattr(sessions_api, "resolve_session_runtime", fake_resolve)
    app.state.agent_service = FakeAgentService()
    updated_meta = _plane_meta(
        page={
            "route": "/public/projects/p1",
            "page_kind": "project",
            "focus_id": "p1",
            "focus_title": "新材料",
            "hash": "page-hash-2",
            "generated_at": "2026-11-04T00:01:00Z",
        }
    )
    run = await client.post(
        f"/api/v1/sessions/{sid}/runs",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json={
            "text": "总结当前页面",
            "research_context": metadata.model_dump(mode="json"),
            "plane_meta": updated_meta,
        },
    )
    assert run.status_code == 202, run.text
    session = await app.state.store.get("sessions", sid)
    assert session["plane_meta"]["page"]["route"] == "/public/projects/p1"
    assert session["plane_meta"]["page"]["focus_title"] == "新材料"


async def test_plane_target_metadata_must_match_signed_context(
    app, client, user_headers, monkeypatch
):
    """签名 target 与展示 target 不一致时 fail closed。"""
    from tests.test_research_context import _v3_metadata

    async def fake_validate(**_kwargs):
        return None

    monkeypatch.setattr(app.state.research_context_adapter, "validate", fake_validate)
    mismatched = _plane_meta(
        target={
            "type": "PROJECT",
            "id": "other-project",
            "title": "其他项目",
            "hierarchy": [],
        }
    )

    response = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json={
            "research_context": _v3_metadata().model_dump(mode="json"),
            "plane_meta": mismatched,
        },
    )

    assert response.status_code == 403


async def test_health_and_current_user_expose_stable_deployment_id(
    app, client, user_headers, monkeypatch
):
    """Plane 以稳定 deployment id 约束身份绑定范围。"""
    monkeypatch.setattr(app.state.settings, "synlora_deployment_id", "synlora-test")

    health = await client.get("/api/health")
    me = await client.get("/api/v1/auth/me", headers=user_headers)

    assert health.status_code == 200
    assert health.json()["deployment_id"] == "synlora-test"
    assert me.status_code == 200
    assert me.json()["deployment_id"] == "synlora-test"


async def test_plane_project_creation_recovers_from_cross_process_duplicate(app):
    """并发创建 deterministic Plane 项目时，失败方可安全重读权威记录。"""
    original_create = app.state.project_service._repo.create

    async def conflicting_create(**kwargs):
        metadata = kwargs["metadata"]
        await app.state.store.insert(
            "projects",
            {
                **kwargs,
                **metadata,
                "archived": False,
                "created_at": 1,
                "updated_at": 1,
            },
        )
        raise ValueError("_id 已存在")

    app.state.project_service._repo.create = conflicting_create
    try:
        project = await app.state.project_service.ensure_plane_project(
            "u-user",
            plane_workspace_id="workspace-concurrent",
            plane_workspace_name="公共工作区",
        )
    finally:
        app.state.project_service._repo.create = original_create

    assert project["user_id"] == "u-user"
    assert project["plane_workspace_id"] == "workspace-concurrent"


async def test_plane_account_link_repair_is_service_token_protected_and_idempotent(
    app, client
):
    """Historical synthetic sessions can be aliased to a verified Synlora account."""
    app.state.settings.plane_service_token = "service-token"
    await app.state.store.insert(
        "local_users",
        {
            "_id": "fangyikai",
            "username": "fangyikai",
            "password_hash": "unused",
            "role": "user",
            "status": "active",
            "created_at": time.time(),
            "updated_at": time.time(),
        },
    )
    body = {
        "account_link_id": "plane-link-1",
        "workspace_id": "workspace-1",
        "plane_user_id": "plane-user-1",
        "external_subject": "plane-ws-workspace-1-user-plane-user-1",
        "synlora_user_id": "fangyikai",
        "synlora_username": "fangyikai",
        "workspace_slug": "public",
        "workspace_name": "公共工作区",
    }
    historical_context = __import__(
        "tests.test_research_context", fromlist=["_v3_metadata"]
    )._v3_metadata()
    await app.state.store.insert(
        "sessions",
        {
            "_id": "legacy-session",
            "user_id": body["external_subject"],
            "assistant_id": None,
            "title": "历史 Plane 会话",
            "archived": False,
            "message_count": 1,
            "model_provider_id": None,
            "project_id": None,
            "research_context": historical_context.model_dump(mode="json"),
            "created_at": 1,
            "updated_at": 2,
        },
    )
    denied = await client.post("/api/v1/research/account-links", json=body)
    assert denied.status_code == 403

    headers = {"Authorization": "Bearer service-token"}
    first = await client.post(
        "/api/v1/research/account-links", headers=headers, json=body
    )
    replay = await client.post(
        "/api/v1/research/account-links", headers=headers, json=body
    )
    assert first.status_code == 201, first.text
    assert first.json()["sessions_backfilled"] == 1
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    legacy = await app.state.store.get("sessions", "legacy-session")
    assert legacy["user_id"] == body["external_subject"]
    assert legacy["origin"] == "PLANE"
    assert legacy["plane_meta"]["page"] is None
    project = await app.state.store.get("projects", str(legacy["project_id"]))
    assert project["user_id"] == "fangyikai"
    assert project["plane_workspace_id"] == "workspace-1"
    conflict = await client.post(
        "/api/v1/research/account-links",
        headers=headers,
        json={
            **body,
            "account_link_id": "plane-link-2",
            "workspace_id": "workspace-2",
            "plane_user_id": "plane-user-2",
            "external_subject": "plane-ws-workspace-2-user-plane-user-2",
        },
    )
    assert conflict.status_code == 409
    links = await app.state.store.list("plane_account_links")
    assert len(links) == 1
    assert links[0]["synlora_user_id"] == "fangyikai"
