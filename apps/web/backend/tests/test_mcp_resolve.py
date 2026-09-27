"""resolve_runtime_mcps 来源合并与优先级测试。"""
from pathlib import Path

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService

PKG = McpPackage(id="dup", name="公共版", description="d",
                 transport="streamable-http", url="https://pub.example.com/mcp",
                 directory=Path("."))


class _Store:
    def __init__(self, docs):
        self._docs = docs

    async def get(self, _col, doc_id):
        return self._docs.get(doc_id)

    async def update(self, _col, _doc_id, _fields):
        return None


def _service(user_docs, tools_payload):
    class _Client:
        def __init__(self, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, headers=None, json=None):
            method = json.get("method")
            body = ({"result": {"tools": tools_payload}} if method == "tools/list"
                    else {"result": {"content": [{"type": "text", "text": "ok"}]}})

            class _Resp:
                status_code = 200
                headers = {"content-type": "application/json"}
                content = b"x"

                def raise_for_status(self):
                    return None

                def json(self):
                    return body
            return _Resp()

    return McpService(_Store(user_docs), "",
                      client_factory=lambda: _Client(),
                      catalog_mcps=lambda: {"dup": PKG})


def _user_doc(enabled=True, tools=None):
    return {"user_id": "u1", "mcp_id": "dup", "name": "自建版",
            "url": "https://mine.example.com/mcp", "enabled": enabled,
            "headers_secret": None, "bearer_token_secret": None,
            "tools": tools or []}


@pytest.mark.asyncio
async def test_user_owned_shadows_catalog_same_id():
    service = _service({"u1:dup": _user_doc()}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert len(rows) == 1
    assert rows[0]["source"] == "user"
    assert rows[0]["name"] == "自建版"


@pytest.mark.asyncio
async def test_user_owned_disabled_skips_entirely():
    """自建存在但停用 → 整体跳过，不回落公共同名条目。"""
    service = _service({"u1:dup": _user_doc(enabled=False)}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert rows == []


@pytest.mark.asyncio
async def test_catalog_used_when_no_user_doc_and_visible():
    service = _service({}, [{"name": "pub-tool", "description": "d",
                             "inputSchema": {"type": "object"}}])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert rows[0]["source"] == "catalog"
    assert rows[0]["tools"][0]["name"] == "pub-tool"


@pytest.mark.asyncio
async def test_catalog_invisible_skipped():
    service = _service({}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids=set())
    assert rows == []


@pytest.mark.asyncio
async def test_call_runtime_tool_dispatches_by_source():
    service = _service({}, [])
    result = await service.call_runtime_tool(
        "u1", "catalog", "dup", "pub-tool", {"x": 1})
    assert result["content"][0]["text"] == "ok"
