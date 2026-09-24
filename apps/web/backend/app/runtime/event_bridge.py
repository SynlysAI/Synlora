"""会话事件到 JSONL、数据库和 SSE 队列的宿主桥接。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from synlys_harness import EventType, SessionEvent

TRANSIENT = {EventType.LLM_DELTA, EventType.REASONING_DELTA}


def make_event_sinks(
    *,
    session_id: str,
    user_id: str,
    event_repo: Any,
    jsonl_path: Path,
    queue,
    broadcast=None,
) -> list:
    """构造保持既有吞错与瞬态过滤语义的事件 sinks。

    Args:
        session_id: 当前会话 ID。
        user_id: 当前用户 ID，保留用于显式接线和未来审计。
        event_repo: 会话事件仓储。
        jsonl_path: JSONL 审计副本路径。
        queue: SSE 内存队列。
        broadcast: 会话级扇出回调（可选，签名 (SessionEvent) -> None）：
            与 queue 同语义投递（含瞬态事件），供前端常驻订阅端点使用。

    Returns:
        可传给 EventLog 的异步 sink 列表。
    """
    del user_id
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    async def jsonl_sink(event: SessionEvent) -> None:
        if event.type in TRANSIENT:
            return
        try:
            with jsonl_path.open("a", encoding="utf-8") as stream:
                stream.write(event.model_dump_json() + "\n")
        except OSError:
            pass

    async def db_sink(event: SessionEvent) -> None:
        if event.type not in TRANSIENT:
            try:
                await event_repo.append(session_id, event)
            except Exception:
                pass
        try:
            queue.put_nowait(event)
        except Exception:
            pass
        if broadcast is not None:
            try:
                broadcast(event)
            except Exception:
                pass

    return [jsonl_sink, db_sink]
