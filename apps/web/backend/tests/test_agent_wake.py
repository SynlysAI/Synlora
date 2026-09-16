"""AgentService 空闲判定 / 唤醒 / 运行结束回调单测。"""
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
                               ownership={"session_id": doc["_id"]})

    monkeypatch.setattr(agent_service, "chat", fake_chat)
    monkeypatch.setattr("app.services.agent_service.resolve_session_runtime",
                        fake_resolve)
    agent_service.set_runtime_deps(_repos(store), SimpleNamespace())

    await agent_service.wake(session_doc["_id"], "任务完成通知", "job-1")
    assert captured["session_id"] == session_doc["_id"]
    assert captured["text"] == "任务完成通知"
    assert captured["kwargs"]["wake_source"] == {"job_id": "job-1"}
