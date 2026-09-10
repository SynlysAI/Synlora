"""EventLog 单测。"""
from synlys_harness.events import EventLog
from synlys_harness.types import EventType


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
