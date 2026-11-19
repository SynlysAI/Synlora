"""Plane 账户绑定数据访问层。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import PlaneAccountLink


class PlaneAccountLinkRepository:
    """封装 Plane 账户绑定的查询与写入。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def list_for_plane_user(self, plane_user_id: str) -> list[PlaneAccountLink]:
        """列出同一 Plane 用户的全量绑定记录（repair 与自服务可并存）。"""
        result = await self.db_session.execute(
            select(PlaneAccountLink)
            .where(PlaneAccountLink.plane_user_id == plane_user_id)
            .order_by(PlaneAccountLink.id.asc())
        )
        return list(result.scalars().all())

    async def list_for_synlora_user(self, synlora_user_id: int) -> list[PlaneAccountLink]:
        """列出同一本方用户的绑定记录。"""
        result = await self.db_session.execute(
            select(PlaneAccountLink)
            .where(PlaneAccountLink.synlora_user_id == synlora_user_id)
            .order_by(PlaneAccountLink.id.asc())
        )
        return list(result.scalars().all())

    async def get_by_account_link_id(self, account_link_id: str) -> PlaneAccountLink | None:
        """按 Plane 侧生成的绑定 ID 精确读取。"""
        result = await self.db_session.execute(
            select(PlaneAccountLink).where(PlaneAccountLink.account_link_id == account_link_id)
        )
        return result.scalar_one_or_none()

    async def list_for_binding(self, *, plane_workspace_id: str, plane_user_id: str) -> list[PlaneAccountLink]:
        """按 (工作区, Plane 用户) 读取全部绑定记录。"""
        result = await self.db_session.execute(
            select(PlaneAccountLink)
            .where(
                PlaneAccountLink.plane_workspace_id == plane_workspace_id,
                PlaneAccountLink.plane_user_id == plane_user_id,
            )
            .order_by(PlaneAccountLink.id.asc())
        )
        return list(result.scalars().all())

    async def create(self, values: dict[str, Any]) -> PlaneAccountLink:
        """新建绑定记录。"""
        link = PlaneAccountLink(**values)
        self.db_session.add(link)
        await self.db_session.flush()
        return link
