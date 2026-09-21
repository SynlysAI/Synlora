"""MCP 服务核心测试。"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.mcp_service import McpService


@pytest.mark.asyncio
async def test_mcp_config_hides_secret_and_resolves_for_runtime(store):
    """列表不回传 Token 明文，运行时仍能解密得到完整配置。"""
    service = McpService(store, "")
    saved = await service.create(
        "u1",
        {
            "id": "lab-db",
            "name": "实验数据库",
            "description": "查询实验数据",
            "url": "https://mcp.example.test/api",
            "headers": {"X-Tenant": "demo"},
            "bearer_token": "secret-token",
            "enabled": True,
        },
    )

    assert saved["bearer_token_set"] is True
    assert "bearer_token" not in saved
    resolved = await service.resolved("u1", "lab-db")
    assert resolved["bearer_token"] == "secret-token"
    assert resolved["headers"] == {"X-Tenant": "demo"}


@pytest.mark.asyncio
async def test_mcp_discover_and_call_tool(store):
    """HTTP MCP 按 initialize → tools/list → tools/call 完成发现与调用。"""
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        method = payload["method"]
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "mock", "version": "1.0"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [{
                    "name": "search-records",
                    "description": "搜索记录",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }]},
            })
        return httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"content": [{"type": "text", "text": "命中 1 条"}]},
        })

    service = McpService(
        store,
        "",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=10),
    )
    await service.create("u1", {
        "id": "lab-db", "name": "实验数据库", "url": "https://mcp.test/api",
        "description": "", "headers": {}, "bearer_token": "", "enabled": True,
    })

    tools = await service.discover_tools("u1", "lab-db", persist=True)
    result = await service.call_tool(
        "u1", "lab-db", "search-records", {"query": "NMR"})

    assert tools[0]["name"] == "search-records"
    assert result["content"][0]["text"] == "命中 1 条"
    assert [request["method"] for request in requests] == ["tools/list", "tools/call"]
    assert requests[0]["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["name"] == "Synlora"
