"""Plane 会话兼容层数据访问：稳定 run 映射与 durable 会话事件。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import PlaneCompatRun, PlaneSessionEvent


class PlaneCompatRunRepository:
    """封装 v1 run 映射的查询与写入。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_by_v1_run_id(self, v1_run_id: str) -> PlaneCompatRun | None:
        """按对外稳定 run id 精确读取。"""
        result = await self.db_session.execute(select(PlaneCompatRun).where(PlaneCompatRun.v1_run_id == v1_run_id))
        return result.scalar_one_or_none()

    async def get_by_external_run_id(self, external_run_id: str) -> PlaneCompatRun | None:
        """按 Plane 对账键读取（幂等重放）。"""
        result = await self.db_session.execute(
            select(PlaneCompatRun).where(PlaneCompatRun.external_run_id == external_run_id)
        )
        return result.scalar_one_or_none()

    async def list_for_thread(self, thread_id: str) -> list[PlaneCompatRun]:
        """按会话时间序列出兼容 run。"""
        result = await self.db_session.execute(
            select(PlaneCompatRun).where(PlaneCompatRun.thread_id == thread_id).order_by(PlaneCompatRun.id.asc())
        )
        return list(result.scalars().all())

    async def list_active_for_projection(self) -> list[PlaneCompatRun]:
        """列出可能仍在产生事件的映射（含内部 run 为空与非终态）。"""
        result = await self.db_session.execute(select(PlaneCompatRun).where(PlaneCompatRun.current_run_id.isnot(None)))
        return list(result.scalars().all())

    async def create(self, values: dict[str, Any]) -> PlaneCompatRun:
        """新建 run 映射。"""
        row = PlaneCompatRun(**values)
        self.db_session.add(row)
        await self.db_session.flush()
        return row

    async def set_current_run_id(self, row: PlaneCompatRun, run_id: str) -> None:
        """审批 resume 后更新当前内部 run。"""
        row.current_run_id = run_id
        await self.db_session.flush()


class PlaneSessionEventRepository:
    """封装 durable 会话事件的追加与回放。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def append(
        self,
        *,
        thread_id: str,
        seq: int,
        event_type: str,
        run_id: str,
        payload: dict[str, Any],
        source_key: str | None = None,
    ) -> PlaneSessionEvent:
        """按已分配的会话级 seq 追加事件。"""
        row = PlaneSessionEvent(
            thread_id=thread_id,
            seq=seq,
            event_type=event_type,
            run_id=run_id,
            source_key=source_key,
            payload=payload,
        )
        self.db_session.add(row)
        await self.db_session.flush()
        return row

    async def source_exists(self, thread_id: str, source_key: str) -> bool:
        """判断某源键事件是否已落库（并发投影幂等判定）。"""
        result = await self.db_session.execute(
            select(PlaneSessionEvent.id).where(
                PlaneSessionEvent.thread_id == thread_id,
                PlaneSessionEvent.source_key == source_key,
            )
        )
        return result.scalar_one_or_none() is not None

    async def events_after(self, thread_id: str, after_seq: int, limit: int = 500) -> list[PlaneSessionEvent]:
        """按 seq 升序回放 after_seq 之后的事件。"""
        result = await self.db_session.execute(
            select(PlaneSessionEvent)
            .where(PlaneSessionEvent.thread_id == thread_id, PlaneSessionEvent.seq > after_seq)
            .order_by(PlaneSessionEvent.seq.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def max_seq(self, thread_id: str) -> int:
        """读取会话当前最大 seq（无事件返回 -1）。"""
        result = await self.db_session.execute(
            select(func.max(PlaneSessionEvent.seq)).where(PlaneSessionEvent.thread_id == thread_id)
        )
        value = result.scalar()
        return value if value is not None else -1

    async def event_counts(self, thread_id: str) -> dict[str, int]:
        """按 v1 run id 统计事件数。"""
        result = await self.db_session.execute(
            select(PlaneSessionEvent.run_id, func.count(PlaneSessionEvent.id))
            .where(PlaneSessionEvent.thread_id == thread_id)
            .group_by(PlaneSessionEvent.run_id)
        )
        return {str(run_id): int(count) for run_id, count in result.all()}
