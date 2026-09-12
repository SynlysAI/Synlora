"""上下文自动压缩（参考 pi harness/compaction 与 DSH packages/compaction）。

机制：LLM 调用真实返回的 usage.prompt_tokens 是最可靠的"当前上下文多大"信号
（优于任何字符估算），超过阈值时把**早期历史**交给 LLM 摘要成一条压缩消息，
近期历史保留原文（retained tail）。压缩结果落一条 session/compaction 事件
（summary + until_seq），下一轮从 DB 事件回放时直接复用，不会重复摘要。

切分边界固定在 user/message 事件上：保证压缩后的消息序列以 user 开头，
不会出现 tool 响应与 tool_call 被切开导致 OpenAI 兼容 API 报序列非法。
"""
from __future__ import annotations

import contextlib

from .events import EventLog
from .models.backend import LLMBackend
from .session import derive_messages
from .types import EventType, Message, Role

# 摘要指令：强调保留可执行的事实（路径/数值/决定），而不是文风
_SUMMARY_PROMPT = (
    "请把以下对话历史压缩成一份要点摘要，供后续对话作为上下文使用。要求：\n"
    "1. 保留所有关键事实：用户的原始需求与后续修正、已做出的决定与理由、"
    "重要数据/数值/结论、涉及的文件路径与工具调用要点；\n"
    "2. 丢弃寒暄、重复表述与失败的尝试细节（只保留失败结论）；\n"
    "3. 用简洁的条目式中文输出，不超过 600 字。\n\n=== 对话历史 ===\n"
)

# 喂给摘要模型的单条消息内容上限（超长工具输出截断，控制摘要成本）
_PER_MESSAGE_CAP = 1500


def _render_for_summary(messages: list[Message]) -> str:
    """把待压缩消息渲染为摘要输入文本（单条截断 + 总量封顶）。

    Args:
        messages: 早期历史消息（不含 system）。

    Returns:
        摘要提示词完整文本。
    """
    lines: list[str] = []
    for m in messages:
        role = m.role.value
        content = (m.content or "").strip()
        if m.role is Role.ASSISTANT and m.tool_calls:
            calls = ", ".join(tc.name for tc in m.tool_calls)
            content = f"[调用工具: {calls}] {content}".strip()
        if len(content) > _PER_MESSAGE_CAP:
            content = content[:_PER_MESSAGE_CAP] + "…(截断)"
        lines.append(f"[{role}] {content}" if content else f"[{role}] (空)")
    return _SUMMARY_PROMPT + "\n".join(lines)


class Compactor:
    """上下文压缩器：阈值触发、摘要复用、边界安全切分。"""

    def __init__(self, backend: LLMBackend, log: EventLog, *,
                 threshold_tokens: int, keep_chars: int) -> None:
        """初始化压缩器。

        Args:
            backend: LLM 后端（复用会话的模型做摘要）。
            log: 事件日志（写 session/compaction 事件）。
            threshold_tokens: 触发阈值（prompt_tokens 口径）。
            keep_chars: 保留尾部的字符预算。
        """
        self._backend = backend
        self._log = log
        self._threshold = threshold_tokens
        self._keep_chars = keep_chars

    def _last_compaction(self) -> tuple[str, int] | None:
        """找最近一次压缩标记。

        Returns:
            (summary, until_seq)；无标记返回 None。
        """
        for ev in reversed(self._log.events):
            if ev.type is EventType.SESSION_COMPACTION:
                return str(ev.payload.get("summary", "")), int(ev.payload.get("until_seq", -1))
        return None

    async def _summarize(self, messages: list[Message]) -> str:
        """调 LLM 生成摘要文本。

        Args:
            messages: 待压缩的早期历史消息。

        Returns:
            摘要文本；LLM 失败时返回空串（调用方跳过本次压缩）。
        """
        prompt = _render_for_summary(messages)
        parts: list[str] = []
        try:
            async with contextlib.aclosing(self._backend.stream(
                [Message(role=Role.USER, content=prompt)], None,
            )) as stream:
                async for ev in stream:
                    text = getattr(ev, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
        except Exception:
            return ""
        return "".join(parts).strip()

    def _boundary_seq(self) -> int | None:
        """选压缩切分边界：最近的 user/message 事件 seq，且其后历史不超过尾部预算。

        Returns:
            边界事件 seq（该事件**保留**在尾部，从此事件起重投）；无合适边界返回 None。
        """
        user_seqs = [ev.seq for ev in self._log.events
                     if ev.type is EventType.USER_MESSAGE]
        for seq in reversed(user_seqs[1:] or user_seqs):  # 至少保留最后一个 user 轮
            after = [ev for ev in self._log.events if ev.seq >= seq]
            tail = derive_messages(after, include_system=False)
            size = sum(len(m.content or "") for m in tail)
            if size <= self._keep_chars:
                return seq
        return None

    async def apply(self, messages: list[Message], last_prompt_tokens: int | None,
                    ) -> list[Message]:
        """按需压缩消息序列（不含首条 system 消息）。

        Args:
            messages: derive_messages 投影出的历史消息（不含 system）。
            last_prompt_tokens: 上一次 LLM 调用真实返回的 prompt_tokens
                （None 表示尚无信号，跳过判断）。

        Returns:
            压缩后的历史消息（未触发压缩时原样返回）。
        """
        if self._threshold <= 0:
            return messages
        if last_prompt_tokens is None or last_prompt_tokens <= self._threshold:
            return messages

        # 既有压缩标记：先展开为"摘要 + 标记后原文"，展开后若已回到阈值内
        # 则直接复用（prompt_tokens 是上一次调用的滞后信号，展开量才是现状）
        marker = self._last_compaction()
        if marker is not None:
            summary, until_seq = marker
            tail = derive_messages(
                [ev for ev in self._log.events if ev.seq > until_seq],
                include_system=False,
            )
            messages = [Message(role=Role.USER, content=f"（前文历史摘要）\n{summary}")] + tail
            expanded_chars = sum(len(m.content or "") for m in messages)
            if expanded_chars // 2 <= self._threshold:  # 同 _estimate_tokens 口径
                return messages

        boundary = self._boundary_seq()
        if boundary is None:
            return messages
        old = derive_messages(
            [ev for ev in self._log.events if ev.seq < boundary], include_system=False,
        )
        if not old:
            return messages
        summary = await self._summarize(old)
        if not summary:
            return messages  # 摘要失败（LLM 异常）：宁可超额也不再抛错打断对话
        tail = derive_messages(
            [ev for ev in self._log.events if ev.seq >= boundary], include_system=False,
        )
        await self._log.append(EventType.SESSION_COMPACTION, {
            "summary": summary, "until_seq": boundary,
            "prompt_tokens_before": last_prompt_tokens,
        })
        return [Message(role=Role.USER, content=f"（前文历史摘要）\n{summary}")] + tail
