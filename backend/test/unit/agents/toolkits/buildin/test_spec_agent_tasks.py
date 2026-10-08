"""Spec_Agent 五类谱图同步任务工具的单元测试（状态机/超时/失败原因/路径安全）。"""

import itertools
from types import SimpleNamespace

import httpx
import pytest

from yuxi.agents.toolkits.buildin import spec_agent


WORKDIR = "/projects/2026-01-01_00-00-00_demo"

_SCOPE = {"runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}


def _runtime():
    return SimpleNamespace(config={"configurable": dict(_SCOPE)}, context=None, state=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SPEC_AGENT_BASE_URL", "http://spec.test")
    monkeypatch.delenv("AI4MS_USERNAME", raising=False)
    monkeypatch.delenv("AI4MS_PASSWORD", raising=False)
    monkeypatch.delenv("SPEC_TASK_MAX_WAIT_SECONDS", raising=False)
    spec_agent._token_provider.reset()


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(spec_agent.asyncio, "sleep", _instant_sleep)


def _install(monkeypatch, handler):
    monkeypatch.setattr(spec_agent, "_transport", httpx.MockTransport(handler))


def _install_file(monkeypatch, content=b"FAKE-SPECTRUM"):
    from yuxi.agents.toolkits.buildin import _workspace_io

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def download_authorized_file_to_path(self, virtual_path, local_path, max_bytes):
            with open(local_path, "wb") as fh:
                fh.write(content)

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)


def _stateful_handler():
    """上传→提交→(pending→running→success)→取结果 的完整状态机。"""
    state = {"uploaded": 0, "status": "pending", "result": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files/upload":
            state["uploaded"] += 1
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "f-1"}})
        if path.startswith("/api/v1/tasks/") and path.endswith("/result"):
            if state["status"] == "failed":
                return httpx.Response(200, json={
                    "code": 0,
                    "data": {"result": None, "error": {
                        "error_code": "E1", "error_message": "解析失败", "error_detail": "峰数不足"}},
                })
            return httpx.Response(200, json={
                "code": 0, "data": {"result": {"peaks": [1, 2, 3]}, "error": None}})
        if request.method == "POST" and path.startswith("/api/v1/tasks/"):
            body = request.read().decode()
            assert '"file_id":"f-1"' in body, f"提交体应含 file_id: {body}"
            state["status"] = "pending"
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-9"}})
        # GET /api/v1/tasks/{id}
        if state["status"] == "pending":
            state["status"] = "running"
        elif state["status"] == "running":
            state["status"] = state.get("final", "success")
        return httpx.Response(200, json={"code": 0, "data": {"status": state["status"]}})

    return handler, state


@pytest.mark.asyncio
async def test_full_success_flow(monkeypatch):
    handler, state = _stateful_handler()
    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    result = await spec_agent._run_spectra_task(
        "nmr", None, "uploads/nmr.txt", {"nucleus": "13C"}, _runtime())
    assert result["ok"] is True
    assert result["task_id"] == "t-9"
    assert result["result"] == {"peaks": [1, 2, 3]}
    assert state["uploaded"] == 1


@pytest.mark.asyncio
async def test_failed_task_returns_upstream_reason(monkeypatch):
    handler, state = _stateful_handler()
    state["final"] = "failed"
    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    result = await spec_agent._run_spectra_task(
        "ir", None, "uploads/ir.csv", None, _runtime())
    assert result["ok"] is False
    assert result["error"] == "upstream_failed"
    assert "解析失败" in result["message"] and "峰数不足" in result["message"]


def test_timeout_budget_clamped(monkeypatch):
    assert spec_agent._task_max_wait_s() == 1500.0
    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "999999")
    assert spec_agent._task_max_wait_s() == 3300.0
    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "1")
    assert spec_agent._task_max_wait_s() == 60.0


@pytest.mark.asyncio
async def test_timeout_returns_task_id(monkeypatch):
    def stuck(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "f-1"}})
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-stuck"}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "RUNNING"}})

    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "60")
    # 递增 fake clock：每次调用 +10000s，让 deadline 很快被越过
    counter = itertools.count(start=1_000_000, step=10_000)
    monkeypatch.setattr(spec_agent.time, "monotonic", lambda: next(counter))
    _install(monkeypatch, stuck)
    _install_file(monkeypatch)
    result = await spec_agent._run_spectra_task(
        "lcms", None, "uploads/lcms.dat", None, _runtime())
    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert "t-" in result["message"] or "task_id" in result


def test_five_task_tools_registered_with_distinct_names():
    """工厂生成的五个工具必须以独立名字注册（防止闭包同名互相覆盖）。"""
    from yuxi.agents.toolkits.registry import get_all_tool_instances

    names = {t.name for t in get_all_tool_instances()}
    assert {
        "spec_task_nmr",
        "spec_task_ir",
        "spec_task_gpc",
        "spec_task_raman",
        "spec_task_lcms",
    } <= names


@pytest.mark.asyncio
async def test_raman_default_mode_applied(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "f-1"}})
        if request.method == "POST":
            captured["body"] = request.read().decode()
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-1"}})
        if path.endswith("/result"):
            return httpx.Response(200, json={"code": 0, "data": {"result": {}, "error": None}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "success"}})

    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    await spec_agent._run_spectra_task(
        "raman", "function_groups", "uploads/r.txt", None, _runtime())
    assert '"mode":"function_groups"' in captured["body"]

    captured.clear()
    await spec_agent._run_spectra_task(
        "raman", "function_groups", "uploads/r.txt", {"mode": "greedy_decode"}, _runtime())
    assert "greedy_decode" in captured["body"]


@pytest.mark.asyncio
async def test_path_escape_rejected(monkeypatch):
    with pytest.raises(ValueError):
        await spec_agent._run_spectra_task("nmr", None, "../escape.txt", None, _runtime())
