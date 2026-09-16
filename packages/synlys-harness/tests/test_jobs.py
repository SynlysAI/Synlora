"""后台任务状态机单测。"""
import json

from synlys_harness.jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    _ALLOWED_TRANSITIONS,
    can_transition,
    is_terminal,
    job_wake_kind,
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
    """状态值即持久化字符串（全小写、与成员名一致）。"""
    for status in JobStatus:
        assert status.value == status.name.lower()
        assert isinstance(status.value, str)


def test_accepts_plain_strings_from_persistence():
    """持久层读回的是裸字符串：同状态幂等与流转判断都要正常工作。"""
    assert can_transition("pending", "pending") is True
    assert can_transition("pending", "running") is True
    assert can_transition("running", "pending") is False
    assert can_transition("completed", "completed") is True
    assert can_transition("completed", "failed") is False

    # 字面量会被 interning（is 恰好为真），真正复现回归需两个独立字符串实例
    src, dst = json.loads('"pending"'), json.loads('"pending"')
    assert src is not dst, "前提：两个内容相同但非同一实例的字符串"
    assert can_transition(src, dst) is True


def test_transition_table_covers_all_states():
    """流转表必须覆盖全部状态（漏配会静默 fail-closed）。"""
    assert set(_ALLOWED_TRANSITIONS) == set(JobStatus)


def test_job_wake_kind_marks_job_source():
    """带 job_id 的唤醒来源标为 job_completed；空来源/空 job_id 返回空串。"""
    assert job_wake_kind({"job_id": "j1"}) == "job_completed"
    assert job_wake_kind({}) == ""
    assert job_wake_kind({"job_id": ""}) == ""
