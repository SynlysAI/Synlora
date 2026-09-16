"""后台任务（Job）状态机：异步长任务的统一生命周期定义。

机制归 harness、存储归宿主：本模块只定义状态集合与合法流转；任务文档的
持久化、外部系统对接与完成唤醒由宿主实现（app/services/job_service.py）。
"""
from __future__ import annotations

import enum


class JobStatus(str, enum.Enum):
    """任务生命周期状态（Connector 负责把外部系统状态映射到其中一种）。"""

    PENDING = "pending"      # 已提交，外部系统排队中
    RUNNING = "running"      # 外部系统执行中
    COMPLETED = "completed"  # 成功完成
    FAILED = "failed"        # 执行失败
    CANCELLED = "cancelled"  # 已取消


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
})
ACTIVE_STATUSES = frozenset({JobStatus.PENDING, JobStatus.RUNNING})

# 合法流转表：终态无出边；同状态在 can_transition 里单独放行（幂等）
_ALLOWED: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, *TERMINAL_STATUSES}),
    JobStatus.RUNNING: frozenset(TERMINAL_STATUSES),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def can_transition(src: JobStatus, dst: JobStatus) -> bool:
    """判断状态流转是否合法。

    Args:
        src: 当前状态。
        dst: 目标状态。

    Returns:
        同状态视为幂等合法；终态不可迁移到任何其他状态。

    Note:
        查询失败/超时导致的"状态未知"不得写入本表——调用方应保持原状态，
        避免把一次网络抖动变成状态倒退。
    """
    if src is dst:
        return True
    return dst in _ALLOWED.get(src, frozenset())


def is_terminal(status: JobStatus) -> bool:
    """是否终态（终态任务不再轮询、不再唤醒）。"""
    return status in TERMINAL_STATUSES
