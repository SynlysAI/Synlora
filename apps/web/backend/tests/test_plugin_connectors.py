"""Spec_Agent 谱图任务连接器测试（httpx MockTransport 模拟上游）。"""
import json
from pathlib import Path

import httpx
import pytest

from synlys_harness import JobStatus

from app.services.job_connectors import JobSubmitFailed

# 插件目录不在 sys.path 上，按 loader 的方式动态加载
PLUGIN_DIR = (Path(__file__).resolve().parents[1]
              / "catalog" / "plugins" / "spec_agent")


def _load_module():
    """动态加载插件的连接器模块（每次返回同一模块实例）。"""
    import importlib.util
    import sys

    name = "spec_agent_connectors_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "connectors.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connectors():
    """插件的 5 个连接器实例。"""
    return _load_module().CONNECTORS


@pytest.fixture
def workspace(tmp_path):
    """造一个含谱图文件的工作区。"""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sample.nmr").write_bytes(b"fake-nmr-binary")
    return root


def _ctx(root):
    """构造连接器上下文。"""
    return {"config": {"base_url": "http://spec.test"},
            "ai4ms_token": "tok-1",
            "workspace_root": str(root)}


def test_five_kinds_declared(connectors):
    """声明 5 个谱图任务，kind 与 plugin_id 正确、状态映射齐备。"""
    kinds = sorted(c.kind for c in connectors)
    assert kinds == ["spec.task.gpc", "spec.task.ir", "spec.task.lcms",
                     "spec.task.nmr", "spec.task.raman"]
    for c in connectors:
        assert c.plugin_id == "spec_agent"
        assert c.status_map["success"] is JobStatus.COMPLETED
        assert c.status_map["canceled"] is JobStatus.CANCELLED


async def test_submit_uploads_then_creates_task(connectors, workspace, monkeypatch):
    """submit 先上传拿 file_id，再以 file_id 提任务，返回上游 task_id。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "message": "ok",
                                             "data": {"file_id": "FILE-1"}})
        if request.url.path == "/api/v1/tasks/nmr":
            body = json.loads(request.content)
            assert body["input"] == {"input_type": "file_id", "file_id": "FILE-1"}
            return httpx.Response(200, json={"code": 0, "message": "ok",
                                             "data": {"task_id": "T-9",
                                                      "task_type": "nmr_analysis",
                                                      "status": "PENDING"}})
        return httpx.Response(404)

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")

    task_id = await nmr.submit({"path": "sample.nmr"}, _ctx(workspace))

    assert task_id == "T-9"
    assert [r.url.path for r in seen] == ["/api/v1/files/upload",
                                          "/api/v1/tasks/nmr"]
    assert seen[0].headers["authorization"] == "Bearer tok-1"


async def test_submit_rejects_missing_or_escaping_path(connectors, workspace,
                                                       monkeypatch):
    """缺少 path、越界路径、文件不存在都给出可读失败。"""
    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(500)))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")

    with pytest.raises(JobSubmitFailed):
        await nmr.submit({}, _ctx(workspace))
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "../../etc/passwd"}, _ctx(workspace))
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "nope.nmr"}, _ctx(workspace))


async def test_submit_requires_configured_base_url(connectors, workspace):
    """未配置服务地址时给出可读失败。"""
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    ctx = {"config": {}, "ai4ms_token": "", "workspace_root": str(workspace)}
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "sample.nmr"}, ctx)


async def test_poll_returns_raw_status(connectors, monkeypatch):
    """poll 返回上游状态原文（由 status_map 翻译）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"task_id": "T-9",
                                                  "status": "RUNNING",
                                                  "progress": 40}})

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    assert await nmr.poll("T-9", {"config": {"base_url": "http://spec.test"}}) == "RUNNING"


async def test_poll_raises_on_http_failure(connectors, monkeypatch):
    """查询失败一律抛 JobPollFailed（静默返回空串会让任务永久挂起）。"""
    from app.services.job_connectors import JobPollFailed

    module = _load_module()
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    ctx = {"config": {"base_url": "http://spec.test"}}

    # 401 凭证过期
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(401)))
    with pytest.raises(JobPollFailed):
        await nmr.poll("T-9", ctx)

    # 500
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(JobPollFailed):
        await nmr.poll("T-9", ctx)

    # code != 0
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"code": 1, "message": "任务不存在"})))
    with pytest.raises(JobPollFailed):
        await nmr.poll("T-9", ctx)

    # 未配置服务地址（_conn 抛 JobSubmitFailed → 查询路径转 JobPollFailed）
    with pytest.raises(JobPollFailed):
        await nmr.poll("T-9", {"config": {}})


async def test_fetch_result_serializes_payload(connectors, monkeypatch):
    """fetch_result 把上游 result 对象序列化为文本。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"task_id": "T-9",
                                                  "status": "SUCCESS",
                                                  "result": {"peaks": [1.2, 3.4]}}})

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    text = await nmr.fetch_result("T-9", {"config": {"base_url": "http://spec.test"}})
    assert "peaks" in text and "1.2" in text


async def test_cancel_returns_false(connectors):
    """上游无取消接口 → cancel 返回 False（本地仍收敛为 cancelled）。"""
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    assert await nmr.cancel("T-9", {"config": {"base_url": "http://spec.test"}}) is False
