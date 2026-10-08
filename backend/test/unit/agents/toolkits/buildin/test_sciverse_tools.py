"""Sciverse 工具的单元测试：认证、参数校验、渲染截断、附件落盘。"""

from types import SimpleNamespace

import httpx
import pytest

from yuxi.agents.toolkits.buildin import sciverse


WORKDIR = "/home/gem/user-data/projects/2026-01-01_00-00-00_demo"

_SCOPE = {
    "runtime_scope_id": "run-1",
    "uid": "u-1",
    "workdir_relative_path": "projects/2026-01-01_00-00-00_demo",
}


def _runtime():
    return SimpleNamespace(config={"configurable": dict(_SCOPE)}, context=None, state=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SCIVERSE_BASE_URL", "https://sciverse.test")
    monkeypatch.setenv("SCIVERSE_API_TOKEN", "sv-tok")


def _install(monkeypatch, handler):
    monkeypatch.setattr(sciverse, "_transport", httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_unconfigured(monkeypatch):
    monkeypatch.delenv("SCIVERSE_API_TOKEN", raising=False)
    result = await sciverse.sciverse_agentic_search.ainvoke({"query": "polymer"})
    assert result["ok"] is False
    assert result["error"] == "sciverse_unconfigured"
    assert "SCIVERSE_API_TOKEN" in result["message"]


@pytest.mark.asyncio
async def test_agentic_search_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agentic-search"
        assert request.headers["Authorization"] == "Bearer sv-tok"
        return httpx.Response(200, json={"snippets": [{"doc_id": "d1", "text": "hello"}]})

    _install(monkeypatch, handler)
    result = await sciverse.sciverse_agentic_search.ainvoke({"query": "polymer"})
    assert result["ok"] is True
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_meta_search_rejects_query_with_sort(monkeypatch):
    result = await sciverse.sciverse_meta_search.ainvoke(
        {"query": "x", "sort": [{"field": "year", "order": "desc"}]})
    assert result["ok"] is False
    assert result["error"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_not_found(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(404, json={"detail": "gone"}))
    result = await sciverse.sciverse_content.ainvoke({"doc_id": "nope"})
    assert result["ok"] is False
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_render_truncation(monkeypatch):
    big = {"text": "x" * 60000}

    _install(monkeypatch, lambda request: httpx.Response(200, json=big))
    result = await sciverse.sciverse_content.ainvoke({"doc_id": "d1"})
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["content"]) <= sciverse.RENDER_MAX_CHARS + 200


@pytest.mark.asyncio
async def test_resource_saved_to_workspace(monkeypatch):
    from yuxi.agents.toolkits.buildin import _workspace_io

    uploaded = {}

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def upload_authorized_file_from_path(self, virtual_path, local_path):
            uploaded[virtual_path] = open(local_path, "rb").read()

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)
    _install(monkeypatch, lambda request: httpx.Response(
        200, content=b"PDF-BYTES", headers={"content-type": "application/pdf"}))

    # 直调 coroutine 以注入 fake runtime（ainvoke 不注入 ToolRuntime）
    result = await sciverse.sciverse_resource.coroutine(
        file_name="paper.pdf", save_relative_path="outputs/paper.pdf", runtime=_runtime())
    assert result["ok"] is True
    assert result["saved_path"] == "outputs/paper.pdf"
    assert uploaded[f"{WORKDIR}/outputs/paper.pdf"] == b"PDF-BYTES"


@pytest.mark.asyncio
async def test_resource_rejects_bad_names(monkeypatch):
    for bad in ("../x.pdf", "/abs.pdf", "a\\b.pdf", "..\\..\\x"):
        result = await sciverse.sciverse_resource.coroutine(file_name=bad, runtime=_runtime())
        assert result["ok"] is False
        assert result["error"] == "invalid_arguments"
