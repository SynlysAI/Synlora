"""公共 MCP（catalog 条目）HTTP 发现与状态缓存测试。"""
from pathlib import Path

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService

PKG_HTTP = McpPackage(
    id="pub-http", name="公共 HTTP", description="d",
    transport="streamable-http", url="https://example.com/mcp",
    directory=Path("."),
)


class _FakeStore:
    async def get(self, *_a, **_k):
        return None


def _service(tools_payload):
    captured = {}

    class _Client:
        def __init__(self, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            body = {"result": {"tools": tools_payload}} if json.get("method") == "tools/list" \
                else {"result": {}}

            class _Resp:
                status_code = 200
                headers = {"content-type": "application/json"}
                content = b"x"

                def raise_for_status(self):
                    return None

                def json(self):
                    return body
            return _Resp()

    return McpService(
        _FakeStore(), "",
        client_factory=lambda: _Client(),
        catalog_mcps=lambda: {"pub-http": PKG_HTTP},
    ), captured


@pytest.mark.asyncio
async def test_discover_public_tools_http():
    service, captured = _service([{"name": "echo", "description": "回声",
                                   "inputSchema": {"type": "object"}}])
    tools = await service.discover_public_tools("pub-http")
    assert captured["url"] == "https://example.com/mcp"
    assert tools[0]["name"] == "echo"
    assert tools[0]["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_test_public_connection_updates_status():
    service, _ = _service([])
    await service.test_public_connection("pub-http")
    status = service.public_status("pub-http")
    assert status["status"] == "connected"
    assert status["tool_count"] == 0


@pytest.mark.asyncio
async def test_unknown_public_mcp_raises():
    service, _ = _service([])
    with pytest.raises(KeyError):
        await service.discover_public_tools("nope")
