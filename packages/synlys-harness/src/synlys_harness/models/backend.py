"""LLM 后端：协议定义、流事件与 OpenAI 兼容实现。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Protocol, runtime_checkable

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ..types import Message, Role


class ModelProviderConfig(BaseModel):
    """一个 OpenAI 兼容模型服务的连接配置（宿主从 DB 读出后传入）。"""

    name: str
    base_url: str
    api_key: str
    model_id: str
    # 模型是否支持视觉输入（决定 file.read_image 工具是否下发）
    multimodal: bool = False


class TextDelta(BaseModel):
    """流式文本增量。"""

    text: str


class ReasoningDelta(BaseModel):
    """流式思考增量（OpenAI 兼容流的 delta.reasoning_content 字段）。"""

    text: str


class ToolCallChunk(BaseModel):
    """聚合完成的工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_error: str | None = None


class Usage(BaseModel):
    """本次请求 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0


StreamEvent = TextDelta | ReasoningDelta | ToolCallChunk | Usage


@runtime_checkable
class LLMBackend(Protocol):
    """LLM 后端协议：流式产出 StreamEvent。"""

    def stream(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用 LLM。

        Args:
            messages: 完整消息序列。
            tools: function calling schema（None 表示不带工具）。

        Yields:
            TextDelta / ReasoningDelta / ToolCallChunk / Usage。
        """
        ...


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """把内部 Message 转为 OpenAI chat 格式。

    Args:
        messages: 完整消息序列（可含 system/user/assistant/tool 四种角色）。

    Returns:
        OpenAI chat.completions 的 messages 参数列表；assistant 的
        tool_calls 参数序列化为合法 JSON 字符串，tool 消息带 tool_call_id。
    """
    out: list[dict] = []
    for m in messages:
        if m.role is Role.TOOL:
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content or ""})
        elif m.role is Role.ASSISTANT and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                } for tc in m.tool_calls],
            })
        elif m.images:
            # 带图片附件：OpenAI 多模态 content 数组（text + image_url 块）
            out.append({
                "role": m.role.value,
                "content": [
                    {"type": "text", "text": m.content or ""},
                    *[{"type": "image_url",
                       "image_url": {"url": f"data:{img['mime']};base64,{img['base64']}"}}
                      for img in m.images],
                ],
            })
        else:
            out.append({"role": m.role.value, "content": m.content or ""})
    return out


async def aggregate_stream(raw_stream: AsyncIterator[Any]) -> AsyncIterator[StreamEvent]:
    """聚合 openai SDK 原始流：文本/思考直通、tool-call delta 按索引拼装、usage 透传。

    Args:
        raw_stream: chat.completions.create(stream=True) 的异步迭代器。

    Yields:
        StreamEvent。
    """
    calls: dict[int, dict] = {}
    async for chunk in raw_stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None and (
            getattr(usage, "prompt_tokens", None) is not None
            or getattr(usage, "completion_tokens", None) is not None
        ):
            yield Usage(prompt_tokens=usage.prompt_tokens or 0, completion_tokens=usage.completion_tokens or 0)
        for choice in getattr(chunk, "choices", []) or []:
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            text = getattr(delta, "content", None)
            if text:
                yield TextDelta(text=text)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ReasoningDelta(text=reasoning)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if getattr(tc.function, "name", None):
                    slot["name"] += tc.function.name
                if getattr(tc.function, "arguments", None):
                    slot["args"] += tc.function.arguments
    for idx in sorted(calls):
        slot = calls[idx]
        error: str | None = None
        try:
            arguments = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError as exc:
            arguments, error = {}, f"工具参数 JSON 解析失败: {exc}"
        if error is None and not isinstance(arguments, dict):
            arguments, error = {}, "工具参数不是 JSON 对象"
        yield ToolCallChunk(
            id=slot["id"], name=slot["name"], arguments=arguments, arguments_error=error,
        )


# 瞬时错误（值得重试）：连接类（含超时，openai SDK 包装 httpx 异常）、
# 限流 429、服务端 5xx。鉴权/参数类错误重试无意义，直接抛出。
def _is_transient(exc: BaseException) -> bool:
    """判断 LLM 调用异常是否瞬时可重试。

    Args:
        exc: 捕获的异常。

    Returns:
        True 表示可退避重试。
    """
    from openai import APIConnectionError, APIStatusError

    if isinstance(exc, APIConnectionError) or isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


class OpenAICompatibleBackend:
    """OpenAI 兼容流式后端（vLLM/Ollama/云 API 通用）。

    瞬时错误重试（参考 pi RetryPolicy）：连接失败/429/5xx 按 1s/2s/4s 退避
    重试 max_retries 次；**只重试尚未产出任何流事件的请求**——已经开始吐
    token 后失败若重试，会把已发给用户的内容重复一遍。
    """

    def __init__(self, provider: ModelProviderConfig, *,
                 max_retries: int = 2, retry_base_delay: float = 1.0) -> None:
        """初始化后端。

        Args:
            provider: 模型服务连接配置。
            max_retries: 瞬时错误的额外重试次数（总尝试 = 1 + max_retries）。
            retry_base_delay: 退避基数（秒），实际延迟 = base * 2^attempt。
        """
        self._provider = provider
        self._client = AsyncOpenAI(base_url=provider.base_url, api_key=provider.api_key)
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    async def stream(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用（见 LLMBackend 协议；瞬时错误首事件前退避重试）。"""
        kwargs: dict[str, Any] = {
            "model": self._provider.model_id,
            "messages": _to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        total = self._max_retries + 1
        for attempt in range(total):
            delay = self._retry_base_delay * (2 ** attempt)
            try:
                raw = await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                if attempt + 1 >= total or not _is_transient(exc):
                    raise
                await asyncio.sleep(delay)
                continue
            produced = False
            try:
                async for event in aggregate_stream(raw):
                    produced = True
                    yield event
                return
            except Exception as exc:
                # 流中途断且已产出内容：重试会导致重复输出，直接抛
                if produced or attempt + 1 >= total or not _is_transient(exc):
                    raise
                await asyncio.sleep(delay)
