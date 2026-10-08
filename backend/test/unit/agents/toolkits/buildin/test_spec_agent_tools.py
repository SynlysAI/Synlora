"""Spec_Agent 核磁三件套工具的单元测试（MockTransport，不依赖真实上游）。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import spec_agent


def _set_env(monkeypatch, base_url="http://spec.test", with_account=True):
    """配置 Spec_Agent 测试环境；with_account 控制是否配置 AI4MS 平台账号。"""
    monkeypatch.setenv("SPEC_AGENT_BASE_URL", base_url)
    if with_account:
        monkeypatch.setenv("AI4MS_USERNAME", "ai4ms-user")
        monkeypatch.setenv("AI4MS_PASSWORD", "ai4ms-pass")
    else:
        monkeypatch.delenv("AI4MS_USERNAME", raising=False)
        monkeypatch.delenv("AI4MS_PASSWORD", raising=False)
    spec_agent._token_provider.reset()


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    spec_agent._token_provider.reset()


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(spec_agent, "_transport", transport)


@pytest.mark.asyncio
async def test_unconfigured_returns_error_dict(monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_BASE_URL", raising=False)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is False
    assert result["error"] == "spec_agent_unconfigured"
    assert "SPEC_AGENT_BASE_URL" in result["message"]


@pytest.mark.asyncio
async def test_forward_success_with_ai4ms_login(monkeypatch):
    _set_env(monkeypatch)
    seen = {"logins": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            seen["logins"] += 1
            return httpx.Response(200, json={
                "code": 0, "data": {"access_token": "tok-1", "expires_at": 9999999999.0}})
        assert request.url.path == "/api/v1/nmrserver/forward"
        assert request.headers["Authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={
            "code": 0, "message": "ok",
            "data": {"items": [{"smiles": "CCO", "c_shifts": [18.2]}]},
        })

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert "CCO" in result["content"]
    assert seen["logins"] == 1


@pytest.mark.asyncio
async def test_unauthorized_is_distinct_error(monkeypatch):
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={
                "code": 0, "data": {"access_token": "tok-1", "expires_at": 9999999999.0}})
        return httpx.Response(401, json={"detail": "no"})

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_search.ainvoke({"c_shifts_input": "18.2"})
    assert result["error"] == "unauthorized"
    assert "AI4MS" in result["message"]


@pytest.mark.asyncio
async def test_upstream_error_code(monkeypatch):
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={
                "code": 0, "data": {"access_token": "tok-1", "expires_at": 9999999999.0}})
        return httpx.Response(200, json={"code": 500, "message": "模型内部错误"})

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_reverse.ainvoke({"formula": "C2H6O"})
    assert result["error"] == "upstream_error"
    assert "模型内部错误" in result["message"]


@pytest.mark.asyncio
async def test_no_account_omits_auth_header(monkeypatch):
    """未配置 AI4MS 账号：不登录、不发认证头（服务端未开鉴权的部署可用）。"""
    _set_env(monkeypatch, with_account=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/api/v1/auth/login", "未配置账号时不应尝试登录"
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is True
    assert "Authorization" not in (seen["auth"] or "")
