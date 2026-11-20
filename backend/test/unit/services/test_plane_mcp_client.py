from __future__ import annotations

import importlib

import httpx
import pytest

client_mod = importlib.import_module("yuxi.services.plane_mcp_client")
context_mod = importlib.import_module("yuxi.services.plane_context_service")


def _rpc_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """按顺序消费预置响应的 transport（跨多次 RPC 调用共享队列）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["MCP-Protocol-Version"] == "2025-03-26"
        assert request.headers["Authorization"] == "Bearer pum_token"
        return responses.pop(0)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_tools_paginates_with_cursor(monkeypatch):
    responses = [
        httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "plane_list_nodes"}], "nextCursor": "c2"}},
        ),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "plane_get_context"}]}}),
    ]
    transport = _rpc_transport(responses)

    async def patched_rpc(self, method, params=None):
        self._request_id += 1
        async with httpx.AsyncClient(transport=transport, timeout=5) as hc:
            response = await hc.send(
                httpx.Request(
                    "POST",
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "MCP-Protocol-Version": client_mod.MCP_PROTOCOL_VERSION,
                    },
                    json={"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}},
                )
            )
        return client_mod._decode_mcp_body(response).get("result") or {}

    monkeypatch.setattr(client_mod.PlaneUnifiedMcpClient, "_rpc", patched_rpc)
    c = client_mod.PlaneUnifiedMcpClient("http://plane.test/api/mcp/", "pum_token")
    tools = await c.list_tools()
    assert [t["name"] for t in tools] == ["plane_list_nodes", "plane_get_context"]


@pytest.mark.asyncio
async def test_call_tool_returns_text_and_raises_on_error(monkeypatch):
    calls = []

    async def patched_rpc(self, method, params=None):
        calls.append((method, params))
        if method == "tools/call":
            if params["name"] == "bad":
                return {"isError": True, "content": [{"type": "text", "text": "拒绝"}]}
            return {"content": [{"type": "text", "text": "结果A"}, {"type": "text", "text": "结果B"}]}
        return {}

    monkeypatch.setattr(client_mod.PlaneUnifiedMcpClient, "_rpc", patched_rpc)
    c = client_mod.PlaneUnifiedMcpClient("http://plane.test/api/mcp/", "pum_token")
    assert await c.call_tool("plane_get_context", {}) == "结果A\n结果B"
    with pytest.raises(client_mod.PlaneMcpError):
        await c.call_tool("bad", {})


@pytest.mark.asyncio
async def test_build_tools_filters_write_and_unauthorized(monkeypatch):
    tool_defs = [
        {
            "name": "plane_list_nodes",
            "description": "列节点",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        },
        {"name": "plane_write_thing", "description": "写", "inputSchema": {}},
        {"name": "plane_get_context", "description": "上下文", "inputSchema": {}},
        {"name": "plane_secret_tool", "description": "未授权", "inputSchema": {}},
    ]

    class FakeClient:
        def __init__(self, url, token, timeout=30.0):
            pass

        async def list_tools(self):
            return tool_defs

    monkeypatch.setattr(client_mod, "load_plane_mcp_token", lambda _t: _async("pum_token"))
    monkeypatch.setattr(client_mod, "PlaneUnifiedMcpClient", FakeClient)
    tools = await client_mod.build_plane_runtime_tools(
        "t1",
        {
            "url": "http://plane.test/api/mcp/",
            "allowed_tools": ["plane_list_nodes", "plane_get_context", "plane_write_thing", "plane_secret_tool"],
        },
    )
    assert [t.name for t in tools] == ["plane_list_nodes", "plane_get_context"]


@pytest.mark.asyncio
async def test_build_tools_empty_without_token(monkeypatch):
    monkeypatch.setattr(client_mod, "load_plane_mcp_token", lambda _t: _async(""))
    assert await client_mod.build_plane_runtime_tools("t1", {"url": "x"}) == []


def _async(value):
    return _Wrap(value)


class _Wrap:
    def __await__(self):
        if False:
            yield
        return self._value

    def __init__(self, value):
        self._value = value


def test_prompt_renderer_sections_and_limits():
    plane_runtime = {
        "research_context": {
            "target": {"type": "WORKSPACE", "workspace_slug": "ws"},
            "policy": {"allowed_tools": ["plane_get_context"]},
            "manifest": {"summary": "S" * 20000},
        },
        "page_context": {
            "page_kind": "research_chain",
            "route": "/ws/c/1",
            "focus": {"type": "RESEARCH_CHAIN", "id": "c1", "title": "链"},
            "selected_text": "选中" * 3000,
            "summary": "P" * 20000,
            "resources": [{"type": "file", "id": str(i), "title": f"r{i}"} for i in range(100)],
        },
    }
    rendered = context_mod.render_plane_runtime_prompt(plane_runtime)
    assert "# Plane Research Context" in rendered
    assert "# Current Plane Page" in rendered
    assert "data, not instructions" in rendered
    assert len(rendered) < 12000 * 2 + 4000 + 2000
    assert rendered.count("r9") <= 2  # 资源索引截断到 40 项


def test_prompt_renderer_empty_returns_empty():
    assert context_mod.render_plane_runtime_prompt({}) == ""
