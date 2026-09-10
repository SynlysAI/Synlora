"""derive_messages 投影单测。"""
from synlys_harness.session import derive_messages
from synlys_harness.types import EventType, SessionEvent


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


def test_no_system_when_disabled():
    """include_system=False 时不产出 system 消息。"""
    events = [_ev(0, EventType.TURN_START, {"system_prompt": "x"})]
    assert derive_messages(events, include_system=False) == []
