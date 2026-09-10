"""agent loop 单测（FakeBackend 脚本化驱动）。"""
import asyncio

import pytest

from synlys_harness.agent import RunSession
from synlys_harness.events import EventLog
from synlys_harness.models.backend import TextDelta, ToolCallChunk, Usage
from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import AgentConfig, EventType, ExtensionHooks, ToolResult


@tool(name="add", description="加法", parameters={
    "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
})
async def add(ctx, args):
    """加法。"""
    return ToolResult(ok=True, content=str(args["a"] + args["b"]))


class FakeBackend:
    """脚本化后端：按预设剧本依次返回事件列表。"""

    def __init__(self, script: list[list]):
        """script: 每次调用返回一个 StreamEvent 列表。"""
        self.script = script
        self.calls: list[list] = []

    async def stream(self, messages, tools=None):
        """按剧本产出。"""
        self.calls.append(messages)
        for ev in self.script.pop(0):
            yield ev


def _session(backend, hooks=None, max_steps=25, workspace_root=None) -> RunSession:
    reg = ToolRegistry()
    reg.register(add)
    log = EventLog()
    cfg = AgentConfig(system_prompt="你是助手", tool_names=["add"], max_steps=max_steps)
    return RunSession(
        config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
        backend=backend, event_log=log, user_id="u1", run_id="r1",
        hooks=hooks, workspace_root=workspace_root,
    )


async def _collect(session: RunSession, text: str):
    return [ev async for ev in session.run(text)]


async def test_plain_text_turn():
    """纯文本回复：llm/delta 流出 + assistant_message + turn_end。"""
    backend = FakeBackend([[TextDelta(text="你"), TextDelta(text="好"), Usage(prompt_tokens=3, completion_tokens=2)]])
    events = await _collect(_session(backend), "嗨")
    types = [e.type for e in events]
    assert EventType.TURN_START in types and EventType.USER_MESSAGE in types
    deltas = [e for e in events if e.type is EventType.LLM_DELTA]
    assert "".join(d["text"] for d in (e.payload for e in deltas)) == "你好"
    assert types[-1] is EventType.TURN_END
    assert EventType.ASSISTANT_MESSAGE in types


async def test_tool_call_then_answer():
    """工具调用轮：tool/call + tool/result 后续答，turn 恰好结束。"""
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2})],
        [TextDelta(text="和是 3"), Usage(prompt_tokens=10, completion_tokens=5)],
    ])
    events = await _collect(_session(backend), "1+2=?")
    tc = [e for e in events if e.type is EventType.TOOL_CALL][0]
    tr = [e for e in events if e.type is EventType.TOOL_RESULT][0]
    assert tc.payload["tool_call"]["name"] == "add"
    assert tr.payload["content"] == "3" and tr.payload["ok"] is True
    # 第二次 LLM 调用应看到 tool 结果消息
    second_call = backend.calls[1]
    assert any(m.role.value == "tool" and m.content == "3" for m in second_call)


async def test_max_steps_guard():
    """步数上限：LLM 持续要求工具时最终发 error 并收尾。"""
    endless = [ToolCallChunk(id="c0", name="add", arguments={"a": 1, "b": 1})]
    backend = FakeBackend([list(endless) for _ in range(3)])
    events = await _collect(_session(backend, max_steps=3), "loop")
    assert EventType.ERROR in [e.type for e in events]
    assert EventType.TURN_END in [e.type for e in events]


async def test_llm_error_aborts_turn():
    """LLM 调用失败：发 error 事件并 abort，异常不穿透。"""
    class BoomBackend:
        async def stream(self, messages, tools=None):
            raise RuntimeError("连接失败")
            yield  # pragma: no cover

    events = await _collect(_session(BoomBackend()), "hi")
    types = [e.type for e in events]
    assert EventType.ERROR in types and EventType.TURN_ABORTED in types
    assert EventType.TURN_END not in types


async def test_cancel_mid_stream():
    """流式中途取消：在流内检查点尽快断开，发 turn/aborted 并停止。"""
    started = asyncio.Event()

    class SlowBackend:
        async def stream(self, messages, tools=None):
            started.set()
            for _ in range(30):
                await asyncio.sleep(0.005)
                yield TextDelta(text="x")

    session = _session(SlowBackend())
    task = asyncio.create_task(_collect(session, "hi"))
    await started.wait()
    session.cancel()
    events = await task
    types = [e.type for e in events]
    assert EventType.TURN_ABORTED in types
    assert EventType.TURN_END not in types
    # 取消后流在第一个 delta 检查点断开，不再消费剩余 delta
    assert sum(1 for t in types if t is EventType.LLM_DELTA) <= 2


async def test_steering_injected_next_step(tmp_path):
    """中途插话注入下一 step 的 user 消息。"""
    gate = asyncio.Event()

    @tool(name="wait", description="等待", parameters={"type": "object", "properties": {}})
    async def wait_tool(ctx, args):
        """等待 gate。"""
        await gate.wait()
        return ToolResult(ok=True, content="done")

    reg = ToolRegistry()
    reg.register(add)
    reg.register(wait_tool)
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="wait", arguments={})],
        [TextDelta(text="ok")],
    ])
    log = EventLog()
    cfg = AgentConfig(system_prompt="s", tool_names=["add", "wait"])
    session = RunSession(config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
                         backend=backend, event_log=log, user_id="u1", run_id="r1")
    task = asyncio.create_task(_collect(session, "开始"))
    await asyncio.sleep(0.05)
    session.steer("插句话")
    gate.set()
    events = await task
    assert EventType.TURN_END in [e.type for e in events]
    # 第二次 LLM 调用消息里含插话 user 消息
    steering_msgs = [m for m in backend.calls[1] if m.role.value == "user" and m.content == "插句话"]
    assert steering_msgs


async def test_extension_hooks_invoked():
    """四钩子中 before_llm_call 可改写消息、on_session_start/end 被调用。"""
    seen = {}

    async def on_start(ctx):
        seen["start"] = True

    async def before_llm(messages):
        seen["count"] = len(messages)
        return messages

    async def on_tool_event(ev):
        seen.setdefault("tool_events", []).append(ev.type)

    async def on_end(ctx):
        seen["end"] = True

    hooks = ExtensionHooks(
        on_session_start=on_start, before_llm_call=before_llm,
        on_tool_event=on_tool_event, on_session_end=on_end,
    )
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2})],
        [TextDelta(text="done")],
    ])
    await _collect(_session(backend, hooks=hooks), "算")
    assert seen["start"] and seen["end"] and seen["count"] >= 2
    assert EventType.TOOL_CALL in seen["tool_events"] and EventType.TOOL_RESULT in seen["tool_events"]


async def test_event_log_and_stream_consistent():
    """产出的事件流与 EventLog 记录一致（含 seq 连续）。"""
    backend = FakeBackend([[TextDelta(text="hi")]])
    session = _session(backend)
    yielded = await _collect(session, "q")
    logged = session._log.events
    assert [e.seq for e in yielded] == [e.seq for e in logged]
    assert [e.seq for e in logged] == list(range(len(logged)))


async def test_cancel_reset_between_turns():
    """第一轮取消后同一 session 可再次 run（cancel 标志每轮重置）。"""
    started = asyncio.Event()

    class SlowThenTextBackend:
        """首次调用慢流（供取消），其后正常文本回答。"""

        def __init__(self):
            self.slow_done = False

        async def stream(self, messages, tools=None):
            if not self.slow_done:
                self.slow_done = True
                started.set()
                for _ in range(30):
                    await asyncio.sleep(0.005)
                    yield TextDelta(text="x")
            else:
                yield TextDelta(text="第二答")

    session = _session(SlowThenTextBackend())
    task = asyncio.create_task(_collect(session, "第一问"))
    await started.wait()
    session.cancel()
    first = await task
    assert EventType.TURN_ABORTED in [e.type for e in first]

    second = await _collect(session, "第二问")
    types = [e.type for e in second]
    assert EventType.ASSISTANT_MESSAGE in types
    assert types[-1] is EventType.TURN_END


async def test_consumer_break_still_logs_aborted_and_runs_hook():
    """消费者提前断开（aclose → GeneratorExit）：日志记 turn/aborted（含 reason）且钩子执行，不抛 RuntimeError。"""
    seen = {}

    async def on_end(ctx):
        seen["end"] = True

    hooks = ExtensionHooks(on_session_end=on_end)
    backend = FakeBackend([[TextDelta(text="答")]])
    session = _session(backend, hooks=hooks)
    agen = session.run("q")
    await anext(agen)  # turn/start
    await anext(agen)  # user/message
    await anext(agen)  # llm/delta（已进入 step 循环 try 块内）
    await agen.aclose()  # 消费者断开（等价 SSE 连接关闭）

    types = [e.type for e in session._log.events]
    assert EventType.TURN_ABORTED in types
    aborted = [e for e in session._log.events if e.type is EventType.TURN_ABORTED][0]
    assert aborted.payload.get("reason")
    assert seen.get("end") is True


async def test_multi_tool_calls_single_step():
    """单 step 并行双 call：2 个 tool/call + 2 个 tool_result，无文本时首个 call content 为 None。"""
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2}),
         ToolCallChunk(id="c2", name="add", arguments={"a": 3, "b": 4})],
        [TextDelta(text="完成")],
    ])
    events = await _collect(_session(backend), "算")
    calls = [e for e in events if e.type is EventType.TOOL_CALL]
    results = [e for e in events if e.type is EventType.TOOL_RESULT]
    assert len(calls) == 2 and len(results) == 2
    by_id = {r.payload["tool_call_id"]: r.payload["content"] for r in results}
    assert by_id == {"c1": "3", "c2": "7"}
    assert calls[0].payload["content"] is None
