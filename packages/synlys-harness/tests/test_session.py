"""derive_messages 投影单测。"""
from synlys_harness.session import derive_messages
from synlys_harness.types import EventType, Role, SessionEvent


def _ev(seq: int, type_: EventType, payload: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=type_, payload=payload, ts=float(seq))


def test_full_projection():
    """完整一轮 turn 的事件投影为合法消息序列。"""
    events = [
        _ev(0, EventType.TURN_START, {"system_prompt": "你是助手"}),
        _ev(1, EventType.USER_MESSAGE, {"text": "分析数据"}),
        _ev(2, EventType.TOOL_CALL, {
            "tool_call": {"id": "c1", "name": "python.run", "arguments": {"code": "1"}},
            "content": None,
        }),
        _ev(3, EventType.TOOL_RESULT, {
            "tool_call_id": "c1", "name": "python.run", "ok": True, "content": "1",
        }),
        _ev(4, EventType.ASSISTANT_MESSAGE, {"content": "结果是 1"}),
        _ev(5, EventType.TURN_END, {}),
    ]
    msgs = derive_messages(events, include_system=True)
    assert [m.role.value for m in msgs] == ["system", "user", "assistant", "tool", "assistant"]
    assert msgs[0].content == "你是助手"
    assert msgs[2].tool_calls[0].name == "python.run"
    assert msgs[3].tool_call_id == "c1"
    assert msgs[3].content == "1"


def test_delta_not_projected():
    """llm/delta 不进入投影（由 assistant/message 承载最终文本）。"""
    events = [
        _ev(0, EventType.LLM_DELTA, {"text": "部分"}),
        _ev(1, EventType.ASSISTANT_MESSAGE, {"content": "部分"}),
    ]
    msgs = derive_messages(events, include_system=False)
    assert len(msgs) == 1 and msgs[0].content == "部分"


def test_reasoning_events_not_projected():
    """思考事件（reasoning/delta 与 assistant/reasoning）不进入 LLM 上下文投影。

    与 DeepSeek 官方多轮语义一致：思考内容只做展示回放，不回投给 LLM。
    """
    events = [
        _ev(0, EventType.TURN_START, {"system_prompt": "你是助手"}),
        _ev(1, EventType.USER_MESSAGE, {"text": "问"}),
        _ev(2, EventType.REASONING_DELTA, {"text": "想"}),
        _ev(3, EventType.ASSISTANT_REASONING, {"content": "想"}),
        _ev(4, EventType.ASSISTANT_MESSAGE, {"content": "答"}),
        _ev(5, EventType.TURN_END, {}),
    ]
    msgs = derive_messages(events, include_system=True)
    assert [m.role.value for m in msgs] == ["system", "user", "assistant"]
    assert msgs[-1].content == "答"


def test_no_system_when_disabled():
    """include_system=False 时不产出 system 消息。"""
    events = [_ev(0, EventType.TURN_START, {"system_prompt": "x"})]
    assert derive_messages(events, include_system=False) == []


def test_consecutive_tool_calls_aggregated():
    """连续两个 tool/call（中间无其他投影事件）聚合为一条 assistant 多 tool_calls 消息。"""
    events = [
        _ev(0, EventType.USER_MESSAGE, {"text": "跑两段"}),
        _ev(1, EventType.TOOL_CALL, {
            "tool_call": {"id": "c1", "name": "python.run", "arguments": {"code": "1"}},
            "content": None,
        }),
        _ev(2, EventType.TOOL_CALL, {
            "tool_call": {"id": "c2", "name": "python.run", "arguments": {"code": "2"}},
            "content": "先跑两段",
        }),
        _ev(3, EventType.TOOL_RESULT, {
            "tool_call_id": "c1", "name": "python.run", "ok": True, "content": "1",
        }),
        _ev(4, EventType.TOOL_RESULT, {
            "tool_call_id": "c2", "name": "python.run", "ok": True, "content": "2",
        }),
        # 第二轮聚合：content 均为 None，不应残留上一轮的 content。
        _ev(5, EventType.TOOL_CALL, {
            "tool_call": {"id": "c3", "name": "python.run", "arguments": {"code": "3"}},
            "content": None,
        }),
        _ev(6, EventType.TOOL_CALL, {
            "tool_call": {"id": "c4", "name": "python.run", "arguments": {"code": "4"}},
            "content": None,
        }),
        _ev(7, EventType.TOOL_RESULT, {
            "tool_call_id": "c3", "name": "python.run", "ok": True, "content": "3",
        }),
        _ev(8, EventType.TOOL_RESULT, {
            "tool_call_id": "c4", "name": "python.run", "ok": True, "content": "4",
        }),
    ]
    msgs = derive_messages(events, include_system=False)
    assert [m.role.value for m in msgs] == [
        "user", "assistant", "tool", "tool", "assistant", "tool", "tool",
    ]
    assert len(msgs[1].tool_calls) == 2
    assert [tc.id for tc in msgs[1].tool_calls] == ["c1", "c2"]
    assert msgs[1].content == "先跑两段"
    assert [m.tool_call_id for m in msgs[2:4]] == ["c1", "c2"]
    assert len(msgs[4].tool_calls) == 2
    assert msgs[4].content is None
    assert [m.tool_call_id for m in msgs[5:]] == ["c3", "c4"]


def test_multi_turn_system_once():
    """两个完整 turn 的事件只投影一条 system 消息。"""

    def turn_events(base: int) -> list[SessionEvent]:
        return [
            _ev(base, EventType.TURN_START, {"system_prompt": "你是助手"}),
            _ev(base + 1, EventType.USER_MESSAGE, {"text": f"问题{base}"}),
            _ev(base + 2, EventType.ASSISTANT_MESSAGE, {"content": f"回答{base}"}),
            _ev(base + 3, EventType.TURN_END, {}),
        ]

    events = turn_events(0) + turn_events(4)
    msgs = derive_messages(events, include_system=True)
    assert [m.role.value for m in msgs] == ["system", "user", "assistant", "user", "assistant"]
    assert sum(1 for m in msgs if m.role is Role.SYSTEM) == 1


def test_steering_user_second_projection():
    """中途 steer 注入的第二条 user/message 同样投影为 user 消息。"""
    events = [
        _ev(0, EventType.USER_MESSAGE, {"text": "第一个问题"}),
        _ev(1, EventType.ASSISTANT_MESSAGE, {"content": "回答"}),
        _ev(2, EventType.USER_MESSAGE, {"text": "补充要求"}),
        _ev(3, EventType.ASSISTANT_MESSAGE, {"content": "最终回答"}),
    ]
    msgs = derive_messages(events, include_system=False)
    assert [m.role.value for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2].content == "补充要求"


def test_tool_chain_multiple_rounds():
    """tc→tr→tc→tr→assistant 交错链路各自独立投影（本就合法）。"""
    events = [
        _ev(0, EventType.USER_MESSAGE, {"text": "链式任务"}),
        _ev(1, EventType.TOOL_CALL, {
            "tool_call": {"id": "c1", "name": "search", "arguments": {"q": "a"}},
            "content": None,
        }),
        _ev(2, EventType.TOOL_RESULT, {
            "tool_call_id": "c1", "name": "search", "ok": True, "content": "r1",
        }),
        _ev(3, EventType.TOOL_CALL, {
            "tool_call": {"id": "c2", "name": "search", "arguments": {"q": "b"}},
            "content": None,
        }),
        _ev(4, EventType.TOOL_RESULT, {
            "tool_call_id": "c2", "name": "search", "ok": True, "content": "r2",
        }),
        _ev(5, EventType.ASSISTANT_MESSAGE, {"content": "完成"}),
    ]
    msgs = derive_messages(events, include_system=False)
    assert [m.role.value for m in msgs] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    assert [len(m.tool_calls) for m in msgs if m.tool_calls] == [1, 1]
    assert msgs[-1].content == "完成"


def test_user_message_none_text_projects_empty_string():
    """user/message 的 text 为 None 时投影为空串（OpenAI 要求 user content 非空）。"""
    events = [_ev(0, EventType.USER_MESSAGE, {"text": None})]
    msgs = derive_messages(events, include_system=False)
    assert msgs[0].content == ""


def test_orphan_tool_call_gets_synthetic_result():
    """call 与 result 之间中断（c1 无 result）：孤儿 call 补合成 tool 消息，顺序保持合法。"""
    events = [
        _ev(0, EventType.USER_MESSAGE, {"text": "跑两段"}),
        _ev(1, EventType.TOOL_CALL, {
            "tool_call": {"id": "c1", "name": "python.run", "arguments": {"code": "1"}},
            "content": None,
        }),
        _ev(2, EventType.TOOL_CALL, {
            "tool_call": {"id": "c2", "name": "python.run", "arguments": {"code": "2"}},
            "content": None,
        }),
        _ev(3, EventType.TOOL_RESULT, {
            "tool_call_id": "c2", "name": "python.run", "ok": True, "content": "2",
        }),
    ]
    msgs = derive_messages(events, include_system=False)
    # assistant(tool_calls=[c1, c2]) 后必须紧跟两条 tool 响应（OpenAI 顺序要求）
    assert [m.role.value for m in msgs] == ["user", "assistant", "tool", "tool"]
    assert [tc.id for tc in msgs[1].tool_calls] == ["c1", "c2"]
    tool_c1, tool_c2 = msgs[2], msgs[3]
    assert tool_c1.tool_call_id == "c1" and "未收到结果" in tool_c1.content
    assert tool_c2.tool_call_id == "c2" and tool_c2.content == "2"
