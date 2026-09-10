"""工具管线单测。"""
import asyncio

from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import Permission, ToolContext, ToolResult


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path)


@tool(name="add", description="加法", parameters={
    "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
})
async def add(ctx, args):
    """加法。"""
    return ToolResult(ok=True, content=str(args["a"] + args["b"]))


@tool(name="boom", description="抛错", parameters={"type": "object", "properties": {}})
async def boom(ctx, args):
    """抛错。"""
    raise RuntimeError("炸了")


@tool(name="slow", description="慢", parameters={"type": "object", "properties": {}}, timeout_s=0.05)
async def slow(ctx, args):
    """慢。"""
    await asyncio.sleep(5)
    return ToolResult(ok=True, content="never")


@tool(name="big", description="大输出", parameters={"type": "object", "properties": {}})
async def big(ctx, args):
    """大输出。"""
    return ToolResult(ok=True, content="x" * 100)


@tool(name="raw_dict", description="返回裸 dict", parameters={"type": "object", "properties": {}})
async def raw_dict(ctx, args):
    """返回裸 dict（非法结果类型）。"""
    return {"content": "x"}


@tool(name="banned", description="被禁用", parameters={"type": "object", "properties": {}},
      permission=Permission.DENY)
async def banned(ctx, args):
    """被禁用的工具。"""
    return ToolResult(ok=True, content="不应执行到这里")


def _pipeline() -> tuple[ToolPipeline, ToolRegistry]:
    reg = ToolRegistry()
    reg.register(add); reg.register(boom); reg.register(slow); reg.register(big)
    reg.register(raw_dict); reg.register(banned)
    return ToolPipeline(registry=reg), reg


async def test_execute_ok(tmp_path):
    """正常执行返回工具结果。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1, "b": 2})
    assert r.ok and r.content == "3"


async def test_unknown_tool(tmp_path):
    """未注册工具返回 error result（不抛异常）。"""
    pipe, _ = _pipeline()
    r = await pipe.run("nope", _ctx(tmp_path), {})
    assert not r.ok and r.error == "unknown_tool"


async def test_missing_required_arg(tmp_path):
    """缺 required 参数返回 error result。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1})
    assert not r.ok and r.error == "invalid_arguments"


async def test_args_not_dict(tmp_path):
    """args 非 dict 返回 invalid_arguments error result。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), "notadict")  # type: ignore[arg-type]
    assert not r.ok and r.error == "invalid_arguments"


async def test_exception_becomes_error_result(tmp_path):
    """工具内部异常捕获为 error result，不中断 run。"""
    pipe, _ = _pipeline()
    r = await pipe.run("boom", _ctx(tmp_path), {})
    assert not r.ok and r.error == "tool_exception" and "炸了" in r.content


async def test_non_toolresult_return(tmp_path):
    """工具返回非 ToolResult 时转为 error result，不抛异常。"""
    pipe, _ = _pipeline()
    r = await pipe.run("raw_dict", _ctx(tmp_path), {})
    assert not r.ok and r.error == "invalid_result"


async def test_deny_by_permission(tmp_path):
    """permission=DENY 的工具在 pre-execute 即被拒绝。"""
    pipe, _ = _pipeline()
    r = await pipe.run("banned", _ctx(tmp_path), {})
    assert not r.ok and r.error == "denied"


async def test_timeout(tmp_path):
    """超时被杀并返回 timeout error result。"""
    pipe, _ = _pipeline()
    r = await pipe.run("slow", _ctx(tmp_path), {})
    assert not r.ok and r.error == "timeout"


async def test_post_execute_truncation(tmp_path):
    """输出超过 max_output_chars 被截断并标记 truncated。"""
    pipe, _ = _pipeline()
    r = await pipe.run("big", _ctx(tmp_path), {}, max_output_chars=10)
    assert r.ok and r.truncated and len(r.content) == 10


async def test_deny_by_not_allowed(tmp_path):
    """不在 allowed 白名单内直接拒绝。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1, "b": 2}, allowed=["other"])
    assert not r.ok and r.error == "denied"
