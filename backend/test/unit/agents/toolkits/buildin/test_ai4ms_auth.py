"""AI4MS 平台账号登录换 token 公共模块的单元测试。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import _ai4ms_auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("AI4MS_USERNAME", "u1")
    monkeypatch.setenv("AI4MS_PASSWORD", "p1")
    provider = _ai4ms_auth.Ai4msTokenProvider()
    provider.reset()
    yield provider
    provider.reset()


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "code": 0, "data": {"access_token": "tok-a", "expires_at": 9999999999.0}})


@pytest.mark.asyncio
async def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("AI4MS_USERNAME", "alice")
    monkeypatch.setenv("AI4MS_PASSWORD", "secret")
    assert _ai4ms_auth.Ai4msTokenProvider.credentials() == ("alice", "secret")
    monkeypatch.delenv("AI4MS_USERNAME", raising=False)
    monkeypatch.delenv("AI4MS_PASSWORD", raising=False)
    assert _ai4ms_auth.Ai4msTokenProvider.credentials() == ("", "")


@pytest.mark.asyncio
async def test_token_cached_per_provider(_clean):
    provider = _clean
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            logins["n"] += 1
            return _login_ok(request)
        raise AssertionError("unexpected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        t1 = await provider.ensure_token(client, "http://svc-a", "u1", "p1")
        t2 = await provider.ensure_token(client, "http://svc-a", "u1", "p1")
    assert t1 == t2 == "tok-a"
    assert logins["n"] == 1


@pytest.mark.asyncio
async def test_force_relogin_after_401(_clean):
    provider = _clean
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        logins["n"] += 1
        return httpx.Response(200, json={
            "code": 0, "data": {"access_token": f"tok-{logins['n']}",
                                 "expires_at": 9999999999.0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await provider.ensure_token(client, "http://svc", "u1", "p1") == "tok-1"
        assert await provider.force_relogin(client, "http://svc", "u1", "p1") == "tok-2"
        assert await provider.ensure_token(client, "http://svc", "u1", "p1") == "tok-2"
    assert logins["n"] == 2


@pytest.mark.asyncio
async def test_login_failure_raises_readable_error(_clean):
    provider = _clean

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 1001, "message": "账号被停用"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="账号被停用"):
            await provider.ensure_token(client, "http://svc", "u1", "p1")


@pytest.mark.asyncio
async def test_expired_cache_relogin(_clean):
    provider = _clean
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        # 第一次签发立即过期的 token，第二次签发长效 token
        expires = 0.0 if state["n"] == 1 else 9999999999.0
        return httpx.Response(200, json={
            "code": 0, "data": {"access_token": f"tok-{state['n']}", "expires_at": expires}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await provider.ensure_token(client, "http://svc", "u1", "p1")
        second = await provider.ensure_token(client, "http://svc", "u1", "p1")
    assert first == "tok-1"
    assert second == "tok-2"  # 缓存已过期，重新登录
