"""事件流 → LLM 消息投影（DSH foldSurface 思想）。"""
from __future__ import annotations

from .types import EventType, Message, Role, SessionEvent, ToolCall


def derive_messages(events: list[SessionEvent], include_system: bool = True) -> list[Message]:
    """把会话事件流投影为 LLM 消息序列。

    Args:
        events: 会话全部事件（按 seq 升序）。
        include_system: 是否把 turn/start 中的 system_prompt 投影为首条 system 消息。

    Returns:
        可直接投喂 LLM 的 Message 列表。
    """
    messages: list[Message] = []
    system_emitted = False
    for ev in events:
        p = ev.payload
        if ev.type is EventType.TURN_START and include_system and not system_emitted:
            prompt = p.get("system_prompt")
            if prompt:
                messages.append(Message(role=Role.SYSTEM, content=prompt))
                system_emitted = True
        elif ev.type is EventType.USER_MESSAGE:
            messages.append(Message(role=Role.USER, content=p.get("text", "")))
        elif ev.type is EventType.ASSISTANT_MESSAGE:
            messages.append(Message(role=Role.ASSISTANT, content=p.get("content")))
        elif ev.type is EventType.TOOL_CALL:
            tc = p.get("tool_call", {})
            messages.append(Message(
                role=Role.ASSISTANT,
                content=p.get("content"),
                tool_calls=[ToolCall(
                    id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", {}),
                )],
            ))
        elif ev.type is EventType.TOOL_RESULT:
            messages.append(Message(
                role=Role.TOOL,
                content=p.get("content", ""),
                tool_call_id=p.get("tool_call_id", ""),
                name=p.get("name", ""),
            ))
    return messages
