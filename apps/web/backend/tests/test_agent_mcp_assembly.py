"""MCP 装配（会话勾选 ∪ 专家引用）测试。"""
import pytest

from app.services.agent_service import assemble_mcp_tools
from synlys_harness.tools.registry import ToolRegistry
from synlys_harness.types import ToolResult


class _FakeMcpService:
    """记录 resolve 入参、按预设返回连接列表与调用结果的桩。"""

    def __init__(self, rows):
        self._rows = rows
        self.resolved_ids = None
        self.visible_ids = None
        self.calls = []

    async def resolve_runtime_mcps(self, user_id, mcp_ids, visible_catalog_ids):
        self.resolved_ids = list(mcp_ids)
        self.visible_ids = set(visible_catalog_ids)
        return self._rows

    async def call_runtime_tool(self, user_id, source, mcp_id, tool_name, args):
        self.calls.append((source, mcp_id, tool_name, args))
        return {"content": [{"type": "text", "text": f"ran {tool_name}"}],
                "isError": False}


class _FakeCapability:
    def __init__(self, visible):
        self._visible = visible

    async def visible_ids(self, user_id, kind):
        return set(self._visible) if kind == "mcp" else set()


def _row(mcp_id, tool_name):
    return {"id": mcp_id, "name": mcp_id, "source": "catalog",
            "transport": "streamable-http",
            "tools": [{"name": tool_name, "description": f"{mcp_id} 的 {tool_name}",
                       "input_schema": {"type": "object", "properties": {}}}]}


@pytest.mark.asyncio
async def test_assemble_merges_session_and_expert_refs():
    service = _FakeMcpService([_row("a", "tool-a"), _row("b", "tool-b")])
    registry = ToolRegistry()
    names = await assemble_mcp_tools(
        service, _FakeCapability({"a", "b"}), "u1", ["a"], ["b"], registry)
    # 去重保序：会话勾选在前、专家引用在后
    assert service.resolved_ids == ["a", "b"]
    assert service.visible_ids == {"a", "b"}
    assert names == ["mcp.a.tool-a", "mcp.b.tool-b"]
    assert registry.find("mcp.a.tool-a") is not None


@pytest.mark.asyncio
async def test_assemble_dedup_same_id():
    service = _FakeMcpService([_row("a", "tool-a")])
    registry = ToolRegistry()
    names = await assemble_mcp_tools(
        service, _FakeCapability({"a"}), "u1", ["a", "a"], ["a"], registry)
    assert service.resolved_ids == ["a"]
    assert names == ["mcp.a.tool-a"]


@pytest.mark.asyncio
async def test_assemble_skips_invisible_and_failed():
    """resolve 阶段已剔除不可见/连接失败条目；装配只注册返回项。"""
    service = _FakeMcpService([_row("b", "tool-b")])
    registry = ToolRegistry()
    names = await assemble_mcp_tools(
        service, _FakeCapability(set()), "u1", ["a", "b"], [], registry)
    assert names == ["mcp.b.tool-b"]


@pytest.mark.asyncio
async def test_assemble_without_assistant_still_uses_session_refs():
    service = _FakeMcpService([_row("a", "tool-a")])
    registry = ToolRegistry()
    names = await assemble_mcp_tools(
        service, _FakeCapability({"a"}), "u1", ["a"], [], registry)
    assert names == ["mcp.a.tool-a"]


@pytest.mark.asyncio
async def test_assembled_tool_executes_via_service():
    service = _FakeMcpService([_row("a", "tool-a")])
    registry = ToolRegistry()
    await assemble_mcp_tools(
        service, _FakeCapability({"a"}), "u1", ["a"], [], registry)
    tool = registry.get("mcp.a.tool-a")
    result = await tool.execute(None, {"x": 1})
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.content == "ran tool-a"
    assert service.calls == [("catalog", "a", "tool-a", {"x": 1})]


@pytest.mark.asyncio
async def test_assemble_skips_name_collision_with_existing_tool():
    service = _FakeMcpService([_row("a", "tool-a")])
    registry = ToolRegistry()

    from synlys_harness.tools.registry import tool

    @tool("mcp.a.tool-a", "已占用的名字", {"type": "object", "properties": {}})
    async def existing(_ctx, _args):
        return ToolResult(ok=True, content="original")

    registry.register(existing)
    names = await assemble_mcp_tools(
        service, _FakeCapability({"a"}), "u1", ["a"], [], registry)
    assert names == []  # 撞名跳过，不覆盖既有工具
