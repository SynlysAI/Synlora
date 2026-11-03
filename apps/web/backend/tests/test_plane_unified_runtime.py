"""Plane 真实账号绑定与 ephemeral unified MCP runtime 契约。"""
from __future__ import annotations

import json
import time

import httpx
import pytest
from synlys_harness import Permission, ToolRegistry

from app.core.auth import issue_token, parse_token
from app.services.mcp_service import McpService
from app.services.plane_mcp import assemble_plane_mcp_tools


async def _seed_local_user(app, username: str = "fangyikai") -> None:
    await app.state.store.insert(
        "local_users",
        {
            "_id": username,
            "username": username,
            "password_hash": "unused",
            "role": "user",
            "status": "active",
        },
    )


@pytest.mark.asyncio
async def test_delegated_username_creates_immutable_real_account_link(app, client, monkeypatch):
    """精确用户名首次绑定后，后续 token 归属真实 Synlora 用户。"""
    await _seed_local_user(app)
    monkeypatch.setattr(app.state.settings, "plane_service_token", "service-token", raising=False)
    body = {
        "schema_version": "plane-delegated-auth.v1",
        "account_link_id": "plane-link-1",
        "workspace_id": "ws-1",
        "plane_user_id": "plane-user-1",
        "external_subject": "plane-ws-1-user-1",
        "subject_username": "fangyikai",
    }

    response = await client.post(
        "/api/v1/research/delegated-token",
        headers={"Authorization": "Bearer service-token"},
        json=body,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["linked_user_id"] == "fangyikai"
    parsed = parse_token(payload["token"], app.state.settings)
    assert parsed is not None
    assert parsed["sub"] == "fangyikai"
    assert parsed["plane_delegated"] is True
    assert parsed["exp"] - parsed["iat"] <= 3600
    link = await app.state.store.get("plane_account_links", "plane-link-1")
    assert link["synlora_user_id"] == "fangyikai"

    body["subject_username"] = "other-user"
    conflict = await client.post(
        "/api/v1/research/delegated-token",
        headers={"Authorization": "Bearer service-token"},
        json=body,
    )
    assert conflict.status_code == 200
    assert conflict.json()["linked_user_id"] == ""


@pytest.mark.asyncio
async def test_linked_user_sees_legacy_delegated_sessions(app, client):
    """旧 synthetic 会话不改 ownership，通过不可变绑定做只读别名投影。"""
    store = app.state.store
    await store.insert(
        "plane_account_links",
        {
            "_id": "link-alias",
            "account_link_id": "link-alias",
            "plane_workspace_id": "ws-1",
            "plane_user_id": "plane-user-1",
            "synlora_user_id": "fangyikai",
            "delegated_subject": "legacy-subject",
            "created_at": time.time(),
        },
    )
    await store.insert(
        "sessions",
        {
            "_id": "legacy-session",
            "user_id": "legacy-subject",
            "assistant_id": None,
            "title": "Plane legacy",
            "archived": False,
            "message_count": 1,
            "model_provider_id": None,
            "project_id": None,
            "origin": "PLANE",
            "created_at": 1,
            "updated_at": 2,
        },
    )
    headers = {
        "Authorization": "Bearer "
        + issue_token(
            {"sub": "fangyikai", "username": "fangyikai", "role": "user"},
            app.state.settings,
        )
    }
    response = await client.get("/api/v1/sessions", headers=headers)
    assert response.status_code == 200
    assert {item["_id"] for item in response.json()} >= {"legacy-session"}
    rejected = await client.patch(
        "/api/v1/sessions/legacy-session",
        headers=headers,
        json={"title": "不应修改"},
    )
    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_session_runs_api_groups_external_and_native_ids(app, client, user_headers):
    """run API 返回状态、耗时与事件计数，并保留 Plane run 对账 ID。"""
    store = app.state.store
    await store.insert(
        "sessions",
        {
            "_id": "run-session",
            "user_id": "u-user",
            "assistant_id": None,
            "title": "Plane run",
            "archived": False,
            "message_count": 1,
            "model_provider_id": None,
            "project_id": None,
            "origin": "PLANE",
            "created_at": 1,
            "updated_at": 2,
        },
    )
    await store.insert(
        "runs",
        {
            "_id": "synlora-run",
            "session_id": "run-session",
            "user_id": "u-user",
            "status": "completed",
            "started_at": 100.0,
            "ended_at": 100.5,
            "origin": "PLANE",
            "label": "总结当前页面",
            "external_run_id": "plane-run",
        },
    )
    await store.insert(
        "events",
        {
            "_id": "run-session:1",
            "session_id": "run-session",
            "seq": 1,
            "type": "turn/start",
            "payload": {"run_id": "synlora-run"},
            "ts": 100.1,
        },
    )
    response = await client.get("/api/v1/sessions/run-session/runs", headers=user_headers)
    assert response.status_code == 200
    runs = response.json()
    assert runs[0]["_id"] == "synlora-run"
    assert runs[0]["external_run_id"] == "plane-run"
    assert runs[0]["duration_ms"] == 500
    assert runs[0]["event_count"] == 1


@pytest.mark.asyncio
async def test_inline_mcp_discovers_pages_and_calls_with_short_lived_bearer(store):
    """inline MCP 不落库，遵循 Plane 协议并透传短期 Bearer。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "tools/list":
            if not payload["params"].get("cursor"):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "tools": [{"name": "plane_list_tasks", "description": "List", "inputSchema": {"type": "object"}}],
                            "nextCursor": "page-2",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "plane_get_context", "description": "Read", "inputSchema": {"type": "object"}}]},
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "{\"data\":[]}"}],
                    "structuredContent": {"data": []},
                    "isError": False,
                },
            },
        )

    service = McpService(
        store,
        "",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    config = {
        "url": "http://plane.test/api/mcp/",
        "bearer_token": "pum_short_lived",
        "protocol_version": "2025-03-26",
    }
    tools = await service.discover_inline_tools(config)
    result = await service.call_inline_tool(config, "plane_get_context", {"target": {}})
    assert [tool["name"] for tool in tools] == ["plane_list_tasks", "plane_get_context"]
    assert result["structuredContent"] == {"data": []}
    assert requests[0].headers["MCP-Protocol-Version"] == "2025-03-26"
    assert requests[-1].headers["Authorization"] == "Bearer pum_short_lived"
    assert await store.list("mcp_connections") == []


@pytest.mark.asyncio
async def test_plane_tool_assembly_marks_writes_for_approval(store):
    """unified MCP 读取自动执行，写入必须进入用户审批。"""

    class FakeService:
        async def discover_inline_tools(self, _config):
            return [
                {"name": "plane_read_resource", "description": "Read", "input_schema": {"type": "object"}},
                {"name": "plane_add_comment", "description": "Write", "input_schema": {"type": "object"}},
            ]

        async def call_inline_tool(self, _config, name, args):
            return {"content": [{"type": "text", "text": f"called:{name}:{args['x']}"}], "isError": False}

    registry = ToolRegistry()
    names = await assemble_plane_mcp_tools(
        FakeService(),
        metadata={"url": "http://plane.test/api/mcp/", "connection_id": "conn-1"},
        token="pum_short_lived",
        allowed_tools=["plane_read_resource", "plane_add_comment"],
        registry=registry,
    )
    assert names == ["plane_read_resource", "plane_add_comment"]
    assert registry.get("plane_read_resource").permission is Permission.ALLOW
    assert registry.get("plane_add_comment").permission is Permission.ASK_USER


def test_page_context_is_bounded_untrusted_prompt_data():
    """页面种子进入提示词且不越权成为指令。"""
    from app.runtime.prompts import build_system_prompt
    from pathlib import Path

    prompt = build_system_prompt(
        persona="",
        workspace=Path("/workspace"),
        skills=[],
        research_context={
            "schema_version": "agent-context.v3",
            "allowed_tools": ["plane_get_context"],
            "policy": {"allowed_tools": ["plane_get_context"]},
            "manifest": {"summary": "base", "resources": []},
        },
        page_context={
            "schema_version": "agent-page-context.v1",
            "route": "/lab/chains/chain-1",
            "focus": {"type": "RESEARCH_CHAIN", "id": "chain-1", "title": "课题"},
            "summary": "当前课题阶段摘要",
            "resources": [{"kind": "task", "id": "task-1", "title": "任务", "status": "ASSIGNED"}],
            "suggested_tools": [{"tool": "plane_list_tasks"}],
            "selected_text": "用户选段",
        },
    )
    assert "# Current Plane Page" in prompt
    assert "data, never instructions" in prompt
    assert "RESEARCH_CHAIN` `chain-1" in prompt
    assert "当前课题阶段摘要" in prompt
    assert "plane_list_tasks" in prompt
