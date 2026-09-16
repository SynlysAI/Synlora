"""任务机制端到端：提交 → 轮询 → 完成唤醒 → 会话多出一条事件。

说明：本用例只验证"唤醒消息被注入会话"这一事实，不验证模型回复——真实
回复需要可用的模型服务。为此会话绑定的 provider 指向一个必然连不上的
地址，run 会在 LLM 调用处失败，但 turn/start 与 user/message 已经落账，
足以断言唤醒链路打通。
"""
import asyncio

import pytest

from app.db.repos import ProviderRepo, SessionRepo
from app.services.job_connectors import make_fake_connector
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


async def test_submit_poll_wake_roundtrip(app, session_id):
    """全链路：工具层提交 → poller 推进到终态 → 会话收到系统通知消息。"""
    # 连接器可在运行期注册（registry 与 ToolRegistry 同为可变注册表）
    app.state.job_connectors.register(
        make_fake_connector("k", plugin_id="p1", script=["queued", "done"]),
        status_map=FAKE_MAP)
    service = app.state.job_service
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}, "label": "端到端"},
        user={"sub": "u-user"}, session_id=session_id, ctx_extra={})
    assert result.ok is True

    # 两轮 tick：第一轮 queued（保持 pending），第二轮 done（终态触发唤醒）
    await app.state.job_poller.tick()
    await app.state.job_poller.tick()

    # 唤醒起的 run 是后台 task，等事件落账（最多 2 秒）
    wake: list = []
    for _ in range(40):
        events = await app.state.event_repo.list_events(session_id)
        wake = [e for e in events if e.type.value == "user/message"
                and e.payload.get("kind") == "job_completed"]
        if wake:
            break
        await asyncio.sleep(0.05)
    assert len(wake) == 1
    assert wake[0].payload["job_id"] == result.data["job_id"]


async def test_poller_started_with_app(app):
    """应用启动后轮询器在跑（生命周期由 lifespan 管）。"""
    assert app.state.job_poller.running is True


async def test_wake_wrapper_does_not_swallow_domain_errors(app, monkeypatch):
    """唤醒包装必须放行 TooManyRuns / WakeTargetGone（否则通知被静默丢弃）。"""
    from app.services.agent_service import TooManyRuns, WakeTargetGone

    async def boom(session_id, text, job_id):
        raise TooManyRuns("会话忙")

    monkeypatch.setattr(app.state.agent_service, "wake", boom)
    wake_cb = app.state.job_service._wake  # noqa: SLF001
    with pytest.raises(TooManyRuns):
        await wake_cb("s1", "text", "job-1")

    async def gone(session_id, text, job_id):
        raise WakeTargetGone("会话没了")

    monkeypatch.setattr(app.state.agent_service, "wake", gone)
    with pytest.raises(WakeTargetGone):
        await wake_cb("s1", "text", "job-1")

    # 其余异常必须被吞掉（只记日志）
    async def other(session_id, text, job_id):
        raise ValueError("别的错")

    monkeypatch.setattr(app.state.agent_service, "wake", other)
    await wake_cb("s1", "text", "job-1")  # 不抛


async def test_spec_agent_plugin_end_to_end(app, session_id, tmp_path, monkeypatch):
    """插件连接器走完整链路：提交 → 轮询成功 → 结果回填 → 会话收到唤醒消息。"""
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
        ctx_extra={"workspace_root": str(workspace),
                   "plugins": {"spec_agent": {"base_url": "http://spec.test"}}})
    assert result.ok is True

    # 4) 一轮 tick：状态推到 SUCCESS 并回填结果
    await app.state.job_poller.tick()
    doc = await app.state.job_service.get(result.data["job_id"])
    assert doc["status"] == "completed"
    assert "peaks" in (doc.get("result") or "")

    # 5) 唤醒消息落进会话
    import asyncio
    wake: list = []
    for _ in range(40):
        events = await app.state.event_repo.list_events(session_id)
        wake = [e for e in events if e.type.value == "user/message"
                and e.payload.get("kind") == "job_completed"]
        if wake:
            break
        await asyncio.sleep(0.05)
    assert len(wake) == 1
    assert "/api/v1/files/upload" in calls and "/api/v1/tasks/nmr" in calls
