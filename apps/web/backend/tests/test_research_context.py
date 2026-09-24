"""Plane Research Context adapter and session metadata tests."""
from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from app.services.research_context import (
    PlaneResearchClient,
    ResearchContextAdapter,
    ResearchContextError,
    ResearchContextMetadata,
)


def _metadata(**overrides) -> ResearchContextMetadata:
    payload = {
        "workspace_id": "workspace-1",
        "workspace_slug": "pi-lab",
        "research_project_id": "project-1",
        "chain_node_id": "node-1",
        "context_id": "context-1",
        "context_hash": "hash-1",
        "visibility_scope": "PRIVATE",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "allowed_knowledge_base_ids": ["kb-1"],
        "allowed_file_ids": ["file-1"],
        "allowed_plugins": [],
        "allowed_tools": ["knowledge.search", "file.read"],
        "policy_id": "policy-1",
        "policy_hash": "policy-hash",
    }
    payload.update(overrides)
    return ResearchContextMetadata.model_validate(payload)


def _plane_response(metadata: ResearchContextMetadata) -> dict:
    return {
        "workspace": {"id": metadata.workspace_id},
        "scope": {"project_id": metadata.research_project_id},
        "context": {
            "context_id": metadata.context_id,
            "context_hash": metadata.context_hash,
            "chain_node_id": metadata.chain_node_id,
            "visibility_scope": metadata.visibility_scope,
            "expires_at": metadata.expires_at.isoformat(),
            "allowed_knowledge_base_ids": metadata.allowed_knowledge_base_ids,
            "allowed_file_ids": metadata.allowed_file_ids,
            "allowed_plugins": metadata.allowed_plugins,
            "allowed_tools": metadata.allowed_tools,
            "policy_id": metadata.policy_id,
            "policy_hash": metadata.policy_hash,
        },
    }


async def test_context_adapter_validates_scope_without_widening():
    """Plane response 必须与请求 metadata 六个 scope 字段全部一致。"""
    metadata = _metadata()
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["token"] = request.headers.get("X-Research-Context-Token")
        captured["hash"] = request.headers.get("X-Research-Context-Hash")
        return httpx.Response(200, json=_plane_response(metadata))

    adapter = ResearchContextAdapter("http://plane.test")
    await adapter.validate(token="opaque", expected=metadata, transport=httpx.MockTransport(handler))
    assert captured == {"token": "opaque", "hash": "hash-1"}


async def test_context_adapter_rejects_scope_mismatch_and_expiry():
    """跨课题、跨节点与过期 token 均 fail closed。"""
    metadata = _metadata()
    mismatch = _plane_response(metadata)
    mismatch["context"]["chain_node_id"] = "other-node"
    adapter = ResearchContextAdapter("http://plane.test")
    with pytest.raises(ResearchContextError) as error:
        await adapter.validate(
            token="opaque",
            expected=metadata,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=mismatch)),
        )
    assert error.value.status_code == 403

    expired = _metadata(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    expired_response = _plane_response(expired)
    with pytest.raises(ResearchContextError) as error:
        await adapter.validate(
            token="opaque",
            expected=expired,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=expired_response)),
        )
    assert error.value.status_code == 401


async def test_plane_event_client_uses_backend_token_and_idempotency():
    """事件回写只经后端 token，并携带稳定 request/event id。"""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers.get("X-API-Key")
        captured["authorization"] = request.headers.get("Authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={"event_id": "event-1"})

    client = PlaneResearchClient("http://plane.test", "service-token")
    result = await client.write_chain_event(
        workspace_slug="pi-lab",
        chain_node_id="node-1",
        event_type="AI_ACTION",
        summary="safe summary",
        request_id="request-1",
        event_id="event-1",
        transport=httpx.MockTransport(handler),
    )
    assert result == {"event_id": "event-1"}
    assert captured["api_key"] == "service-token"
    assert captured["authorization"] is None
    assert captured["json"]["request_id"] == "request-1"


async def test_session_stores_research_metadata_without_token(app, client, user_headers, monkeypatch):
    """科研会话保存 scope metadata，但不保存 opaque context token。"""
    metadata = _metadata()
    adapter = app.state.research_context_adapter

    async def fake_validate(*, token, expected):
        assert token == "opaque-token"
        assert expected.context_id == metadata.context_id

    monkeypatch.setattr(adapter, "validate", fake_validate)
    response = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque-token"},
        json={"research_context": metadata.model_dump(mode="json")},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["research_context"]["context_id"] == metadata.context_id
    assert "opaque-token" not in response.text

    stored = await app.state.session_repo.get(created["_id"])
    assert stored["research_context"]["chain_node_id"] == "node-1"
    assert "token" not in stored["research_context"]


async def test_research_session_requires_context_token(app, client, user_headers, monkeypatch):
    """缺 token 时先拒绝，不调用 Plane 也不落库。"""
    called = False

    async def fake_validate(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(app.state.research_context_adapter, "validate", fake_validate)
    response = await client.post(
        "/api/v1/sessions",
        headers=user_headers,
        json={"research_context": _metadata().model_dump(mode="json")},
    )
    assert response.status_code == 401
    assert called is False
