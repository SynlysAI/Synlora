"""任务轮询器单测。"""
import asyncio

from app.db.repos import JobRepo
from app.services.job_connectors import JobConnectorRegistry, make_fake_connector
from app.services.job_poller import JobPoller
from app.services.job_service import JobService
from synlys_harness import JobStatus

FAKE_MAP = {"queued": JobStatus.PENDING, "doing": JobStatus.RUNNING,
            "done": JobStatus.COMPLETED}


async def test_tick_refreshes_active_jobs_only(store):
    """一轮 tick 只刷新未完成任务；终态任务不再请求外部系统。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_MAP)
    done = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                user={"sub": "u1"}, session_id="s1", ctx_extra={})
    reg.register(make_fake_connector("slow", plugin_id="p1", script=["queued", "queued"]),
                 status_map=FAKE_MAP)
    pending = await service.handle({"action": "submit", "kind": "slow", "params": {}},
                                   user={"sub": "u1"}, session_id="s1", ctx_extra={})
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(done.data["job_id"]))["status"] == "completed"
    assert (await service.get(pending.data["job_id"]))["status"] == "pending"


async def test_tick_survives_single_job_failure(store):
    """单个任务刷新抛异常不影响同轮其他任务（轮询循环不被打挂）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)

    class BoomConnector:
        kind = "boom"
        plugin_id = "p1"

        async def submit(self, params, ctx):
            return "e-boom"

        async def poll(self, external_id, ctx):
            raise RuntimeError("炸了")

        async def cancel(self, external_id, ctx):
            return True

    reg.register(BoomConnector(), status_map={"x": JobStatus.RUNNING})
    reg.register(make_fake_connector("ok", plugin_id="p1", script=["done"]),
                 status_map=FAKE_MAP)
    await service.handle({"action": "submit", "kind": "boom", "params": {}},
                         user={"sub": "u1"}, session_id="s1", ctx_extra={})
    ok_job = await service.handle({"action": "submit", "kind": "ok", "params": {}},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(ok_job.data["job_id"]))["status"] == "completed"


async def test_start_stop_loop(store):
    """start 起后台循环，stop 能干净收尾（无残留 task）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    poller = JobPoller(service, interval_s=0.01)
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert poller.running is False
