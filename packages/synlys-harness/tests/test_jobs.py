"""后台任务状态机单测。"""
from synlys_harness.jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    can_transition,
    is_terminal,
)


def test_terminal_and_active_partition():
    """终态与活跃态互补且不重叠（覆盖全部状态）。"""
    assert TERMINAL_STATUSES | ACTIVE_STATUSES == set(JobStatus)
    assert not (TERMINAL_STATUSES & ACTIVE_STATUSES)


def test_terminal_predicate():
    """三个终态的 is_terminal 为真，两个活跃态为假。"""
    assert is_terminal(JobStatus.COMPLETED)
    assert is_terminal(JobStatus.FAILED)
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.PENDING)
    assert not is_terminal(JobStatus.RUNNING)


def test_active_to_terminal_allowed():
    """活跃态可以走向任一终态。"""
    for src in (JobStatus.PENDING, JobStatus.RUNNING):
        for dst in TERMINAL_STATUSES:
            assert can_transition(src, dst), f"{src} -> {dst} 应允许"


def test_pending_to_running_allowed():
    """排队 → 执行中允许。"""
    assert can_transition(JobStatus.PENDING, JobStatus.RUNNING)


def test_terminal_is_frozen():
    """终态不可再流转（重复置同一终态视为幂等合法）。"""
    for src in TERMINAL_STATUSES:
        for dst in JobStatus:
            if dst == src:
                assert can_transition(src, dst), "同状态应幂等允许"
            else:
                assert not can_transition(src, dst), f"{src} -> {dst} 不应允许"


def test_running_cannot_go_back_to_pending():
    """执行中不可回退到排队（防轮询抖动导致状态倒退）。"""
    assert not can_transition(JobStatus.RUNNING, JobStatus.PENDING)


def test_status_value_is_string():
    """状态值即持久化字符串（DB 里存小写短横线形式）。"""
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.COMPLETED.value == "completed"
