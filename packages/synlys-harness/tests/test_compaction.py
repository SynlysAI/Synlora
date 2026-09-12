"""上下文自动压缩单测（Compactor）。"""
from synlys_harness.compaction import Compactor
from synlys_harness.events import EventLog
from synlys_harness.models.backend import TextDelta
from synlys_harness.session import derive_messages
from synlys_harness.types import EventType, Role


class SummaryBackend:
    """摘要用假后端：记录调用并返回固定摘要文本。"""

    def __init__(self, summary: str = "早期历史要点：用户要做 X。"):
        """初始化。

        Args:
            summary: 摘要调用的返回文本。
        """
        self.summary = summary
        self.calls: list[list[Message]] = []

    async def stream(self, messages, tools=None):
        """记录调用并产出摘要文本。"""
        self.calls.append(messages)
        yield TextDelta(text=self.summary)


async def _seed_history(log: EventLog, turns: int) -> None:
    """写入多轮 user/assistant 事件。

    Args:
        log: 事件日志。
        turns: 轮数（每轮 user + assistant 各一条）。
    """
    await log.append(EventType.TURN_START, {"system_prompt": "p"})
    for i in range(turns):
        await log.append(EventType.USER_MESSAGE, {"text": f"第{i}轮问题" + "长" * 200})
        await log.append(EventType.ASSISTANT_MESSAGE, {"content": f"第{i}轮回答" + "文" * 200})


def _compactor(backend, log, threshold=1000, keep_chars=100000) -> Compactor:
    """构造测试压缩器。"""
    return Compactor(backend, log, threshold_tokens=threshold, keep_chars=keep_chars)


async def test_no_trigger_below_threshold():
    """无 usage 信号或未超阈值：原样返回，不摘要。"""
    log = EventLog()
    await _seed_history(log, turns=3)
    backend = SummaryBackend()
    comp = _compactor(backend, log)
    messages = derive_messages(log.events, include_system=False)
    out_none = await comp.apply(messages, None)
    out_low = await comp.apply(messages, 10)
    assert out_none is messages and out_low is messages
    assert backend.calls == []


async def test_disabled_when_threshold_zero():
    """threshold=0：显式关闭压缩。"""
    log = EventLog()
    await _seed_history(log, turns=3)
    backend = SummaryBackend()
    comp = _compactor(backend, log, threshold=0)
    messages = derive_messages(log.events, include_system=False)
    out = await comp.apply(messages, 99999)
    assert out is messages and backend.calls == []


async def test_compaction_replaces_old_history():
    """超阈值：早期历史被摘要消息替换，尾部保留原文，事件落账。"""
    log = EventLog()
    await _seed_history(log, turns=4)
    backend = SummaryBackend()
    comp = _compactor(backend, log, threshold=1000, keep_chars=1000)
    messages = derive_messages(log.events, include_system=False)
    out = await comp.apply(messages, 5000)
    assert len(backend.calls) == 1  # 恰好一次摘要调用
    assert out[0].role is Role.USER and "前文历史摘要" in out[0].content
    assert "早期历史要点" in out[0].content
    # 尾部保留最近的原文（最后一轮的问题还在）
    assert any("第3轮问题" in (m.content or "") for m in out)
    markers = [e for e in log.events if e.type is EventType.SESSION_COMPACTION]
    assert len(markers) == 1
    assert markers[0].payload["summary"] == backend.summary
    assert markers[0].payload["until_seq"] > 0
    # 摘要消息数显著少于原始投影
    assert len(out) < len(messages)


async def test_marker_reused_without_resummarize():
    """已有压缩标记：展开后未再超阈值时直接复用摘要，不二次摘要。"""
    log = EventLog()
    await _seed_history(log, turns=4)
    backend = SummaryBackend()
    comp = _compactor(backend, log, threshold=1000, keep_chars=1000)
    first = await comp.apply(derive_messages(log.events, include_system=False), 5000)
    assert len(backend.calls) == 1
    # 新增一轮对话（尾部增长），再次用滞后的超阈信号触发：
    # 展开为"摘要+新尾部"后已回到阈值内 → 不再调 LLM 摘要
    await log.append(EventType.USER_MESSAGE, {"text": "新问题"})
    await log.append(EventType.ASSISTANT_MESSAGE, {"content": "新回答"})
    fresh = derive_messages(log.events, include_system=False)
    second = await comp.apply(fresh, 5000)
    assert len(backend.calls) == 1  # 未再触发摘要
    assert second[0].role is Role.USER and "前文历史摘要" in second[0].content
    assert any(m.content == "新问题" for m in second)
    markers = [e for e in log.events if e.type is EventType.SESSION_COMPACTION]
    assert len(markers) == 1  # 没有新增压缩标记


async def test_tail_boundary_is_user_message():
    """压缩后尾部以 user 消息开头（保证 tool_call/result 不被切开）。"""
    log = EventLog()
    await _seed_history(log, turns=4)
    # 最后一轮后追加 tool 序列，验证边界不会切进 call/result 对
    await log.append(EventType.TOOL_CALL, {
        "tool_call": {"id": "c1", "name": "python.run", "arguments": {}},
    })
    await log.append(EventType.TOOL_RESULT, {
        "tool_call_id": "c1", "name": "python.run", "ok": True, "content": "ok",
    })
    backend = SummaryBackend()
    comp = _compactor(backend, log, threshold=1000, keep_chars=100000)
    messages = derive_messages(log.events, include_system=False)
    out = await comp.apply(messages, 5000)
    marker = [e for e in log.events if e.type is EventType.SESSION_COMPACTION][0]
    tail_events = [e for e in log.events if e.seq >= marker.payload["until_seq"]]
    assert tail_events[0].type is EventType.USER_MESSAGE
    # 尾部投影里 tool 响应对完整（c1 有对应 tool 消息）
    tail = [m for m in out[1:]]
    assert any(m.role is Role.TOOL and m.tool_call_id == "c1" for m in tail)
