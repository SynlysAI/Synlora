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

    外部取消落在 call 与 result 之间时，事件流会留下无响应的孤儿
    tool/call；投影结束后为其补一条合成 tool 消息（"工具调用未收到
    结果"），否则下轮会产出非法的"assistant(tool_calls) 后无 tool
    响应"序列。

    思考事件（reasoning/delta 与 assistant/reasoning）不投影：思考内容
    只做展示回放，不回投给 LLM（与 DeepSeek 官方多轮语义一致）——下方
    elif 链无对应分支，自然跳过。llm/delta 同理由 assistant/message
    承载最终文本。

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
    # 每条 assistant(tool_calls) 消息的 (messages 内索引, 组内 call id 列表)，供孤儿回补定位
    call_groups: list[tuple[int, list[str]]] = []

    def flush_calls() -> None:
        """把缓冲中的连续 TOOL_CALL 聚合为一条 assistant 消息。"""
        nonlocal pending_content
        if pending_calls:
            messages.append(Message(
                role=Role.ASSISTANT,
                content=pending_content,
                tool_calls=list(pending_calls),
            ))
            call_groups.append((len(messages) - 1, [tc.id for tc in pending_calls]))
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
    _fill_orphan_tool_results(messages, call_groups)
    return messages


def _fill_orphan_tool_results(
    messages: list[Message], call_groups: list[tuple[int, list[str]]],
) -> None:
    """为从未收到 tool/result 的 tool_call 补合成 tool 消息。

    对每条含孤儿 call 的 assistant 消息，在其后插入对应的合成 tool 消息
    （倒序插入避免前组插入使后组索引失效），保证序列满足 OpenAI
    "assistant(tool_calls) 后必须紧跟其全部 tool 响应"的要求。

    Args:
        messages: 投影产出的消息列表（就地修改）。
        call_groups: 每条 assistant(tool_calls) 消息的 (索引, call id 列表)。
    """
    answered = {m.tool_call_id for m in messages if m.role is Role.TOOL}
    for idx, ids in reversed(call_groups):
        orphans = [cid for cid in ids if cid not in answered]
        for offset, cid in enumerate(orphans):
            messages.insert(idx + 1 + offset, Message(
                role=Role.TOOL,
                content="工具调用未收到结果（会话中断）",
                tool_call_id=cid,
            ))
