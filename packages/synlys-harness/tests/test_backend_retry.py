"""OpenAICompatibleBackend 瞬时错误重试单测。"""
from types import SimpleNamespace

import httpx
import openai
import pytest

from synlys_harness.models.backend import (
    ModelProviderConfig, OpenAICompatibleBackend, TextDelta, Usage,
)
from synlys_harness.types import Message, Role


def _chunk(text: str | None, usage=None) -> SimpleNamespace:
    """构造 openai SDK 形状的流 chunk。"""
    delta = SimpleNamespace(content=text, reasoning_content=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=None)
    return SimpleNamespace(choices=[choice], usage=usage)


async def _raw(chunks, fail_at: int | None = None, exc: Exception | None = None):
    """构造原始异步流；fail_at 后抛 exc。"""
    for i, c in enumerate(chunks):
        if fail_at is not None and i == fail_at:
            raise exc
        yield c


def _backend(behaviors) -> tuple[OpenAICompatibleBackend, "FakeClient"]:
    """构造注入 FakeClient 的后端（重试间隔 0 加速测试）。"""
    client = FakeClient(behaviors)
    b = OpenAICompatibleBackend(
        ModelProviderConfig(name="t", base_url="http://x", api_key="k", model_id="m"),
        max_retries=2, retry_base_delay=0,
    )
    b._client = client  # noqa: SLF001 测试注入
    return b, client


class FakeClient:
    """按剧本响应（抛异常或返回异步流）的假 openai 客户端。"""

    def __init__(self, behaviors: list):
        """初始化。

        Args:
            behaviors: 每次 create 的剧本项（Exception=抛出，否则=返回的异步流）。
        """
        self.behaviors = behaviors
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls += 1
        item = self.behaviors.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _conn_error() -> openai.APIConnectionError:
    """构造连接异常（openai SDK 要求携带 request）。"""
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x/v1"))


async def test_retry_on_connect_error_then_success(monkeypatch):
    """连接失败一次后重试成功。"""
    ok_stream = _raw([
        _chunk("你"), _chunk("好"),
        _chunk(None, usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2)),
    ])
    b, client = _backend([_conn_error(), ok_stream])
    events = [ev async for ev in b.stream([Message(role=Role.USER, content="hi")])]
    assert client.calls == 2
    assert TextDelta(text="你好") in events or (
        isinstance(events[0], TextDelta) and events[0].text == "你")
    assert isinstance(events[-1], Usage)


async def test_no_retry_after_first_event():
    """已产出首个流事件后中断：不重试（避免重复输出），异常上抛。"""
    broken = _raw([_chunk("半"), _chunk("好")], fail_at=1, exc=_conn_error())
    b, client = _backend([broken])
    got = []
    with pytest.raises(openai.APIConnectionError):
        async for ev in b.stream([Message(role=Role.USER, content="hi")]):
            got.append(ev)
    assert client.calls == 1 and len(got) == 1


async def test_no_retry_on_auth_error(monkeypatch):
    """鉴权类（401）非瞬时错误：立即上抛不重试。"""
    auth_err = openai.AuthenticationError(
        message="bad key", response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    b, client = _backend([auth_err])
    with pytest.raises(openai.AuthenticationError):
        async for _ in b.stream([Message(role=Role.USER, content="hi")]):
            pass
    assert client.calls == 1


async def test_retry_exhausted(monkeypatch):
    """瞬时错误持续：重试耗尽后上抛。"""
    b, client = _backend([_conn_error(), _conn_error(), _conn_error()])
    with pytest.raises(openai.APIConnectionError):
        async for _ in b.stream([Message(role=Role.USER, content="hi")]):
            pass
    assert client.calls == 3  # 1 + max_retries(2)
