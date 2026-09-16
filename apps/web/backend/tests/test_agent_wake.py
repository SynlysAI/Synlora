"""AgentService 空闲判定 / 唤醒 / 运行结束回调单测。"""
import asyncio
from types import SimpleNamespace

import pytest

from app.api.deps import Repos
from app.db.repos import (
    AssistantRepo,
    EventRepo,
    FileRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
)
from app.services.agent_service import AgentService
from app.services.skill_service import SkillService


def _repos(store):
    """与 deps.get_repos 同结构的 repo 组合。"""
    return Repos(
        provider=ProviderRepo(store, fernet_key=""),
        assistant=AssistantRepo(store),
        session=SessionRepo(store),
        run=RunRepo(store),
        file=FileRepo(store),
        event=EventRepo(store),
    )


@pytest.fixture
def agent_service(store, tmp_path):
    """最小 AgentService（不发真实 LLM：只测空闲判定 / 唤醒接线 / 结束回调）。"""
    settings = SimpleNamespace(data_root=tmp_path, allowed_hosts=[])
    return AgentService(store, settings, EventRepo(store), SkillService(tmp_path))


@pytest.fixture
async def session_doc(store):
    """一条属于 u1 的会话（唤醒路径读它取 user_id）。"""
    return await SessionRepo(store).create({
        "user_id": "u1", "assistant_id": None, "title": "t", "project_id": None,
    })


async def test_is_busy_reflects_active_sessions(agent_service, session_doc):
    """无 run 时空闲；占位后忙。"""
    service = agent_service
    assert service.is_busy(session_doc["_id"]) is False
    service._active_by_session.setdefault(session_doc["_id"], set()).add("r1")  # noqa: SLF001
    assert service.is_busy(session_doc["_id"]) is True


async def test_on_run_finished_hook_is_awaited(agent_service, session_doc):
    """运行结束钩子在收尾路径被调用（唤醒队列靠它 drain）。"""
    seen: list[str] = []

    async def hook(session_id: str) -> None:
        seen.append(session_id)

    agent_service.set_run_finished_hook(hook)
    agent_service._active_by_session.setdefault(session_doc["_id"], set()).add("r9")  # noqa: SLF001
    await agent_service._notify_run_finished(session_doc["_id"])  # noqa: SLF001
    assert seen == [session_doc["_id"]]


async def test_drive_invokes_hook_after_releasing_session_slot(
        agent_service, session_doc, store):
    """_drive 收尾路径真的回调钩子，且回调时会话占位已释放（is_busy 为假）。"""
    seen: dict = {}

    class _Flag:
        """最小取消旗标（_drive 读 session._cancel.is_set()）。"""

        def is_set(self) -> bool:
            """恒为未取消。"""
            return False

    class _StubSession:
        """不跑 LLM 的假 RunSession：run 产出空事件流，无残留插话。"""

        _cancel = _Flag()

        async def run(self, text, attachments=None):
            """空事件流。"""
            return
            yield  # pragma: no cover 使其成为 async generator

        @staticmethod
        def take_queued_turn():
            """无残留插话（_drive 据此收尾）。"""
            return None

    sid = session_doc["_id"]

    async def hook(session_id: str) -> None:
        seen["session_id"] = session_id
        seen["busy"] = agent_service.is_busy(session_id)

    agent_service.set_run_finished_hook(hook)
    agent_service._active_by_session.setdefault(sid, set()).add("r1")  # noqa: SLF001
    await agent_service._drive("r1", _StubSession(), "hi", "u1", sid)
    assert seen == {"session_id": sid, "busy": False}


async def test_wake_starts_run_with_system_text(agent_service, session_doc,
                                                store, tmp_path, monkeypatch):
    """wake 以系统通知文本起一轮新 run，并把 job_id 作为唤醒来源透传。"""
    captured: dict = {}

    async def fake_chat(session_id, user, assistant, cfg, text, **kwargs):
        captured.update({"session_id": session_id, "text": text, "kwargs": kwargs})
        return "run-1"

    async def fake_resolve(settings, project_service, repos, doc, user):
        return SimpleNamespace(assistant=None, provider_cfg=object(),
                               workspace_root=tmp_path,
                               ownership={"session_id": "sid-x"})

    monkeypatch.setattr(agent_service, "chat", fake_chat)
    monkeypatch.setattr("app.services.agent_service.resolve_session_runtime",
                        fake_resolve)
    agent_service.set_runtime_deps(_repos(store), SimpleNamespace())

    await agent_service.wake(session_doc["_id"], "任务完成通知", "job-1")
    assert captured["session_id"] == session_doc["_id"]
    assert captured["text"] == "任务完成通知"
    assert captured["kwargs"]["wake_source"] == {"job_id": "job-1"}
    # 装配解析的结果必须原样透传给 chat（两条路径共用同一工作根与归属——
    # 这正是 Task 7 抽 session_runtime 要保证的；漏传会跑在错误目录）
    assert captured["kwargs"]["workspace_root"] is tmp_path
    assert captured["kwargs"]["file_ownership"] == {"session_id": "sid-x"}
    assert "enabled_plugins" in captured["kwargs"]


async def test_wake_raises_when_deps_missing(agent_service, session_doc):
    """未注入运行时依赖时 wake 拒绝（fail-closed）。"""
    with pytest.raises(RuntimeError):
        await agent_service.wake(session_doc["_id"], "x")


async def test_wake_raises_target_gone_for_deleted_session(agent_service, store):
    """会话已删除走 WakeTargetGone（正常的业务情形，调用方静默跳过）。"""
    from app.services.agent_service import WakeTargetGone

    agent_service.set_runtime_deps(_repos(store), SimpleNamespace())
    with pytest.raises(WakeTargetGone):
        await agent_service.wake("no-such-session", "x")


def test_narrow_tools_for_wake_drops_interactive():
    """唤醒轮工具收窄：去掉需要用户在场的交互工具，其余原样保留。"""
    from app.services.agent_service import narrow_tools_for_wake

    assert narrow_tools_for_wake(["file.read", "ask_user", "job.submit"]) == [
        "file.read", "job.submit"]
    # 不含交互工具时原样返回（不得误删）
    assert narrow_tools_for_wake(["file.read"]) == ["file.read"]
    assert narrow_tools_for_wake([]) == []


def _register_run(service, session_id, run_id, *, kind="chat", ask_pending=False):
    """在内存注册表里造一个 run（免去跑真实 LLM）。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    service._runs[run_id] = active          # noqa: SLF001
    service._run_kind[run_id] = kind        # noqa: SLF001
    service._active_by_session.setdefault(session_id, set()).add(run_id)  # noqa: SLF001
    if ask_pending:
        active.ask_future = asyncio.get_running_loop().create_future()
    return active


async def test_yieldable_covers_wake_and_parked_ask(agent_service, session_doc):
    """可让位 = 后台唤醒轮（对谁都让）；停在 ask 上的轮只对用户消息让。"""
    service, sid = agent_service, session_doc["_id"]
    _register_run(service, sid, "wake-1", kind="wake")
    _register_run(service, sid, "parked-1", ask_pending=True)
    _register_run(service, sid, "busy-1")

    assert service._yieldable("wake-1", for_user=True) is True     # noqa: SLF001
    assert service._yieldable("wake-1", for_user=False) is True    # noqa: SLF001
    assert service._yieldable("parked-1", for_user=True) is True   # noqa: SLF001
    # 后台轮不得抢用户正等着回答的问题
    assert service._yieldable("parked-1", for_user=False) is False  # noqa: SLF001
    assert service._yieldable("busy-1", for_user=True) is False    # noqa: SLF001
    assert service._yieldable("ghost", for_user=True) is False     # noqa: SLF001


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
        self._service._run_kind.pop(self._run_id, None)    # noqa: SLF001
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

    # 情形一：占位者是可让位的唤醒轮 → 用户消息抢占后放行
    active = _register_run(service, sid, "wake-1", kind="wake")
    active.session = _StubSession(service, sid, "wake-1", active)
    await service._admit_run(sid, wake=False)     # noqa: SLF001
    assert not service._active_by_session.get(sid)  # noqa: SLF001

    # 情形二：占位者是不可让位的前台轮 → 用户消息被拒
    _register_run(service, sid, "busy-1")
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid, wake=False)  # noqa: SLF001
    service._active_by_session.get(sid, set()).discard("busy-1")  # noqa: SLF001
    service._runs.pop("busy-1", None)             # noqa: SLF001
    service._run_kind.pop("busy-1", None)         # noqa: SLF001

    # 情形三：用户的前台轮停在 ask 上 → 唤醒轮不得抢占（会让用户的问题消失）
    parked = _register_run(service, sid, "parked-1", ask_pending=True)
    parked.session = _StubSession(service, sid, "parked-1", parked)
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid, wake=True)   # noqa: SLF001
    assert parked.ask_future is not None and not parked.ask_future.done()


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

    active = _register_run(service, sid, "wake-1", kind="wake")
    active.session = _NeverEnds()
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid, wake=False)  # noqa: SLF001
