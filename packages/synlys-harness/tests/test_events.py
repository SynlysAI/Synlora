"""EventLog 单测。"""
import pytest

from synlys_harness.events import EventLog
from synlys_harness.types import EventType, SessionEvent


async def test_append_assigns_seq():
    """seq 从 0 开始单调递增。"""
    log = EventLog()
    ev1 = await log.append(EventType.TURN_START, {})
    ev2 = await log.append(EventType.USER_MESSAGE, {"text": "hi"})
    assert (ev1.seq, ev2.seq) == (0, 1)
    assert ev1.ts > 0


async def test_sinks_receive_events():
    """append 同步广播到所有 sink。"""
    received = []

    async def sink(ev):
        received.append(ev)

    log = EventLog(sinks=[sink])
    await log.append(EventType.USER_MESSAGE, {"text": "a"})
    await log.append(EventType.LLM_DELTA, {"text": "b"})
    assert [e.type for e in received] == [EventType.USER_MESSAGE, EventType.LLM_DELTA]


async def test_events_view():
    """events 只读视图返回全部事件。"""
    log = EventLog()
    await log.append(EventType.TURN_START, {})
    await log.append(EventType.TURN_END, {})
    assert [e.seq for e in log.events] == [0, 1]


async def test_last_returns_latest_event():
    """空日志返回 None；append 后返回最新事件本身。"""
    log = EventLog()
    assert log.last() is None
    await log.append(EventType.TURN_START, {})
    ev = await log.append(EventType.USER_MESSAGE, {"text": "hi"})
    assert log.last() is ev


async def test_append_copies_payload():
    """append 后修改调用方 dict 不影响已入账事件（事件是唯一事实源）。"""
    log = EventLog()
    payload = {"text": "原始"}
    await log.append(EventType.USER_MESSAGE, payload)
    payload["text"] = "被篡改"
    assert log.events[0].payload["text"] == "原始"


def _history_event(seq: int) -> SessionEvent:
    """构造历史事件辅助。

    Args:
        seq: 事件序号。

    Returns:
        指定 seq 的 SessionEvent。
    """
    return SessionEvent(seq=seq, type=EventType.USER_MESSAGE, payload={"text": f"m{seq}"}, ts=1.0 + seq)


def test_seed_restores_events_view():
    """seed 3 条历史后 events 视图按序完整返回全部历史事件。"""
    log = EventLog()
    log.seed([_history_event(i) for i in range(3)])
    assert [e.seq for e in log.events] == [0, 1, 2]
    assert log.last() is not None and log.last().seq == 2


async def test_seed_then_append_assigns_next_seq():
    """seed 历史后 append：seq 从 len(events) 续接（跨轮恢复 seq 连续性）。"""
    log = EventLog()
    log.seed([_history_event(i) for i in range(3)])
    ev = await log.append(EventType.TURN_END, {})
    assert ev.seq == 3
    assert [e.seq for e in log.events] == [0, 1, 2, 3]
    assert log.events[:3] == [_history_event(i) for i in range(3)]


def test_seed_rejects_non_contiguous_seq():
    """seed 校验 seq 必须从 0 升序连续：乱序/跳号抛 ValueError。"""
    log = EventLog()
    with pytest.raises(ValueError, match="不连续"):
        log.seed([_history_event(0), _history_event(2)])
    with pytest.raises(ValueError, match="不连续"):
        log.seed([_history_event(1), _history_event(0)])
    with pytest.raises(ValueError, match="不连续"):
        log.seed([_history_event(1)])


def test_seed_empty_keeps_log_clean():
    """seed 空列表等价新日志（宿主空历史可直接调用）。"""
    log = EventLog()
    log.seed([])
    assert log.events == []
    assert log.last() is None
