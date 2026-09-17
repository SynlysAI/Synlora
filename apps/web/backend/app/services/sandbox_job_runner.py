"""单进程沙箱后台任务句柄与执行生命周期。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from synlys_harness import ExecutionRequest, ToolResult

_LOGGER = logging.getLogger(__name__)

RunningCallback = Callable[[str], Awaitable[None]]
FinishCallback = Callable[..., Awaitable[None]]


class SandboxJobRunner:
    """持有后台执行任务并负责取消与停机清理。"""

    def __init__(
        self,
        executor,
        mark_running: RunningCallback,
        finish: FinishCallback,
    ) -> None:
        """保存执行器和状态回调。"""
        self._executor = executor
        self._mark_running = mark_running
        self._finish = finish
        self._tasks: dict[str, asyncio.Task] = {}
        self._accepting = True

    def start(self, job_id: str, request: ExecutionRequest) -> None:
        """同步登记后台任务句柄并立即返回。"""
        if not self._accepting:
            raise RuntimeError("后台任务运行器正在关闭")
        if job_id in self._tasks:
            raise RuntimeError(f"后台任务已登记: {job_id}")
        task = asyncio.create_task(self._run(job_id, request))
        self._tasks[job_id] = task
        task.add_done_callback(lambda done, key=job_id: self._consume(key, done))

    async def _run(self, job_id: str, request: ExecutionRequest) -> None:
        """推进 running、执行并收敛终态。"""
        try:
            await self._mark_running(job_id)
            result = await self._executor.execute(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 后台异常必须落任务终态
            result = ToolResult(
                ok=False,
                content=f"后台执行出错: {exc}",
                error="execution_failed",
            )
        await self._finish(job_id, result, cancelled=False)

    def _consume(self, job_id: str, task: asyncio.Task) -> None:
        """摘除已完成句柄并消费异常。"""
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.exception("后台任务 runner 异常 job=%s", job_id)

    def is_running(self, job_id: str) -> bool:
        """任务是否仍由当前进程持有。"""
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    async def wait(self, job_id: str) -> None:
        """等待测试或停机路径中的指定任务收尾。"""
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)

    async def cancel(self, job_id: str) -> bool:
        """取消任务并等待执行器确认资源清理。"""
        task = self._tasks.get(job_id)
        if task is None:
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        stopped = await self._executor.cleanup_execution(job_id)
        if stopped:
            await self._finish(
                job_id,
                ToolResult(ok=False, content="任务已取消", error="cancelled"),
                cancelled=True,
            )
        return stopped

    async def shutdown(self) -> None:
        """停止接收并取消收尾全部持有任务。"""
        self._accepting = False
        for job_id in list(self._tasks):
            await self.cancel(job_id)
