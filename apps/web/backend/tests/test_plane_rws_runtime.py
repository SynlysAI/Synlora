"""Plane Research Workspace 共享运行适配层契约。"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from synlys_harness import ExecutionRequest, ToolResult

from app.services.plane_rws_runtime import PlaneRWSRuntime, PlaneRWSRuntimeError


class FakeResponse:
    """轻量 HTTP 响应。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        """返回预设 JSON。"""
        return self._payload


class FakeHTTPClient:
    """按路径返回 Plane / Runner 契约响应。"""

    def __init__(self, workspace_root: Path, assets_root: Path, verify_status=200):
        self.calls = []
        self.verify_status = verify_status
        self.workspace_root = workspace_root
        self.assets_root = assets_root

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, *, json=None, headers=None, timeout=None):
        """记录调用并返回固定控制面结果。"""
        self.calls.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/verify/"):
            if self.verify_status != 200:
                return FakeResponse(401)
            return FakeResponse(
                payload={
                    "data": {
                        "run_id": "run-1",
                        "study_id": "study-1",
                        "working_copy_id": "copy-1",
                        "grant_id": "grant-1",
                        "branch": "agent/run-1",
                        "capabilities": ["files.read", "files.write", "run.execute", "assets.read"],
                        "asset_manifest_digest": "a" * 64,
                        "asset_items": [{"path": "data/a.csv", "sha256": "b" * 64, "size": 3}],
                    }
                }
            )
        if url.endswith("/binding.resolve"):
            return FakeResponse(
                payload={
                    "ok": True,
                    "data": {"grant_id": "grant-1", "workspace_path": str(self.workspace_root)},
                }
            )
        if url.endswith("/binding.execution-start") or url.endswith("/binding.execution-finish"):
            return FakeResponse(payload={"ok": True, "data": {"stopped": True}})
        if url.endswith("/assets.materialize"):
            assert json["items"][0]["path"] == "data/a.csv"
            return FakeResponse(payload={"ok": True, "data": {"assets_path": str(self.assets_root)}})
        if url.endswith("/finalize/"):
            return FakeResponse(payload={"data": {"run": {"run_id": "run-1", "status": "SUCCEEDED"}}})
        raise AssertionError(f"未知测试 URL: {url}")


class FakeExecutor:
    """记录执行请求的 Docker 形态执行器。"""

    sandbox = "docker"

    def __init__(self):
        self.requests = []

    async def execute(self, request: ExecutionRequest):
        """返回成功结果并保留挂载请求。"""
        self.requests.append(request)
        return ToolResult(ok=True, content="shared output")

    async def cleanup_execution(self, execution_id):
        """测试执行器确认没有遗留可写容器。"""
        return True


@pytest.mark.asyncio
async def test_rws_run_uses_verified_shared_workspace_and_assets(store, tmp_path):
    """绑定复验、同盘挂载、资产物化、run receipt 全链路闭环。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    http = FakeHTTPClient(workspace, assets)
    executor = FakeExecutor()
    factory_kwargs = {}
    settings = SimpleNamespace(
        plane_request_timeout_seconds=3.0,
        plane_rws_api_url="http://plane",
        plane_rws_runner_url="http://runner",
        plane_rws_runner_token="runner-service-token",
    )
    runtime = PlaneRWSRuntime(
        settings,
        store,
        lambda: _async_value(executor),
        http_client_factory=lambda **kwargs: (factory_kwargs.update(kwargs), http)[1],
    )
    result = await runtime.execute(
        run_id="run-1",
        token="short-term-token-value",
        argv=["/bin/sh", "-c", "cat /assets/data/a.csv > /workspace/output/result.txt"],
        cwd=".",
        changed_paths=["output/result.txt"],
        outputs={"result_path": "output/result.txt"},
        request_id="receipt-1",
    )

    assert result["ok"] is True
    assert result["branch"] == "agent/run-1"
    assert result["execution_workspace"] == str(workspace.resolve())
    assert result["execution_assets"] == str(assets)
    assert result["plane_receipt"]["run"]["status"] == "SUCCEEDED"
    request = executor.requests[0]
    assert request.workspace_root == workspace.resolve()
    assert factory_kwargs["timeout"] == 1800.0 + 600.0
    assert request.resources[0].target == PurePosixPath("/assets")
    assert http.calls[1]["headers"]["Authorization"] == "Bearer runner-service-token"
    assert http.calls[-1]["json"]["changed_paths"] == ["output/result.txt"]
    stored = await store.get("plane_rws_runs", "run-1")
    assert stored["grant_id"] == "grant-1"
    assert "short-term-token-value" not in str(stored)


@pytest.mark.asyncio
async def test_invalid_plane_binding_never_reaches_personal_workspace(store):
    """Plane 复验失败时直接失败，不解析个人目录、不执行沙箱。"""
    workspace = Path("/tmp") / "must-not-exist"
    assets = Path("/tmp") / "assets-must-not-exist"
    http = FakeHTTPClient(workspace, assets, verify_status=401)
    executor = FakeExecutor()
    settings = SimpleNamespace(
        plane_request_timeout_seconds=3.0,
        plane_rws_api_url="http://plane",
        plane_rws_runner_url="http://runner",
        plane_rws_runner_token="runner-service-token",
    )
    runtime = PlaneRWSRuntime(
        settings,
        store,
        lambda: _async_value(executor),
        http_client_factory=lambda **kwargs: http,
    )
    with pytest.raises(PlaneRWSRuntimeError):
        await runtime.execute(run_id="run-1", token="short-term-token-value", argv=["/bin/true"])
    assert executor.requests == []
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_container_cleanup_before_releasing_pin(store, tmp_path):
    """取消执行时必须等创建线程和容器清理结束，才向 Runner 确认停止。"""
    http = FakeHTTPClient(tmp_path, tmp_path)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    class BlockingExecutor(FakeExecutor):
        """模拟在取消时仍需收尾的 Docker 执行器。"""

        async def execute(self, request):
            """等待取消后完成清理。"""
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        async def cleanup_execution(self, execution_id):
            """仅在执行器收尾后确认停止。"""
            assert cleaned.is_set()
            assert not any(call["url"].endswith("/binding.execution-finish") for call in http.calls)
            return True

    settings = SimpleNamespace(
        plane_request_timeout_seconds=3.0,
        plane_rws_api_url="http://plane",
        plane_rws_runner_url="http://runner",
        plane_rws_runner_token="runner-service-token",
    )
    runtime = PlaneRWSRuntime(settings, store, lambda: _async_value(BlockingExecutor()), http_client_factory=lambda **kw: http)
    task = asyncio.create_task(runtime.execute(run_id="run-1", token="short-term-token-value", argv=["/bin/true"]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert http.calls[-1]["url"].endswith("/binding.execution-finish")
    assert not any(call["url"].endswith("/finalize/") for call in http.calls)


@pytest.mark.asyncio
async def test_revoked_binding_stops_execution_and_confirms_cleanup(store, tmp_path):
    """控制面撤销授权后，监测循环取消沙箱并回报停止。"""
    http = FakeHTTPClient(tmp_path, tmp_path)
    original_post = http.post
    resolves = 0
    cancelled = asyncio.Event()

    async def revoke_after_start(url, **kwargs):
        """首次解析成功，后续轮询模拟授权撤销。"""
        nonlocal resolves
        if url.endswith("/binding.resolve"):
            resolves += 1
            if resolves > 1:
                return FakeResponse(403, {"ok": False, "message": "revoked"})
        return await original_post(url, **kwargs)

    http.post = revoke_after_start

    class RevocableExecutor(FakeExecutor):
        """模拟等待控制面取消的长任务。"""

        async def execute(self, request):
            """保持执行直至监测循环取消。"""
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    settings = SimpleNamespace(
        plane_request_timeout_seconds=3.0,
        plane_rws_api_url="http://plane",
        plane_rws_runner_url="http://runner",
        plane_rws_runner_token="runner-service-token",
    )
    runtime = PlaneRWSRuntime(settings, store, lambda: _async_value(RevocableExecutor()), http_client_factory=lambda **kw: http)
    with pytest.raises(PlaneRWSRuntimeError, match="revoked"):
        await runtime.execute(run_id="run-1", token="short-term-token-value", argv=["/bin/true"])
    assert cancelled.is_set()
    assert http.calls[-1]["url"].endswith("/binding.execution-finish")


@pytest.mark.asyncio
async def test_rws_endpoint_requires_plane_service_token(app, client):
    """执行入口只接受 Plane 服务凭证，不暴露给普通用户。"""
    app.state.settings.plane_service_token = "plane-service-token"
    app.state.plane_rws_runtime = SimpleNamespace(
        execute=_reject_runtime_call,
    )
    denied = await client.post(
        "/api/v1/research/rws-runs",
        json={"run_id": "run-1", "token": "x" * 20, "argv": ["/bin/true"]},
    )
    assert denied.status_code == 403


async def _async_value(value):
    """包装同步值为异步 resolver。"""
    return value


async def _reject_runtime_call(**kwargs):
    """测试中禁止进入真实执行。"""
    raise AssertionError("未授权请求不应调用 runtime")
