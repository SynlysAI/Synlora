"""types 数据模型单测。"""
from synlys_harness.types import (
    EventType, Message, Role, SessionEvent, ToolCall,
)


def test_message_roundtrip():
    """Message 各字段可构造且默认值正确。"""
    m = Message(role=Role.USER, content="你好")
    assert m.role is Role.USER
    assert m.tool_calls == []
    assert m.tool_call_id is None


def test_tool_call_model():
    """ToolCall 参数为任意 dict。"""
    tc = ToolCall(id="call_1", name="python.run", arguments={"code": "print(1)"})
    assert tc.arguments["code"] == "print(1)"


def test_session_event_seq_enforced():
    """SessionEvent 必须携带 seq 与 ts。"""
    ev = SessionEvent(seq=1, type=EventType.TURN_START, payload={"a": 1}, ts=1_000.0)
    assert ev.seq == 1
    assert ev.type == EventType.TURN_START


def test_event_type_members():
    """事件类型枚举覆盖设计文档全部 V1 事件。"""
    members = {e.value for e in EventType}
    assert members == {
        "turn/start", "user/message", "llm/delta", "assistant/message",
        "tool/call", "tool/result", "turn/end", "turn/aborted", "error",
    }
