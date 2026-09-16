"""JobService / JobConnector 单测。"""
import pytest

from app.db.repos import JobRepo
from app.services.job_connectors import (
    FAKE_STATUS_MAP,
    FakeConnector,
    JobConnectorRegistry,
    JobPollFailed,
    JobSubmitFailed,
    make_fake_connector,
)
from app.services.job_service import JobService
from synlys_harness import JobStatus


async def test_registry_register_and_get():
    """注册后可按 kind 取回连接器与状态映射。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("spec.nmr.forward", plugin_id="spec_agent")
    reg.register(conn, status_map={"queued": JobStatus.PENDING,
                                   "doing": JobStatus.RUNNING,
                                   "done": JobStatus.COMPLETED})
    got = reg.get("spec.nmr.forward")
    assert got is not None
    assert got.connector.plugin_id == "spec_agent"
    assert got.map_status("doing") is JobStatus.RUNNING
    # 映射键做了小写/去空白归一，外部原文可直接查
    assert got.map_status(" DONE ") is JobStatus.COMPLETED

    # status_map 省略 = 回落连接器自带的映射（FakeConnector 自带 FAKE_STATUS_MAP）
    reg.register(make_fake_connector("k2", plugin_id="p2"))
    assert reg.kinds == ["k2", "spec.nmr.forward"]
    assert reg.get("k2").map_status("done") is JobStatus.COMPLETED


async def test_registry_duplicate_kind_rejected():
    """同一 kind 重复注册抛错（防两个插件抢同名任务类型）。"""
    reg = JobConnectorRegistry()
    reg.register(make_fake_connector("k", plugin_id="p1"))
    with pytest.raises(ValueError):
        reg.register(make_fake_connector("k", plugin_id="p2"))


async def test_registry_unknown_status_keeps_none():
    """未映射的外部状态返回 None（调用方保持原状态，不倒退）。"""
    reg = JobConnectorRegistry()
    reg.register(make_fake_connector("k", plugin_id="p1"),
                 status_map={"done": JobStatus.COMPLETED})
    assert reg.get("k").map_status("weird") is None


async def test_registry_rejects_bad_connector_and_empty_kind():
    """形状不合格或 kind 为空的连接器注册即报错（防轮询时才炸）。"""
    reg = JobConnectorRegistry()

    class _Incomplete:
        """漏实现 poll/cancel 的连接器（形状不合格）。"""

        kind = "half"
        plugin_id = "p1"

        async def submit(self, params, ctx):
            return "x"

    with pytest.raises(ValueError):
        reg.register(_Incomplete())
    with pytest.raises(ValueError):
        reg.register(make_fake_connector("  ", plugin_id="p1"))
    assert reg.kinds == []


async def test_registry_rejects_sync_methods():
    """用同步 def 冒充异步的连接器注册即报错（防运行期 await 时才炸）。"""
    reg = JobConnectorRegistry()

    class _SyncPoll:
        """poll 漏写 async 的连接器（isinstance 查不出来）。"""

        kind = "sync"
        plugin_id = "p1"
        # 形状齐全（含 status_map），确保被拦下的是 async 校验而非协议校验
        status_map = {"done": JobStatus.COMPLETED}

        async def submit(self, params, ctx):
            return "x"

        def poll(self, external_id, ctx):  # 忘了 async
            return "done"

        async def cancel(self, external_id, ctx):
            return True

    with pytest.raises(ValueError) as exc:
        reg.register(_SyncPoll())
    assert "async" in str(exc.value)
    assert reg.kinds == []


async def test_fake_connector_lifecycle():
    """测试连接器：submit 返回外部 id，poll 按脚本推进状态，cancel 生效。"""
    conn = make_fake_connector("k", plugin_id="p1", script=["queued", "doing", "done"])
    external_id = await conn.submit({"x": 1}, ctx={})
    assert external_id
    assert await conn.poll(external_id, ctx={}) == "queued"
    assert await conn.poll(external_id, ctx={}) == "doing"
    assert await conn.poll(external_id, ctx={}) == "done"
    # 脚本走完后保持末态（幂等）
    assert await conn.poll(external_id, ctx={}) == "done"
    # 取消后 poll 一律返回 cancelled（覆盖取消态）
    await conn.cancel(external_id, ctx={})
    assert await conn.poll(external_id, ctx={}) == "cancelled"


async def test_fake_connector_submit_failure():
    """测试连接器可被配置为提交失败（验证服务层错误归一化）。"""
    conn = make_fake_connector("k", plugin_id="p1", fail_submit="上游拒绝")
    with pytest.raises(JobSubmitFailed):
        await conn.submit({}, ctx={})


async def test_fake_status_map_covers_all_script_states():
    """标准映射必须覆盖 FakeConnector 的 cancel 态（漏了它取消观察不到）。"""
    assert FAKE_STATUS_MAP["cancelled"] is JobStatus.CANCELLED
    assert make_fake_connector("k", script=["queued"]).kind == "k"


def _job_service(store, **kwargs):
    """构造最小可用的 JobService（无插件/身份服务，走 FakeConnector）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg, **kwargs)
    return service, reg


async def test_submit_creates_pending_job(store):
    """提交后落库一条 pending 任务，内容含外部 id 与摘要。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {"a": 1}, "label": "试算"},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is True
    assert "任务 ID" in result.content
    job_id = result.data["job_id"]
    doc = await service.get(job_id)
    assert doc["status"] == "pending"
    assert doc["session_id"] == "s1"
    assert doc["user_id"] == "u1"
    assert doc["external_id"].startswith("fake-")


async def test_submit_unknown_kind_is_readable_error(store):
    """未注册的任务类型给出可读错误，不抛异常。"""
    service, _ = _job_service(store)
    result = await service.handle(
        {"action": "submit", "kind": "nope", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "unknown_job_kind"
    assert "nope" in result.content


async def test_submit_passes_plugin_config_and_token(store):
    """提交时把该插件的配置与用户代签 token 交给连接器。"""
    service, reg = _job_service(store)
    conn = make_fake_connector("k", plugin_id="spec_agent", script=["queued"])
    reg.register(conn, status_map=FAKE_STATUS_MAP)
    await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"plugins": {"spec_agent": {"base_url": "http://x"}},
                   "ai4ms_token": "tok-1"})
    assert conn.submitted[0]["ctx"]["config"] == {"base_url": "http://x"}
    assert conn.submitted[0]["ctx"]["ai4ms_token"] == "tok-1"


async def test_submit_failure_maps_to_tool_error(store):
    """连接器提交失败 → ok=False 且错误码可辨识（不落库半成品）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", fail_submit="上游拒绝"),
                 status_map=FAKE_STATUS_MAP)
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "submit_failed"
    assert "上游拒绝" in result.content
    assert await service.list_for_session("s1") == []


async def test_status_and_list_render_summary(store):
    """status/list 返回人类可读摘要，含任务 ID 与状态。"""
    service, reg = _job_service(store)
    # 脚本首态刻意取非 pending：Task 6 落地后 status 会先真刷一次外部状态，
    # 此处必须看到 running（若首态写成 queued 映射回 pending，断言就退化成
    # "刷新没生效也通过"的静默失效，而不是守卫）。
    reg.register(make_fake_connector("k", plugin_id="p1", script=["doing", "done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle(
        {"action": "submit", "kind": "k", "params": {}, "label": "试算"},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    one = await service.handle({"action": "status", "job_id": job_id},
                               user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert one.ok is True and job_id in one.content
    assert "状态: running" in one.content  # Task 6：status 先刷新再渲染
    many = await service.handle({"action": "list"},
                                user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert many.ok is True and "试算" in many.content


async def test_status_of_foreign_job_is_not_visible(store):
    """别人的任务查不到（按 user_id 校验，不泄露存在性）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    other = await service.handle({"action": "status", "job_id": job_id},
                                 user={"sub": "u2"}, session_id="s2", ctx_extra={})
    assert other.ok is False and other.error == "not_found"


async def test_ctx_for_poll_path_rebuilds_config(store):
    """轮询路径（无 ctx_extra）从插件配置存储重建配置；解析失败降级为空。"""
    class _ConfigStore:
        """最小配置存储替身（记录解析结果或模拟解密失败）。"""

        def __init__(self, fail=False):
            """fail=True 时解析抛异常（模拟解密失败）。"""
            self.fail = fail

        async def resolved_for_user(self, user_id, plugin_id):
            """返回该用户该插件的配置。"""
            if self.fail:
                raise RuntimeError("解密失败")
            return {"base_url": "http://poll", "user": user_id}

    service, reg = _job_service(store, plugin_config_store=_ConfigStore())
    ctx = await service._ctx_for("u1", "spec_agent", None)  # noqa: SLF001
    assert ctx["config"]["base_url"] == "http://poll"
    assert ctx["ai4ms_token"] == ""

    degraded, _ = _job_service(store, plugin_config_store=_ConfigStore(fail=True))
    ctx2 = await degraded._ctx_for("u1", "spec_agent", None)  # noqa: SLF001
    assert ctx2["config"] == {}


async def test_handle_unknown_action_and_render_list_isolation(store):
    """未知 action 报 invalid_arguments；list 不泄露他人任务。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    bad = await service.handle({"action": "nope"}, user={"sub": "u1"},
                               session_id="s1", ctx_extra={})
    assert bad.ok is False and bad.error == "invalid_arguments"
    await service.handle({"action": "submit", "kind": "k", "params": {}},
                         user={"sub": "u2"}, session_id="s1", ctx_extra={})
    mine = await service.render_list("s1", user_id="u1")
    assert mine.data["count"] == 0


async def test_refresh_advances_status_via_status_map(store):
    """刷新把外部状态原文映射为统一状态并落库。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued", "doing", "done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    doc = await service.refresh(await service.get(job_id))
    assert doc["status"] == "pending"  # 首态 queued 映射回 pending（同状态重复刷新不炸、不倒退）
    # 连推三次必然到 completed
    for _ in range(3):
        doc = await service.refresh(await service.get(job_id))
    assert doc["status"] == "completed"


async def test_refresh_keeps_status_on_poll_failure(store):
    """查询失败时保持原状态并累计失败计数（不把抖动判成失败）。"""
    class FlakyConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            raise JobPollFailed("网络不可达")

    service, reg = _job_service(store)
    reg.register(FlakyConnector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    before = await service.get(submitted.data["job_id"])
    after = await service.refresh(before)
    assert after["status"] == before["status"]
    assert int(after.get("poll_failures") or 0) == 1


async def test_refresh_ignores_unmapped_status(store):
    """未映射的外部状态不改变任务状态（防状态倒退）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["weird"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "pending"
    assert doc["last_raw_status"] == "weird"


async def test_refresh_fails_job_when_connector_gone(store):
    """连接器消失（插件被卸载）时任务判失败、落结束时间并唤醒一次。"""
    woken: list[tuple[str, str, str]] = []

    async def wake(session_id, text, job_id):
        woken.append((session_id, text, job_id))

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    # 模拟插件被卸载：连接器从注册表消失
    reg._items.pop("k")  # noqa: SLF001
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "failed"
    assert "k" in doc["error"]
    assert doc.get("ended_at")
    # 判失败也是终态：同样要唤醒（否则用户永远等不到失败通知）
    assert len(woken) == 1
    assert woken[0][0] == "s1"
    assert woken[0][2] == submitted.data["job_id"]
    assert "failed" in woken[0][1]
    assert "任务类型已不可用" in woken[0][1]


async def test_terminal_job_is_not_refreshed(store):
    """终态任务不再请求外部系统（避免无谓调用）。"""
    calls: list[str] = []

    class CountingConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            calls.append(external_id)
            return await super().poll(external_id, ctx)

    service, reg = _job_service(store)
    reg.register(CountingConnector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    assert doc.get("ended_at")  # 终态落结束时间
    calls.clear()
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert calls == []


async def test_cancel_marks_cancelled(store):
    """取消：调连接器 cancel 并把状态置为 cancelled。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    result = await service.handle({"action": "cancel",
                                   "job_id": submitted.data["job_id"]},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is True
    doc = await service.get(submitted.data["job_id"])
    assert doc["status"] == "cancelled"


async def test_cancel_without_external_ack(store):
    """外部系统未受理取消时仍收敛本地状态，文案区分未确认。"""
    class RefusingConnector(FakeConnector):
        async def cancel(self, external_id, ctx):
            return False

    service, reg = _job_service(store)
    reg.register(RefusingConnector("k", plugin_id="p1", script=["queued"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    result = await service.handle({"action": "cancel",
                                   "job_id": submitted.data["job_id"]},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is True
    assert "未确认" in result.content
    doc = await service.get(submitted.data["job_id"])
    assert doc["status"] == "cancelled"
    assert doc["cancel_accepted"] is False


async def test_cancel_terminal_job_is_rejected(store):
    """已结束的任务不能再取消（给可读提示）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    result = await service.handle({"action": "cancel",
                                   "job_id": submitted.data["job_id"]},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "already_finished"


async def test_terminal_status_triggers_wake(store):
    """任务进入终态时调用唤醒回调，文本含任务与结果摘要。"""
    woken: list[tuple[str, str, str]] = []

    async def wake(session_id, text, job_id):
        woken.append((session_id, text, job_id))

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k",
                                      "params": {}, "label": "试算"},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert len(woken) == 1
    assert woken[0][0] == "s1"
    assert woken[0][2] == submitted.data["job_id"]
    assert "试算" in woken[0][1]


async def test_busy_session_queues_wake_until_drain(store):
    """会话忙时先排队，drain 后补发（不丢唤醒）。"""
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: True)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert woken == []
    assert service.pending_wake_count("s1") == 1
    await service.drain_pending("s1")
    assert woken == [submitted.data["job_id"]]
    assert service.pending_wake_count("s1") == 0


async def test_only_notified_once_per_job(store):
    """同一任务不重复唤醒（避免刷新抖动导致多轮注入）。"""
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    await service.refresh(await service.get(job_id))
    await service.refresh(await service.get(job_id))
    await service.refresh(await service.get(job_id))
    assert woken == [job_id]


async def test_concurrent_refresh_wakes_once(store):
    """并发刷新同一任务只唤醒一次（poller 的 tick 与模型查进度可能同时命中）。

    两个调用方拿到同一份非终态快照后一起挂起在外部 poll 上，先后观察到终态：
    任务级锁串行化 + 锁内重读，使后到者看到已落库的终态直接返回——既不再重复
    唤醒（会白烧一轮 LLM），也不再对同一任务重复调外部 poll。
    """
    import asyncio

    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    class SlowConnector(FakeConnector):
        """poll 主动让出事件循环，制造两个 refresh 交错推进的窗口。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.polls = 0

        async def poll(self, external_id, ctx):
            self.polls += 1
            await asyncio.sleep(0)  # 让出控制权：并发调用方在此交错
            return await super().poll(external_id, ctx)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    conn = SlowConnector("k", plugin_id="p1", script=["done"])
    reg.register(conn, status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    doc = await service.get(job_id)
    # 两个调用方共用同一份非终态快照（正是 poller 与 job.status 并发的真实形态）
    await asyncio.gather(service.refresh(doc), service.refresh(doc))
    assert woken == [job_id]
    # 锁生效的旁证：后到者在锁内重读已终态，连外部 poll 都没再发一次
    assert conn.polls == 1


async def test_cancel_not_overwritten_by_concurrent_refresh(store):
    """取消与刷新并发时，取消结果不被轮询覆盖（任务不得从 cancelled 回退）。

    让出型交错（不用 barrier：加锁后对撞会死锁）——poll 反复让出事件循环：
    未取锁的实现里 cancel 会在让出窗口内把 cancelled 写进库，poll 观察到后
    仍返回非终态，随后 `_refresh_locked` 的整字段写把 cancelled 覆盖回
    running（任务"复活"，还会被继续轮询/唤醒）；取锁的实现里 cancel 排在锁上，
    poll 空转到上限直接返回，cancel 在锁内重读后以最新状态收敛为 cancelled。
    """
    import asyncio

    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    probe: dict = {}

    class SlowPollConnector(FakeConnector):
        """poll 让出事件循环：取消落库（或空转到上限）后才返回非终态。"""

        async def poll(self, external_id, ctx):
            for _ in range(200):
                await asyncio.sleep(0)
                current = await probe["service"].get(probe["job_id"])
                if current and current.get("status") == "cancelled":
                    break  # 未取锁：取消已经落地，本函数随后仍返回非终态
            return "doing"

    reg.register(SlowPollConnector("k", plugin_id="p1", script=["doing"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    probe["service"] = service
    probe["job_id"] = job_id
    doc = await service.get(job_id)
    # 刷新与取消并发：无论谁先，终态必须是 cancelled（取消是用户显式意图）
    await asyncio.gather(service.refresh(doc),
                         service.cancel(job_id, user_id="u1"))
    final = await service.get(job_id)
    assert final["status"] == "cancelled"
    assert woken == []  # 已取消的任务不该被唤醒


async def test_cancel_after_concurrent_completion_is_rejected(store):
    """刷新先落地终态时，排在锁上的取消必须看到最新状态并拒绝（锁内重读）。

    cancel 在锁内重读文档，看到并发 refresh 已落地的 completed 便早返回——
    既不把已完成的任务拉回 cancelled（终态回退），也不对同一任务重复唤醒。
    """
    import asyncio

    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)

    class SlowDoneConnector(FakeConnector):
        """poll 让出事件循环，让并发的 cancel 排到任务锁上后再返回终态。"""

        async def poll(self, external_id, ctx):
            for _ in range(100):
                await asyncio.sleep(0)
            return "done"

    reg.register(SlowDoneConnector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    doc = await service.get(job_id)
    refreshed, cancelled = await asyncio.gather(
        service.refresh(doc), service.cancel(job_id, user_id="u1"))
    assert refreshed["status"] == "completed"
    assert cancelled.ok is False and cancelled.error == "already_finished"
    final = await service.get(job_id)
    assert final["status"] == "completed"  # 取消不得把已完成的任务拉回 cancelled
    assert woken == [job_id]  # 完成唤醒仍只发生一次


async def test_wake_composes_result_text(store):
    """唤醒文本包含任务类型、状态与结果正文（供模型直接整合）。"""
    seen: list[str] = []

    async def wake(session_id, text, job_id):
        seen.append(text)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert "任务 ID" in seen[0]
    assert "completed" in seen[0]


async def test_wake_requeues_on_too_many_runs(store):
    """唤醒撞上会话忙（TooManyRuns）时重新入队，不丢通知。"""
    from app.services.agent_service import TooManyRuns

    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)
        # 只第一次撞上忙（"判定空闲"到"chat 取锁"之间有窗口，用户此刻发消息）；
        # 会话空出来后再唤醒即成功——否则"补发成功"与"重新入队"两断言互相矛盾
        if len(woken) == 1:
            raise TooManyRuns("会话忙")

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)  # 判定时空闲，但 chat 取锁时被抢先
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    await service.refresh(await service.get(job_id))
    # 第一次尝试失败 → 重新入队
    assert service.pending_wake_count("s1") == 1
    # 会话空闲后 drain 补发成功
    await service.drain_pending("s1")
    assert woken == [job_id, job_id]
    assert service.pending_wake_count("s1") == 0


async def test_wake_skips_deleted_session(store):
    """会话已删（WakeTargetGone）时静默跳过，不重新入队也不抛错。"""
    from app.services.agent_service import WakeTargetGone

    async def wake(session_id, text, job_id):
        raise WakeTargetGone("会话没了")

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert service.pending_wake_count("s1") == 0


async def test_drain_keeps_other_jobs_when_one_fails(store):
    """drain 逐条出队：单条失败不牵连同会话其余待唤醒任务。"""
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)
        if len(woken) == 1:
            raise RuntimeError("第一条炸了")

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: True)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    first = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                 user={"sub": "u1"}, session_id="s1", ctx_extra={})
    second = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(first.data["job_id"]))
    await service.refresh(await service.get(second.data["job_id"]))
    assert service.pending_wake_count("s1") == 2
    await service.drain_pending("s1")
    # 两条都被尝试（第一条抛错不阻断第二条），队列清空
    assert woken == [first.data["job_id"], second.data["job_id"]]
    assert service.pending_wake_count("s1") == 0


async def test_drain_does_not_retry_requeued_job_this_round(store):
    """drain 本轮不重试被重新入队的任务（否则 while 立刻取到 → 死循环）。

    重新入队的那条留待下一次 drain 补发，同会话其余通知照常处理。
    """
    from app.services.session_runtime import TooManyRuns

    busy = {"flag": True}
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)
        if busy["flag"] and len(woken) == 1:
            raise TooManyRuns("会话忙")  # 第一条撞上"判定空闲到取锁"的窗口

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: True)  # 提交后先排队，避免终态即唤醒
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    first = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                 user={"sub": "u1"}, session_id="s1", ctx_extra={})
    second = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(first.data["job_id"]))
    await service.refresh(await service.get(second.data["job_id"]))
    assert service.pending_wake_count("s1") == 2
    await service.drain_pending("s1")
    # 第一条本轮只尝试一次（不死循环），第二条照常补发，队列里留 1 条等下次
    assert woken == [first.data["job_id"], second.data["job_id"]]
    assert service.pending_wake_count("s1") == 1
    # 会话空出来后下一轮 drain 补发成功并清空队列
    busy["flag"] = False
    await service.drain_pending("s1")
    assert woken == [first.data["job_id"], second.data["job_id"],
                     first.data["job_id"]]
    assert service.pending_wake_count("s1") == 0


async def test_wake_text_guards_failed_task(store):
    """失败终态的唤醒文本不给"可继续调用工具"的开放邀请（防原样重提死循环）。"""
    seen: list[str] = []

    async def wake(session_id, text, job_id):
        seen.append(text)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map=FAKE_STATUS_MAP)
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    reg._items.pop("k")  # noqa: SLF001 模拟插件卸载 → 任务判失败
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert "不要原样重新提交同一任务" in seen[0]
    assert "可继续调用工具" not in seen[0]
    # 完成态则是正向引导：直接回应，不重复提交
    terminal_text = service.compose_wake_text(
        {"_id": "job-x", "kind": "k", "status": JobStatus.COMPLETED.value})
    assert "不要重复提交同一任务" in terminal_text
    assert "可继续调用工具" not in terminal_text


async def test_register_falls_back_to_connector_status_map(store):
    """register 不传 status_map 时回落到连接器自带的 status_map。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1", script=["done"])
    reg.register(conn)
    assert reg.get("k").map_status("done") is JobStatus.COMPLETED


async def test_explicit_status_map_wins_over_connector(store):
    """显式传 status_map 时以显式为准（兼容既有调用点）。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1", script=["done"])
    reg.register(conn, status_map={"done": JobStatus.FAILED})
    assert reg.get("k").map_status("done") is JobStatus.FAILED


async def test_register_requires_some_status_map(store):
    """两者都没有则报错（缺映射会让状态永远映射不上、任务永不终结）。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1")
    conn.status_map = {}
    with pytest.raises(ValueError):
        reg.register(conn)


async def test_register_rejects_non_dict_status_map(store):
    """status_map 非 dict 时抛 ValueError（不能穿出 AttributeError 打挂启动）。

    truthy 非 mapping（如列表）若放行，会在 `.items()` 处抛 AttributeError；
    挂载逻辑只捕 ValueError，异常将穿透到 lifespan 让应用起不来。
    """
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1", script=["done"])
    conn.status_map = ["done"]          # truthy 但非 mapping
    with pytest.raises(ValueError) as exc:
        reg.register(conn)
    assert "list" in str(exc.value)     # 报错文案给出实际类型名，便于排查
    assert reg.kinds == []


async def test_submit_persists_workspace_root(store):
    """提交时把工作区根落进 job 文档（轮询路径据此重建 ctx）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]))
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"workspace_root": "/data/users/u1/sessions/s1/workspace"})
    doc = await service.get(result.data["job_id"])
    assert doc["workspace_root"] == "/data/users/u1/sessions/s1/workspace"


async def test_ctx_for_carries_workspace_root_on_both_paths(store):
    """提交路径从 ctx_extra 取工作根；轮询路径从参数取。"""
    service, reg = _job_service(store)
    conn = make_fake_connector("k", plugin_id="p1", script=["queued"])
    reg.register(conn)
    await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"workspace_root": "/w/sub"})
    # 提交路径：连接器收到的 ctx 带工作根
    assert conn.submitted[0]["ctx"]["workspace_root"] == "/w/sub"
    # 轮询路径：无 ctx_extra 时由调用方传入（refresh 从 job 文档取）
    ctx = await service._ctx_for("u1", "p1", workspace_root="/w/sub")  # noqa: SLF001
    assert ctx["workspace_root"] == "/w/sub"


async def test_ctx_for_without_workspace_root_is_empty_string(store):
    """无工作区（历史任务）时给空串，插件据此给出可读错误。"""
    service, _ = _job_service(store)
    ctx = await service._ctx_for("u1", "p1")  # noqa: SLF001
    assert ctx["workspace_root"] == ""


async def test_refresh_fetches_result_on_success(store):
    """成功终态时调连接器的 fetch_result 并把结果写入 job 文档。"""
    class ResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            return '{"peaks": [1.2, 3.4]}'

    service, reg = _job_service(store)
    reg.register(ResultConnector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    assert doc["result"] == '{"peaks": [1.2, 3.4]}'


async def test_refresh_without_fetch_result_leaves_result_empty(store):
    """连接器未实现 fetch_result 时结果保持空（不报错）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    assert not doc.get("result")


async def test_fetch_result_failure_does_not_break_transition(store):
    """取结果失败只告警，任务状态照常落地（结果留空）。"""
    class BrokenResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            raise RuntimeError("取结果炸了")

    service, reg = _job_service(store)
    reg.register(BrokenResultConnector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"


async def test_failed_task_does_not_fetch_result(store):
    """失败终态不取结果（上游失败时 result 无意义）。"""
    calls: list[str] = []

    class CountingResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            calls.append(external_id)
            return "x"

    service, reg = _job_service(store)
    reg.register(CountingResultConnector("k", plugin_id="p1", script=["failed"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "failed"
    assert calls == []


async def test_refresh_passes_workspace_root_to_connector(store):
    """轮询路径的 ctx 带上工作根（提交时落库、刷新时从 job 文档取回）。"""
    seen: list[dict] = []

    class SpyConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            seen.append(dict(ctx))
            return await super().poll(external_id, ctx)

    service, reg = _job_service(store)
    reg.register(SpyConnector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"workspace_root": "/w/from-submit"})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert seen[-1]["workspace_root"] == "/w/from-submit"
