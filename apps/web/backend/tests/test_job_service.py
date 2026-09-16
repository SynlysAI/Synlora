"""JobService / JobConnector 单测。"""
import pytest

from app.services.job_connectors import (
    FAKE_STATUS_MAP,
    JobConnectorRegistry,
    JobSubmitFailed,
    make_fake_connector,
)
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

    # status_map 省略 = 未映射任何状态（map_status 恒 None）
    reg.register(make_fake_connector("k2", plugin_id="p2"))
    assert reg.kinds == ["k2", "spec.nmr.forward"]
    assert reg.get("k2").map_status("done") is None


async def test_registry_duplicate_kind_rejected():
    """同一 kind 重复注册抛错（防两个插件抢同名任务类型）。"""
    reg = JobConnectorRegistry()
    reg.register(make_fake_connector("k", plugin_id="p1"), status_map={})
    with pytest.raises(ValueError):
        reg.register(make_fake_connector("k", plugin_id="p2"), status_map={})


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
        reg.register(_Incomplete(), status_map={})
    with pytest.raises(ValueError):
        reg.register(make_fake_connector("  ", plugin_id="p1"), status_map={})
    assert reg.kinds == []


async def test_registry_rejects_sync_methods():
    """用同步 def 冒充异步的连接器注册即报错（防运行期 await 时才炸）。"""
    reg = JobConnectorRegistry()

    class _SyncPoll:
        """poll 漏写 async 的连接器（isinstance 查不出来）。"""

        kind = "sync"
        plugin_id = "p1"

        async def submit(self, params, ctx):
            return "x"

        def poll(self, external_id, ctx):  # 忘了 async
            return "done"

        async def cancel(self, external_id, ctx):
            return True

    with pytest.raises(ValueError) as exc:
        reg.register(_SyncPoll(), status_map={})
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
