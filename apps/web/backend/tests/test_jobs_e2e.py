"""后台任务端到端：提交、轮询、结果落库，不自动创建聊天回复。"""
from pathlib import Path

import pytest

from app.db.repos import ProviderRepo, SessionRepo
from app.services.job_connectors import make_fake_connector
from app.services.job_access import JobSubmissionScope
from synlys_harness import JobStatus

FAKE_MAP = {"queued": JobStatus.PENDING, "doing": JobStatus.RUNNING,
            "done": JobStatus.COMPLETED}


@pytest.fixture
async def session_id(app):
    """一条属于 u-user 的会话，绑定一个不可达的 provider。"""
    provider = await ProviderRepo(
        app.state.store, fernet_key=app.state.settings.fernet_key).create({
            "name": "测试模型", "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-test", "model_id": "m", "enabled": True})
    doc = await SessionRepo(app.state.store).create({
        "user_id": "u-user", "assistant_id": None, "title": "端到端",
        "project_id": None, "model_provider_id": provider["_id"]})
    return doc["_id"]


async def test_submit_poll_wakes_agent_on_terminal(
    app, session_id, monkeypatch,
):
    """外部任务进入终态后唤醒所属会话：chat 以 notice=True 续跑一轮。"""
    started_runs: list[tuple[tuple, dict]] = []

    async def chat(*args, **kwargs) -> str:
        """记录唤醒触发的新一轮对话。"""
        started_runs.append((args, kwargs))
        return "wake-run"

    monkeypatch.setattr(app.state.agent_service, "chat", chat)
    # 连接器可在运行期注册（registry 与 ToolRegistry 同为可变注册表）
    app.state.job_connectors.register(
        make_fake_connector("k", plugin_id="p1", script=["queued", "done"]),
        status_map=FAKE_MAP)
    service = app.state.job_service
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}, "label": "端到端"},
        user={"sub": "u-user"}, session_id=session_id,
        ctx_extra=_submission_extra(session_id))
    assert result.ok is True

    # 两轮 tick：第一轮 queued（保持 pending），第二轮 done（写入终态并唤醒）
    await app.state.job_poller.tick()
    await app.state.job_poller.tick()

    doc = await service.get(result.data["job_id"])
    assert doc["status"] == "completed"
    assert len(started_runs) == 1
    args, kwargs = started_runs[0]
    assert args[0] == session_id and args[1] == {"sub": "u-user"}
    assert kwargs.get("notice") is True
    assert "[系统通知]" in args[4] and "端到端" in args[4]


async def test_wakeup_disabled_skips_agent(app, session_id, monkeypatch):
    """唤醒关闭（hook 未接线）时任务终态只更新文档，不触发新对话。"""
    started_runs: list[tuple[tuple, dict]] = []

    async def chat(*args, **kwargs) -> str:
        """记录意外触发的新一轮对话。"""
        started_runs.append((args, kwargs))
        return "unexpected-run"

    monkeypatch.setattr(app.state.agent_service, "chat", chat)
    monkeypatch.setattr(app.state.job_service, "_wakeup_hook", None)
    app.state.job_connectors.register(
        make_fake_connector("k2", plugin_id="p1", script=["queued", "done"]),
        status_map=FAKE_MAP)
    service = app.state.job_service
    result = await service.handle(
        {"action": "submit", "kind": "k2", "params": {}, "label": "关闭唤醒"},
        user={"sub": "u-user"}, session_id=session_id,
        ctx_extra=_submission_extra(session_id))
    assert result.ok is True

    await app.state.job_poller.tick()
    await app.state.job_poller.tick()

    doc = await service.get(result.data["job_id"])
    assert doc["status"] == "completed"
    assert started_runs == []


async def test_poller_started_with_app(app):
    """应用启动后轮询器在跑（生命周期由 lifespan 管）。"""
    assert app.state.job_poller.running is True


async def test_spec_agent_plugin_end_to_end(app, session_id, tmp_path, monkeypatch):
    """插件任务走完整链路：提交、轮询和结果回填，不创建聊天消息。"""
    import importlib.util
    import sys
    from pathlib import Path as _Path

    import httpx

    # 1) 让插件的连接器模块走 MockTransport，模拟 Spec_Agent 的端点
    plugin_dir = (_Path(__file__).resolve().parents[1]
                  / "catalog" / "plugins" / "spec_agent")
    name = "spec_agent_connectors_e2e"
    spec = importlib.util.spec_from_file_location(name, plugin_dir / "connectors.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "F1"}})
        if request.url.path.endswith("/tasks/nmr"):
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "PENDING"}})
        if request.url.path == "/api/v1/tasks/T1":
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "SUCCESS"}})
        if request.url.path == "/api/v1/tasks/T1/result":
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "SUCCESS",
                                                      "result": {"peaks": [1.2]}}})
        return httpx.Response(404)

    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))

    # 2) 注册插件连接器（模拟宿主挂载路径：这里直接注册到运行中的 registry）
    for connector in module.CONNECTORS:
        app.state.job_connectors.register(connector)

    # 2b) 落公共插件配置：轮询路径不带本轮 ctx，按 job 的 user_id 从配置存储重解析
    # （等价于管理后台「插件」页安装时填写服务地址，缺了它轮询会因无 base_url 失败）
    await app.state.plugin_config_store.save(
        "spec_agent", {"base_url": "http://spec.test"},
        app.state.plugin_service.package("spec_agent").config_schema)

    # 3) 造工作区文件 + 插件配置（base_url 指向 Mock 上游）
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "sample.nmr").write_bytes(b"data")

    result = await app.state.job_service.handle(
        {"action": "submit", "kind": "spec.task.nmr",
         "params": {"path": "sample.nmr"}, "label": "端到端 NMR"},
        user={"sub": "u-user"}, session_id=session_id,
        ctx_extra=_submission_extra(
            session_id,
            workspace_root=str(workspace),
            plugins={"spec_agent": {"base_url": "http://spec.test"}},
        ))
    assert result.ok is True

    # 4) 一轮 tick：状态推到 SUCCESS 并回填结果
    await app.state.job_poller.tick()
    doc = await app.state.job_service.get(result.data["job_id"])
    assert doc["status"] == "completed"
    assert "peaks" in (doc.get("result") or "")

    # 5) 终态不自动发起新一轮对话
    events = await app.state.event_repo.list_events(session_id)
    assert not [
        event for event in events
        if event.type.value == "user/message"
        and event.payload.get("kind") == "job_completed"
    ]
    assert "/api/v1/files/upload" in calls and "/api/v1/tasks/nmr" in calls
def _submission_extra(session_id: str, **extra) -> dict:
    """构造新架构要求的显式外部任务授权快照。"""
    return {
        **extra,
        "job_submission_scope": JobSubmissionScope(
            allowed_tools=frozenset({"job.submit"}),
            allowed_plugins=frozenset({"p1", "fake", "spec_agent"}),
            skills=(),
            resources=(),
            workspace_root=Path(extra.get("workspace_root") or ".").resolve(),
            ownership={"session_id": session_id},
        ),
    }
