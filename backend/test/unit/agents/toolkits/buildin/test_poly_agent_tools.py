"""Poly_Agent 工具的单元测试：登录缓存、401 重登、轮询兜底、未配置。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import poly_agent


@pytest.fixture(autouse=True)
def _clean_token_cache():
    poly_agent._reset_token_cache()
    yield
    poly_agent._reset_token_cache()


def _env(monkeypatch, complete=True):
    monkeypatch.setenv("POLY_AGENT_BASE_URL", "http://poly.test" if complete else "")
    monkeypatch.setenv("AI4MS_USERNAME", "u" if complete else "")
    monkeypatch.setenv("AI4MS_PASSWORD", "p" if complete else "")


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "code": 0, "data": {"access_token": "tok-a", "expires_at": 9999999999.0}})


@pytest.mark.asyncio
async def test_unconfigured(monkeypatch):
    _env(monkeypatch, complete=False)
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "CCOc1ccc"})
    assert result["ok"] is False
    assert result["error"] == "poly_agent_unconfigured"
    assert "POLY_AGENT_BASE_URL" in result["message"]
    assert "AI4MS_USERNAME" in result["message"]


@pytest.mark.asyncio
async def test_login_token_cached(monkeypatch):
    _env(monkeypatch)
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            logins["n"] += 1
            return _login_ok(request)
        if request.url.path.startswith("/api/v1/research-engine/algorithm-runs"):
            assert request.headers["Authorization"] == "Bearer tok-a"
            return httpx.Response(200, json={
                "code": 0,
                "data": {"run_id": "r-1", "status": "completed",
                         "output_summary": {"tg_c": 220.5}},
            })
        raise AssertionError(f"unexpected {request.url.path}")

    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    for _ in range(3):
        result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "CCOc1ccc"})
        assert result["ok"] is True
    assert logins["n"] == 1  # 平台级账号缓存，三次调用只登录一次


@pytest.mark.asyncio
async def test_401_forces_relogin(monkeypatch):
    _env(monkeypatch)
    state = {"logins": 0, "first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            state["logins"] += 1
            return httpx.Response(200, json={
                "code": 0, "data": {"access_token": f"tok-{state['logins']}",
                                     "expires_at": 9999999999.0}})
        if request.url.path.startswith("/api/v1/research-engine/algorithm-runs"):
            if state["first"]:
                state["first"] = False
                return httpx.Response(401, json={"detail": "revoked"})
            return httpx.Response(200, json={
                "code": 0, "data": {"run_id": "r-2", "status": "completed",
                                     "output_summary": {"tg_c": 210.0}}})
        raise AssertionError(f"unexpected {request.url.path}")

    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "X"})
    assert result["ok"] is True
    assert state["logins"] == 2  # 缓存 token 被吊销后强制重登一次并重试


@pytest.mark.asyncio
async def test_async_fallback_polling(monkeypatch):
    """上游若改异步（返回 queued），轮询详情接口直到终态。"""
    _env(monkeypatch)
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return _login_ok(request)
        if request.method == "POST":
            return httpx.Response(200, json={
                "code": 0, "data": {"run_id": "r-3", "status": "queued"}})
        polls["n"] += 1
        status = "running" if polls["n"] < 2 else "completed"
        return httpx.Response(200, json={
            "code": 0, "data": {"run_id": "r-3", "status": status,
                                 "output_summary": {"tg_c": 205.0}}})

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(poly_agent.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "X"})
    assert result["ok"] is True
    assert polls["n"] == 2


@pytest.mark.asyncio
async def test_failed_run_returns_reason(monkeypatch):
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return _login_ok(request)
        return httpx.Response(200, json={
            "code": 0, "data": {"run_id": "r-4", "status": "failed",
                                 "error": {"message": "SMILES 无法解析", "detail": "第 3 个字符"}}})

    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "X"})
    assert result["ok"] is False
    assert result["error"] == "upstream_error"
    assert "SMILES 无法解析" in result["message"] and "第 3 个字符" in result["message"]
    assert result["run_id"] == "r-4"
