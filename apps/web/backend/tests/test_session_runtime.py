"""会话运行装配解析单测（发消息与任务唤醒共用同一口径）。"""
import pytest

from app.services import workspace
from app.services.session_runtime import NoUsableProvider, resolve_session_runtime


async def _seed_provider(repos, name="p"):
    """落一个启用的 provider 并返回文档。"""
    return await repos.provider.create({
        "name": name, "base_url": "http://x", "model_id": "m", "enabled": True})


def _repos_for(store):
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


async def test_unbound_session_uses_session_workspace(store, settings, project_service):
    """未绑定项目的会话：工作根 = sessions/{sid}，归属为 session_id。"""
    repos = _repos_for(store)
    provider = await _seed_provider(repos)
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider["_id"], "project_id": None,
    })
    runtime = await resolve_session_runtime(settings, project_service, repos, doc,
                                            {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert runtime.workspace_root == workspace.session_root(
        settings.data_root, "u1", doc["_id"])
    assert runtime.assistant is None
    # provider_cfg 构造：字段 str 归一化 + multimodal 直通
    assert runtime.provider_cfg.name == "p"
    assert runtime.provider_cfg.base_url == "http://x"
    assert runtime.provider_cfg.model_id == "m"
    assert runtime.provider_cfg.multimodal is False


async def test_bound_project_uses_project_root(store, settings, project_service):
    """绑定项目有效时用项目根，归属为 project_id。"""
    repos = _repos_for(store)
    await _seed_provider(repos)
    project = await project_service.create_project("u1", "proj")
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "project_id": project["_id"],
    })
    runtime = await resolve_session_runtime(settings, project_service, repos, doc,
                                            {"sub": "u1"})
    assert runtime.ownership == {"project_id": project["_id"]}
    assert runtime.workspace_root == project_service.root_for(project)
    # 有效绑定不得被清掉
    assert (await repos.session.get(doc["_id"]))["project_id"] == project["_id"]


async def test_stale_project_binding_is_cleared(store, settings, project_service):
    """绑定已失效（项目被删）时回落会话工作根，并清掉脏 project_id。"""
    repos = _repos_for(store)
    provider = await _seed_provider(repos)
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider["_id"], "project_id": "gone",
    })
    runtime = await resolve_session_runtime(settings, project_service, repos, doc,
                                            {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert (await repos.session.get(doc["_id"]))["project_id"] is None


async def test_model_fallback_order(store, settings, project_service):
    """模型优先级：会话级覆盖 > 助手绑定 > 首个启用模型。"""
    repos = _repos_for(store)
    first = await _seed_provider(repos, name="first")
    second = await _seed_provider(repos, name="second")
    assistant = await repos.assistant.create({
        "name": "a", "system_prompt": "p", "model_provider_id": second["_id"],
    })
    # 1) 助手绑定生效
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": assistant["_id"], "title": "t",
    })
    rt = await resolve_session_runtime(settings, project_service, repos, doc,
                                       {"sub": "u1"})
    assert rt.provider_cfg.name == "second"
    assert rt.assistant is not None
    # 2) 会话级覆盖优先于助手绑定
    await repos.session.update(doc["_id"], {"model_provider_id": first["_id"]})
    rt = await resolve_session_runtime(settings, project_service, repos,
                                       await repos.session.get(doc["_id"]),
                                       {"sub": "u1"})
    assert rt.provider_cfg.name == "first"


async def test_fallback_ignores_disabled_provider(store, settings, project_service):
    """停用的 provider 不参与回落（只剩停用项时视作无可用模型）。"""
    repos = _repos_for(store)
    for i in range(2):
        await repos.provider.create({"name": f"p{i}", "base_url": "http://x",
                                     "model_id": "m", "enabled": False})
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
    })
    with pytest.raises(NoUsableProvider):
        await resolve_session_runtime(settings, project_service, repos, doc,
                                      {"sub": "u1"})


async def test_missing_provider_raises_domain_error(store, settings, project_service):
    """无可用模型服务时抛领域异常（唤醒路径据此放弃本轮并告警）。"""
    repos = _repos_for(store)
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": None, "project_id": None,
    })
    with pytest.raises(NoUsableProvider) as exc:
        await resolve_session_runtime(settings, project_service, repos, doc,
                                      {"sub": "u1"})
    assert "未指定模型服务" in str(exc.value)
