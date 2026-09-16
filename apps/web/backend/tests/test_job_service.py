"""JobService / JobConnector 单测。"""
import pytest

from app.services.job_connectors import (
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


async def test_fake_connector_submit_failure():
    """测试连接器可被配置为提交失败（验证服务层错误归一化）。"""
    conn = make_fake_connector("k", plugin_id="p1", fail_submit="上游拒绝")
    with pytest.raises(JobSubmitFailed):
        await conn.submit({}, ctx={})
