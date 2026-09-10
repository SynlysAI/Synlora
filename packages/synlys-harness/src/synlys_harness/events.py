"""事件日志：seq 分配与 sinks 广播。"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from .types import EventType, SessionEvent

EventSink = Callable[[SessionEvent], Awaitable[None]]


class EventLog:
    """单会话事件日志（内存态；持久化由宿主通过 sink 实现）。"""

    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        """初始化事件日志。

        Args:
            sinks: 事件广播目标（可为 None，宿主后续也可不接）。
        """
        self._events: list[SessionEvent] = []
        self._sinks = sinks or []

    @property
    def events(self) -> list[SessionEvent]:
        """返回全部事件的只读副本。"""
        return list(self._events)

    def last(self) -> SessionEvent | None:
        """返回最新事件（空日志返回 None）。"""
        return self._events[-1] if self._events else None

    def seed(self, events: list[SessionEvent]) -> None:
        """用历史事件预填充日志（跨轮恢复 seq 与上下文）。

        Args:
            events: 历史事件（须按 seq 升序且从 0 连续）。

        Raises:
            ValueError: seq 不连续或非升序。
        """
        for i, ev in enumerate(events):
            if ev.seq != i:
                raise ValueError(f"历史事件 seq 不连续: 期望 {i} 实际 {ev.seq}")
        self._events = list(events)

    async def append(self, type_: EventType, payload: dict[str, Any]) -> SessionEvent:
        """追加事件并广播到所有 sink。

        Args:
            type_: 事件类型。
            payload: 事件负载。

        Returns:
            构造完成的 SessionEvent（seq 已分配）。
        """
        event = SessionEvent(
            seq=len(self._events), type=type_, payload=dict(payload), ts=time.time()
        )
        self._events.append(event)
        for sink in self._sinks:
            await sink(event)
        return event
