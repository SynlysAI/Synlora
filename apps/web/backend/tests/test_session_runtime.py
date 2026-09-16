"""会话运行装配解析单测（发消息与任务唤醒共用同一口径）。"""
import pytest
from fastapi import HTTPException

from app.services import workspace
from app.services.session_runtime import resolve_session_runtime


class _State:
    """最小 app.state 替身：只带解析用得到的服务。"""

    def __init__(self, settings, project_service):
        self.settings = settings
        self.project_service = project_service


async def test_unbound_session_uses_session_workspace(store, settings, project_service):
    """未绑定项目的会话：工作根 = sessions/{sid}，归属为 session_id。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    await repos.provider.create({"name": "p", "base_url": "http://x",
                                 "model_id": "m", "enabled": True})
    provider_id = (await repos.provider.list())[0]["_id"]
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider_id, "project_id": None,
    })
    runtime = await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert runtime.workspace_root == workspace.session_root(
        settings.data_root, "u1", doc["_id"])
    assert runtime.assistant is None


async def test_stale_project_binding_is_cleared(store, settings, project_service):
    """绑定已失效（项目被删）时回落会话工作根，并清掉脏 project_id。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    await repos.provider.create({"name": "p", "base_url": "http://x",
                                 "model_id": "m", "enabled": True})
    provider_id = (await repos.provider.list())[0]["_id"]
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider_id, "project_id": "gone",
    })
    runtime = await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert (await repos.session.get(doc["_id"]))["project_id"] is None


async def test_missing_provider_raises_422(store, settings, project_service):
    """无可用模型服务时抛 422（唤醒路径据此放弃本轮并告警）。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": None, "project_id": None,
    })
    with pytest.raises(HTTPException) as exc:
        await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert exc.value.status_code == 422


def _make_repos(store):
    """构造解析函数需要的 repo 组合（与 deps.get_repos 同结构）。"""
    from app.api.deps import Repos
    from app.db.repos import (
        AssistantRepo,
        EventRepo,
        FileRepo,
        ProviderRepo,
        RunRepo,
        SessionRepo,
    )
    return Repos(
        provider=ProviderRepo(store, fernet_key=""),
        assistant=AssistantRepo(store),
        session=SessionRepo(store),
        run=RunRepo(store),
        file=FileRepo(store),
        event=EventRepo(store),
    )


@pytest.fixture
def settings(tmp_path):
    """最小 Settings 替身（解析函数只用到 data_root，与真 Settings 同为 Path）。"""
    from types import SimpleNamespace
    return SimpleNamespace(data_root=tmp_path)


@pytest.fixture
def project_service(store, tmp_path):
    """项目服务（同一 store 与临时数据根）。"""
    from app.services.project_service import ProjectService
    return ProjectService(store, tmp_path)
