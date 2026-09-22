"""types 数据模型单测。"""
import pytest
from pydantic import ValidationError

from synlys_harness.types import (
    EventType, Message, Role, SessionEvent, ToolCall,
)
from synlys_harness.types import ResearchContextScope, ToolContext


def test_research_context_is_read_only():
    """工具不能通过赋值扩大 Plane 签发的科研范围。"""
    scope = ResearchContextScope(
        context_id="ctx-1",
        workspace_id="ws-1",
        research_project_id="project-1",
        chain_node_id="node-1",
        context_hash="hash-1",
        visibility_scope="PRIVATE",
    )
    context = ToolContext(user_id="u1", run_id="r1", research_context=scope)
    assert context.research_context.research_project_id == "project-1"
    with pytest.raises(ValidationError):
        context.research_context = scope.model_copy(update={"research_project_id": "other-project"})


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
    """事件类型枚举覆盖设计文档全部 V1 事件（含思考流/压缩/问答回路）。"""
    members = {e.value for e in EventType}
    assert members == {
        "turn/start", "user/message", "llm/delta", "reasoning/delta",
        "assistant/reasoning", "assistant/message",
        "tool/call", "tool/result", "ask/user", "file/send", "session/compaction",
        "turn/end", "turn/aborted", "error",
    }
