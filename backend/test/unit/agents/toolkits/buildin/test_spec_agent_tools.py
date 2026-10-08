"""Spec_Agent 核磁三件套工具的单元测试（MockTransport，不依赖真实上游）。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import spec_agent


def _set_env(monkeypatch, base_url="http://spec.test", token="tok-1"):
    monkeypatch.setenv("SPEC_AGENT_BASE_URL", base_url)
    if token is None:
        monkeypatch.delenv("SPEC_AGENT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SPEC_AGENT_TOKEN", token)


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
async def test_forward_success(monkeypatch):
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
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


@pytest.mark.asyncio
async def test_unauthorized_is_distinct_error(monkeypatch):
    _set_env(monkeypatch)
    _install_transport(monkeypatch, lambda request: httpx.Response(401, json={"detail": "no"}))
    result = await spec_agent.spec_nmr_search.ainvoke({"c_shifts_input": "18.2"})
    assert result["error"] == "unauthorized"
    assert "SPEC_AGENT_TOKEN" in result["message"]


@pytest.mark.asyncio
async def test_upstream_error_code(monkeypatch):
    _set_env(monkeypatch)
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"code": 500, "message": "模型内部错误"}),
    )
    result = await spec_agent.spec_nmr_reverse.ainvoke({"formula": "C2H6O"})
    assert result["error"] == "upstream_error"
    assert "模型内部错误" in result["message"]


@pytest.mark.asyncio
async def test_empty_token_omits_auth_header(monkeypatch):
    _set_env(monkeypatch, token=None)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is True
    assert "Authorization" not in (seen["auth"] or "")
