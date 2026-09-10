"""事件流 → LLM 消息投影（DSH foldSurface 思想）。"""
from __future__ import annotations

from .types import EventType, Message, Role, SessionEvent, ToolCall


def derive_messages(events: list[SessionEvent], include_system: bool = True) -> list[Message]:
    """把会话事件流投影为 LLM 消息序列。

    连续出现的 TOOL_CALL 事件（中间无其他投影事件）聚合为一条
    tool_calls=[c1, c2] 的 assistant 消息，content 取组内第一个非 None
    的 content；满足 OpenAI 兼容 API"带 tool_calls 的 assistant 消息必须
    紧跟其 tool 响应"的顺序要求。交错顺序（call→result→call→result）
    各自独立成条，行为不变。

    Args:
        events: 会话全部事件（按 seq 升序）。
        include_system: 是否把 turn/start 中的 system_prompt 投影为首条 system 消息。

    Returns:
        可直接投喂 LLM 的 Message 列表。
    """
    messages: list[Message] = []
    system_emitted = False
    pending_calls: list[ToolCall] = []
    pending_content: str | None = None

    def flush_calls() -> None:
        """把缓冲中的连续 TOOL_CALL 聚合为一条 assistant 消息。"""
        nonlocal pending_content
        if pending_calls:
            messages.append(Message(
                role=Role.ASSISTANT,
                content=pending_content,
                tool_calls=list(pending_calls),
            ))
            pending_calls.clear()
            pending_content = None

    for ev in events:
        p = ev.payload
        if ev.type is EventType.TURN_START:
            flush_calls()
            if include_system and not system_emitted:
                prompt = p.get("system_prompt")
                if prompt:
                    messages.append(Message(role=Role.SYSTEM, content=prompt))
                    system_emitted = True
        elif ev.type is EventType.USER_MESSAGE:
            flush_calls()
            messages.append(Message(role=Role.USER, content=p.get("text") or ""))
        elif ev.type is EventType.ASSISTANT_MESSAGE:
            flush_calls()
            messages.append(Message(role=Role.ASSISTANT, content=p.get("content")))
        elif ev.type is EventType.TOOL_CALL:
            tc = p.get("tool_call", {})
            pending_calls.append(ToolCall(
                id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", {}),
            ))
            if pending_content is None and p.get("content") is not None:
                pending_content = p.get("content")
        elif ev.type is EventType.TOOL_RESULT:
            flush_calls()
            messages.append(Message(
                role=Role.TOOL,
                content=p.get("content", ""),
                tool_call_id=p.get("tool_call_id", ""),
                name=p.get("name", ""),
            ))
    flush_calls()
    return messages
