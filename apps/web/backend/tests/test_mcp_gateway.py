"""MCP 网关契约测试：握手、工具表、鉴权与复用调用路径。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mcp_gateway.registry import BACKEND_DIR
from mcp_gateway.server import build_app
from mcp_gateway.settings import GatewaySettings


def _client(monkeypatch, **env) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = build_app(GatewaySettings())
    return TestClient(app)


def _rpc(client: TestClient, method: str, params=None, msg_id=1, token="gw-token"):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_healthz_exposes_tool_count(monkeypatch):
    client = _client(monkeypatch, MCP_GATEWAY_AUTH_TOKEN="gw-token")
    response = client.get("/healthz")
    assert response.status_code == 200
    # 13 个复用插件工具（spec 3 + poly 5 + sciverse 5）+ 4 个谱图任务工具。
    assert response.json() == {"ok": True, "tools": 17}


def test_initialize_handshake_and_auth(monkeypatch):
    client = _client(monkeypatch, MCP_GATEWAY_AUTH_TOKEN="gw-token")
    unauthorized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert unauthorized.status_code == 401

    response = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "ai4ms-professional-tools-gateway"


def test_tools_list_contains_sanitized_plugin_tools(monkeypatch):
    client = _client(monkeypatch, MCP_GATEWAY_AUTH_TOKEN="gw-token")
    response = _rpc(client, "tools/list")
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert "sciverse_agentic_search" in names  # 点号已 sanitize
    assert "poly_tg_predict" in names
    assert "spec_nmr_forward" in names
    assert {"spec_spectra_submit", "spec_spectra_status", "spec_spectra_result", "spec_spectra_cancel"} <= names
    assert all("." not in name for name in names)


def test_tools_call_reuses_plugin_handler_with_gateway_credentials(monkeypatch):
    """工具调用走复用的插件函数，且凭证经网关注入（不下发给 SD）。"""
    from mcp_gateway import registry

    captured = {}

    async def fake_sciverse(ctx, args):
        captured["config"] = ctx.extra["plugins"]["sciverse"]
        captured["args"] = args
        from synlys_harness.types import ToolResult

        return ToolResult(ok=True, content="检索完成", data={})

    monkeypatch.setitem(registry.PLUGIN_TOOL_MODULES, "sciverse", ("tools.py", ("sciverse_agentic_search",)))
    def patched():
        table = {}
        module = pytest.importorskip("synlys_harness.types")
        fake_sciverse.__tool_definition__ = type(
            "D", (), {"name": "sciverse.agentic_search", "description": "d", "parameters": {}, "timeout_s": 5}
        )()
        table["sciverse_agentic_search"] = {"fn": fake_sciverse, "definition": fake_sciverse.__tool_definition__}
        return table

    monkeypatch.setattr("mcp_gateway.server.build_plugin_tool_table", patched)
    client = _client(monkeypatch, MCP_GATEWAY_AUTH_TOKEN="gw-token", SCIVERSE_BASE_URL="http://10.0.0.9", SCIVERSE_API_TOKEN="secret-token")

    response = _rpc(client, "tools/call", {"name": "sciverse_agentic_search", "arguments": {"query": "polymer"}})
    body = response.json()
    assert body["result"]["isError"] is False
    assert body["result"]["content"][0]["text"] == "检索完成"
    assert captured["config"]["api_token"] == "secret-token"


def test_spectra_submit_validates_arguments(monkeypatch):
    client = _client(monkeypatch, MCP_GATEWAY_AUTH_TOKEN="gw-token")
    bad_kind = _rpc(client, "tools/call", {"name": "spec_spectra_submit", "arguments": {"kind": "xrd", "file_name": "a.csv", "file_base64": "aGk="}})
    assert bad_kind.json()["result"]["isError"] is True

    bad_path = _rpc(client, "tools/call", {"name": "spec_spectra_submit", "arguments": {"kind": "nmr", "file_name": "../escape.csv", "file_base64": "aGk="}})
    assert bad_path.json()["result"]["isError"] is True
