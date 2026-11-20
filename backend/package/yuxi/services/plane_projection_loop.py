"""Plane 会话事件后台投影循环。

读端点的懒投影依赖客户端轮询；本循环兜底在 run 终态后主动补投影，避免
Redis run 事件流（TTL 2 小时）过期后 durable 事件缺失。无 plane 会话时
每轮仅一次空查询。
"""

from __future__ import annotations

import asyncio

from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

from datetime import timedelta

POLL_SECONDS = 5.0
# run 终态后仍保留的补投影窗口
RECENT_WINDOW = timedelta(minutes=10)


async def _project_once() -> None:
    """把活跃或刚结束的 plane run 的新事件投影为 durable v1 事件。"""
    from sqlalchemy import select

    from yuxi.services.plane_session_compat_service import PlaneSessionCompatService
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import AgentRun, PlaneCompatRun

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(PlaneCompatRun.thread_id)
            .join(AgentRun, AgentRun.id == PlaneCompatRun.current_run_id)
            .where(
                (AgentRun.finished_at.is_(None))
                | (AgentRun.finished_at >= utc_now_naive() - RECENT_WINDOW)
            )
            .limit(200)
        )
        thread_ids = sorted({row for row in result.scalars()})
        if not thread_ids:
            return
        service = PlaneSessionCompatService(db)
        for thread_id in thread_ids:
            try:
                await service.project_pending_events(thread_id)
            except Exception as exc:
                logger.warning(f"plane projection failed: thread={thread_id} type={type(exc).__name__}")


async def plane_projection_loop() -> None:
    """常驻循环：周期性投影，单轮失败不中断。"""
    while True:
        try:
            await _project_once()
        except Exception as exc:
            logger.warning(f"plane projection loop iteration failed: type={type(exc).__name__}")
        await asyncio.sleep(POLL_SECONDS)


async def start_plane_projection_loop(app) -> None:
    """启动后台投影任务（幂等）。"""
    if getattr(app.state, "plane_projection_task", None) is not None:
        return
    app.state.plane_projection_task = asyncio.create_task(plane_projection_loop())
    logger.info("plane session projection loop started")


async def stop_plane_projection_loop(app) -> None:
    """停止后台投影任务。"""
    task = getattr(app.state, "plane_projection_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.state.plane_projection_task = None
    logger.info("plane session projection loop stopped")
