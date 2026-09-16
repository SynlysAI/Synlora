"""任务轮询器单测。"""
import asyncio

from app.db.repos import JobRepo
from app.services.job_connectors import (
    FakeConnector,
    JobConnectorRegistry,
    make_fake_connector,
)
from app.services.job_poller import JobPoller
from app.services.job_service import JobService
from synlys_harness import JobStatus

FAKE_MAP = {"queued": JobStatus.PENDING, "doing": JobStatus.RUNNING,
            "done": JobStatus.COMPLETED}


async def test_tick_refreshes_active_jobs_only(store, monkeypatch):
    """一轮 tick 只刷新未完成任务；终态任务不再请求外部系统。

    两层都得盯：`list_active` 决定"哪些任务进轮询入口"，`refresh` 内部的终态
    守卫决定"要不要真的请求外部系统"。只数 poll 的话，`list_active` 放行终态
    文档时 tick 仍会白刷一遍（refresh 早返回、poll 数不变），测不出过滤失效。
    """
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    polls: list[str] = []

    class CountingConnector(FakeConnector):
        """记录 poll 调用次数的假连接器（证伪"终态任务仍被轮询"）。"""

        async def poll(self, external_id, ctx):
            polls.append(external_id)
            return await super().poll(external_id, ctx)

    reg.register(CountingConnector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_MAP)
    done = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                user={"sub": "u1"}, session_id="s1", ctx_extra={})
    reg.register(CountingConnector("slow", plugin_id="p1", script=["queued", "queued"]),
                 status_map=FAKE_MAP)
    pending = await service.handle({"action": "submit", "kind": "slow", "params": {}},
                                   user={"sub": "u1"}, session_id="s1", ctx_extra={})
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(done.data["job_id"]))["status"] == "completed"
    assert (await service.get(pending.data["job_id"]))["status"] == "pending"

    # 第二轮：已终态的 done 既不该进轮询入口，也不该再被 poll
    refreshed: list[str] = []
    real_refresh = service.refresh

    async def counting_refresh(doc):
        """记录 tick 本轮刷新了哪些任务。"""
        refreshed.append(str(doc.get("_id")))
        return await real_refresh(doc)

    monkeypatch.setattr(service, "refresh", counting_refresh)
    polls.clear()
    await poller.tick()
    assert refreshed == [pending.data["job_id"]], (
        f"tick 只应刷新活跃任务，实际刷新: {refreshed}")
    assert len(polls) == 1, f"终态任务不该再被轮询，实际 poll 次数: {len(polls)}"


async def test_tick_survives_single_job_failure(store, monkeypatch):
    """单个任务刷新抛异常不影响同轮其他任务（轮询循环不被打挂）。

    注意：连接器 poll 抛异常会被 JobService 内部兜底成 poll_failures，
    到不了 tick 的守卫——所以这里在 repo 层打桩，让 refresh 真的抛。
    """
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    reg.register(make_fake_connector("boom", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_MAP)
    reg.register(make_fake_connector("ok", plugin_id="p1", script=["done"]),
                 status_map=FAKE_MAP)
    await service.handle({"action": "submit", "kind": "boom", "params": {}},
                         user={"sub": "u1"}, session_id="s1", ctx_extra={})
    ok_job = await service.handle({"action": "submit", "kind": "ok", "params": {}},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})

    real_get = service._repo.get  # noqa: SLF001

    async def flaky_get(job_id):
        """boom 任务的读取抛异常，其余照常（让 refresh 真的抛）。"""
        doc = await real_get(job_id)
        if doc is not None and doc.get("kind") == "boom":
            raise RuntimeError("存储炸了")
        return doc

    monkeypatch.setattr(service._repo, "get", flaky_get)  # noqa: SLF001
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(ok_job.data["job_id"]))["status"] == "completed"


async def test_start_stop_loop(store):
    """start 起后台循环，stop 能干净收尾（无残留 task）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    poller = JobPoller(service, interval_s=0.01)
    await poller.start()
    assert poller.running is True             # start 真的起了循环
    first_task = poller._task                 # noqa: SLF001
    await poller.start()                      # 幂等：不应换 task
    assert poller._task is first_task         # noqa: SLF001
    await asyncio.sleep(0.05)
    await poller.stop()
    assert poller.running is False
