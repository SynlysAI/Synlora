"""AgentService 会话准入、问答等待与抢占单测。"""
import asyncio
from types import SimpleNamespace

import pytest

from app.db.repos import (
    EventRepo,
    SessionRepo,
)
from app.services.agent_service import AgentService
from app.services.skill_service import SkillService


@pytest.fixture
def agent_service(store, tmp_path):
    """最小 AgentService（不发真实 LLM，只测运行控制）。"""
    settings = SimpleNamespace(data_root=tmp_path, allowed_hosts=[])
    return AgentService(store, settings, EventRepo(store), SkillService(tmp_path))


@pytest.fixture
async def session_doc(store):
    """创建一条属于 u1 的会话。"""
    return await SessionRepo(store).create({
        "user_id": "u1", "assistant_id": None, "title": "t", "project_id": None,
    })


def _register_run(service, session_id, run_id, *, ask_pending=False):
    """在内存注册表里造一个 run（免去跑真实 LLM）。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    service._runs[run_id] = active          # noqa: SLF001
    service._active_by_session.setdefault(session_id, set()).add(run_id)  # noqa: SLF001
    if ask_pending:
        active.ask_future = asyncio.get_running_loop().create_future()
    return active


async def test_only_parked_ask_is_yieldable(agent_service, session_doc):
    """只有停在 ask 上等待用户回答的 run 可让位。"""
    service, sid = agent_service, session_doc["_id"]
    _register_run(service, sid, "parked-1", ask_pending=True)
    _register_run(service, sid, "busy-1")

    assert service._yieldable("parked-1") is True   # noqa: SLF001
    assert service._yieldable("busy-1") is False    # noqa: SLF001
    assert service._yieldable("ghost") is False     # noqa: SLF001


class _StubSession:
    """停在 ask 上的假会话：只有待答 future 被解掉才会收尾。

    同时按 `_drive` 的 finally 口径**同步**摘除注册表占位——`_drive` 里
    `done.set()` 与 `discard(run_id)` 之间没有 await，抢占方恢复执行时占位
    必然已释放；桩若用独立 task 做摘除，会造出生产里不存在的竞态。
    """

    def __init__(self, service, session_id, run_id, active):
        """保存宿主与所属 run。"""
        self._service = service
        self._session_id = session_id
        self._run_id = run_id
        self._active = active
        self.cancelled = False

    def cancel(self):
        """置取消旗标（真实实现只置旗标，叫不醒 await）+ 同步摘除占位。"""
        self.cancelled = True
        self._active.done.set()
        self._service._runs.pop(self._run_id, None)        # noqa: SLF001
        runs = self._service._active_by_session.get(self._session_id)  # noqa: SLF001
        if runs is not None:
            runs.discard(self._run_id)

    async def await_ask_then_finish(self):
        """模拟工具在管线内 await future（沿途无取消检查点）。"""
        await self._active.ask_future


async def test_preempt_unblocks_run_parked_on_ask(agent_service, session_doc):
    """抢占停在 ask 上的 run：必须解掉 future，否则 run 收不了尾、抢占一起挂死。

    tripwire：桩 session 只有 `ask_future` 被解掉才会返回，返回前 `cancel`
    已完成收尾并置位 `active.done`。实现若漏掉 `set_result`，`_preempt_runs`
    会一直等到 `PREEMPT_TIMEOUT_S` 超时，`active.done` 始终不置位，
    下面的断言即失败（而不是"跑得慢但通过"）。
    """
    service, sid = agent_service, session_doc["_id"]
    active = _register_run(service, sid, "parked-1", ask_pending=True)
    stub = _StubSession(service, sid, "parked-1", active)
    active.session = stub
    finisher = asyncio.create_task(stub.await_ask_then_finish())

    await service._preempt_runs(["parked-1"])  # noqa: SLF001

    assert stub.cancelled is True
    assert active.ask_future.done()
    assert active.ask_future.result() != ""
    assert active.done.is_set()          # run 真的收尾了（没被 await 卡住）
    assert not service._active_by_session.get(sid)  # noqa: SLF001
    await finisher
    assert finisher.done() and not finisher.cancelled()


async def test_admit_run_preempts_yieldable_then_allows(agent_service, session_doc):
    """准入：占位者全可让位时先抢占再放行，让不掉才抛 TooManyRuns。"""
    from app.services.agent_service import TooManyRuns

    service, sid = agent_service, session_doc["_id"]

    # 情形一：占位者停在 ask 上 → 新用户消息抢占后放行
    active = _register_run(service, sid, "parked-1", ask_pending=True)
    active.session = _StubSession(service, sid, "parked-1", active)
    await service._admit_run(sid)     # noqa: SLF001
    assert not service._active_by_session.get(sid)  # noqa: SLF001

    # 情形二：占位者是不可让位的前台轮 → 用户消息被拒
    _register_run(service, sid, "busy-1")
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid)  # noqa: SLF001
    service._active_by_session.get(sid, set()).discard("busy-1")  # noqa: SLF001
    service._runs.pop("busy-1", None)             # noqa: SLF001


async def test_wait_for_answer_returns_text_on_timeout(agent_service):
    """等回答超时返回兜底文本（run 继续跑，而不是永久占住会话）。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    active.ask_future = asyncio.get_running_loop().create_future()
    text = await agent_service._wait_for_answer(active, 0.01)  # noqa: SLF001
    assert text and "跳过" in text


async def test_wait_for_answer_returns_user_text(agent_service):
    """正常路径原样返回用户回答。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    active.ask_future = asyncio.get_running_loop().create_future()
    active.ask_future.set_result("选 B")
    assert await agent_service._wait_for_answer(active, 5) == "选 B"  # noqa: SLF001


async def test_wait_for_answer_without_future(agent_service):
    """无待答 future 时返回空串（防御：调用点不该出现，但不能抛）。"""
    from app.services.agent_service import ActiveRun

    assert await agent_service._wait_for_answer(ActiveRun(), 1) == ""  # noqa: SLF001


async def test_admit_run_gives_up_after_bounded_wait(agent_service, session_doc,
                                                     monkeypatch):
    """让位者迟迟不收尾时，准入在总预算内放弃并拒绝（不无限阻塞用户消息）。

    回归：早期实现用 `while True` 循环重试抢占，每轮各等 PREEMPT_TIMEOUT_S，
    run 卡在模型的网络调用里时会把用户消息阻塞成多个 5s 段（实测 25s）。
    """
    from app.services import agent_service as mod
    from app.services.agent_service import TooManyRuns

    monkeypatch.setattr(mod, "PREEMPT_TIMEOUT_S", 0.01)
    service, sid = agent_service, session_doc["_id"]

    class _NeverEnds:
        """cancel 只置旗标、从不收尾（模拟卡在模型网络调用里的 run）。"""

        def cancel(self):
            """对应真实实现里 session.cancel() 只置旗标的语义。"""

    active = _register_run(service, sid, "parked-1", ask_pending=True)
    active.session = _NeverEnds()
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid)  # noqa: SLF001
