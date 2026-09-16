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
