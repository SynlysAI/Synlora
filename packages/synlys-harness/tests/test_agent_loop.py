"""agent loop 单测（FakeBackend 脚本化驱动）。"""
import asyncio

import pytest

from synlys_harness.agent import RunSession
from synlys_harness.events import EventLog
from synlys_harness.models.backend import ReasoningDelta, TextDelta, ToolCallChunk, Usage
from synlys_harness.session import derive_messages
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


def _session(backend, hooks=None, max_steps=25, workspace_root=None,
             context_extra=None) -> RunSession:
    reg = ToolRegistry()
    reg.register(add)
    log = EventLog()
    cfg = AgentConfig(system_prompt="你是助手", tool_names=["add"], max_steps=max_steps)
    return RunSession(
        config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
        backend=backend, event_log=log, user_id="u1", run_id="r1",
        hooks=hooks, workspace_root=workspace_root, context_extra=context_extra,
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


async def test_take_queued_turn_after_final_answer():
    """收尾窗口插话不被本轮消费：take_queued_turn 取出供宿主续跑下一轮。"""

    class SlowFinalBackend(FakeBackend):
        """每轮都延时流式输出纯文本最终回答（无下一个 step 可消费插话）。"""

        async def stream(self, messages, tools=None):
            self.calls.append(messages)
            for _ in range(10):
                await asyncio.sleep(0.005)
                yield TextDelta(text="最终回答")
            yield Usage(prompt_tokens=1, completion_tokens=1)

    backend = SlowFinalBackend([])
    session = _session(backend)
    task = asyncio.create_task(_collect(session, "第一问"))
    await asyncio.sleep(0.02)
    session.steer("收尾后来的一句")  # 本轮已无下一个 step，插话留队列
    events = await task
    assert EventType.TURN_END in [e.type for e in events]
    assert session.take_queued_turn() == "收尾后来的一句"
    # 宿主续跑：插话作为下一轮正式输入，LLM 可见
    await _collect(session, "收尾后来的一句")
    assert any(m.role.value == "user" and m.content == "收尾后来的一句"
               for m in backend.calls[1])
    assert session.take_queued_turn() is None


async def test_take_queued_turn_empty():
    """无残留插话时 take_queued_turn 返回 None。"""
    backend = FakeBackend([[TextDelta(text="ok")]])
    session = _session(backend)
    await _collect(session, "hi")
    assert session.take_queued_turn() is None


async def test_take_queued_turn_discarded_on_cancel():
    """取消路径的残留插话直接丢弃（不续跑）。"""
    backend = FakeBackend([[TextDelta(text="ok")]])
    session = _session(backend)
    await _collect(session, "hi")
    session.steer("迟到的插话")
    session.cancel()
    assert session.take_queued_turn() is None
    assert session.take_queued_turn() is None  # 队列已清空


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


async def test_context_extra_reaches_tools(tmp_path):
    """context_extra 经 RunSession 透传到 ToolContext（http 白名单生效）。"""
    seen = {}

    @tool(name="probe", description="探测", parameters={"type": "object", "properties": {}})
    async def probe(ctx, args):
        """记录 extra。"""
        seen.update(ctx.extra)
        return ToolResult(ok=True, content="ok")

    reg = ToolRegistry()
    reg.register(probe)
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="probe", arguments={})],
        [TextDelta(text="done")],
    ])
    log = EventLog()
    cfg = AgentConfig(system_prompt="s", tool_names=["probe"])
    session = RunSession(
        config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
        backend=backend, event_log=log, user_id="u1", run_id="r1",
        context_extra={"http_allowed_hosts": ["api.example.com"]},
    )
    events = [ev async for ev in session.run("探测")]
    assert EventType.TOOL_RESULT in [e.type for e in events]
    assert seen.get("http_allowed_hosts") == ["api.example.com"]


async def test_wake_source_marks_user_message_as_job_completed():
    """任务完成唤醒：首条 user/message 带 kind=job_completed 与 job_id，供前端渲染提示条。"""
    backend = FakeBackend([[TextDelta(text="收到")]])
    session = _session(backend, context_extra={"wake_source": {"job_id": "j1"}})
    events = await _collect(session, "任务完成通知")
    user_events = [e for e in events if e.type is EventType.USER_MESSAGE]
    assert user_events[0].payload["kind"] == "job_completed"
    assert user_events[0].payload["job_id"] == "j1"
    # 标记不污染 LLM 投影：正文仍是通知原文
    msgs = derive_messages(session._log.events)
    assert [m.content for m in msgs if m.role.value == "user"] == ["任务完成通知"]


async def test_no_wake_source_keeps_plain_user_message():
    """普通用户发言（无 wake_source）：user/message 不带 kind 字段。"""
    backend = FakeBackend([[TextDelta(text="收到")]])
    session = _session(backend)
    events = await _collect(session, "你好")
    user_events = [e for e in events if e.type is EventType.USER_MESSAGE]
    assert "kind" not in user_events[0].payload


async def test_reasoning_flow_delta_and_final():
    """思考链路：reasoning/delta 流出 + assistant/reasoning 定稿全文 + turn/end 带 usage。"""
    backend = FakeBackend([[
        ReasoningDelta(text="先想"),
        ReasoningDelta(text="一想"),
        TextDelta(text="答"),
        Usage(prompt_tokens=7, completion_tokens=4),
    ]])
    session = _session(backend)
    events = await _collect(session, "问")
    types = [e.type for e in events]
    deltas = [e for e in events if e.type is EventType.REASONING_DELTA]
    assert [d.payload["text"] for d in deltas] == ["先想", "一想"]
    finals = [e for e in events if e.type is EventType.ASSISTANT_REASONING]
    assert len(finals) == 1
    assert finals[0].payload["content"] == "先想一想"
    # 定稿先于 assistant/message 发出
    assert types.index(EventType.ASSISTANT_REASONING) < types.index(EventType.ASSISTANT_MESSAGE)
    # turn/end payload 携带 usage
    end = [e for e in events if e.type is EventType.TURN_END][0]
    assert end.payload["usage"] == {"prompt_tokens": 7, "completion_tokens": 4}
    # 思考事件不进入投影（LLM 上下文不受影响）
    msgs = derive_messages(session._log.events)
    assert [(m.role.value, m.content) for m in msgs] == [
        ("system", "你是助手"), ("user", "问"), ("assistant", "答"),
    ]


async def test_reasoning_finalized_each_step():
    """多 step turn（工具循环）每 step 各发一条 assistant/reasoning 定稿；无 Usage 时 turn/end 不带 usage。"""
    backend = FakeBackend([
        [ReasoningDelta(text="查"), ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2})],
        [ReasoningDelta(text="总"), TextDelta(text="3")],
    ])
    events = await _collect(_session(backend), "1+2")
    finals = [e for e in events if e.type is EventType.ASSISTANT_REASONING]
    assert [f.payload["content"] for f in finals] == ["查", "总"]
    end = [e for e in events if e.type is EventType.TURN_END][0]
    assert "usage" not in end.payload


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


async def test_read_image_injected_as_transient_user_message(tmp_path):
    """file.read_image：图片经瞬态 user 消息注入下一次 LLM 调用，不落事件流。"""
    # 工作区放一张真图（1x1 png）
    import base64

    (tmp_path / "pic.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))

    @tool(name="read_img", description="读图", parameters={
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    async def read_img(ctx, args):
        """读图。"""
        # 直接复用内置实现逻辑：读文件转 base64 放 data.images
        raw = (tmp_path / args["path"]).read_bytes()
        return ToolResult(ok=True, content="（已附图片）",
                          data={"images": [{"mime": "image/png",
                                            "base64": base64.b64encode(raw).decode()}]})

    reg = ToolRegistry()
    reg.register(read_img)
    log = EventLog()
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="read_img", arguments={"path": "pic.png"})],
        [TextDelta(text="看到一张 1x1 的图"), Usage()],
    ])
    session = RunSession(
        config=AgentConfig(system_prompt="p", tool_names=["read_img"]),
        registry=reg, pipeline=ToolPipeline(registry=reg), backend=backend,
        event_log=log, user_id="u", run_id="r", workspace_root=tmp_path,
    )
    events = [e async for e in session.run("看看图")]
    # 第二次 LLM 调用收到带 images 的 user 消息（在末尾）
    second = backend.calls[1]
    img_msg = [m for m in second if m.images]
    assert img_msg and img_msg[0].images[0]["mime"] == "image/png"
    assert second[-1].role.value == "user"
    # 图片本体不落事件流（瞬态语义）
    assert all("base64" not in (e.payload.get("content") or "") for e in events)
