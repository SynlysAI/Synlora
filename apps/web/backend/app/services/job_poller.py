"""后台任务轮询器：定时把未完成任务的状态从外部系统同步回来。

设计取舍：agent 侧"免轮询"（提交后不占 step），但进程内必须有人定期问外部
系统——本类就是这个角色。轮询间隔取秒级而非事件驱动，是因为多数子平台
（Spec_Agent 等）不提供完成回调；将来若有平台支持 webhook，可另加事件入口，
本循环仍作为兜底。

单实例前提：本循环是进程内 asyncio task，依赖部署侧的 workers=1 约束
（多副本会导致同一任务被重复轮询与重复唤醒）。
"""
from __future__ import annotations

import asyncio
import logging

from app.services.job_service import JobService

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 5.0


class JobPoller:
    """未完成任务的状态轮询循环。"""

    def __init__(self, service: JobService,
                 interval_s: float = DEFAULT_INTERVAL_S) -> None:
        """初始化。

        Args:
            service: 任务服务（提供 list_active / refresh）。
            interval_s: 轮询间隔（秒）。
        """
        self._service = service
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        """循环是否在跑。"""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """启动后台循环（重复调用无副作用）。"""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop())
        _LOGGER.info("任务轮询器已启动（间隔 %.1fs）", self._interval_s)

    async def stop(self) -> None:
        """停止后台循环并等待收尾。"""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _LOGGER.info("任务轮询器已停止")

    async def _loop(self) -> None:
        """定时 tick；单轮异常只记日志，不退出循环。

        周期为「tick 耗时 + interval」：tick 内的外部调用会叠加到间隔上；
        当前 tick 只做 DB 往返，漂移可忽略。
        """
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 存储故障等不得打挂轮询
                _LOGGER.warning("任务轮询失败，下轮重试", exc_info=True)
            await asyncio.sleep(self._interval_s)

    async def tick(self) -> None:
        """执行一轮：逐个刷新未完成任务（单个失败不影响其余）。"""
        for doc in await self._service.list_active():
            try:
                await self._service.refresh(doc)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 同上
                _LOGGER.warning("刷新任务失败 job=%s", doc.get("_id"), exc_info=True)
