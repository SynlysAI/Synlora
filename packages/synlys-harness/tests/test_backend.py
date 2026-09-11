"""LLM 后端单测：tool-call delta 聚合与流式文本。"""
import json

from synlys_harness.models.backend import (
    ModelProviderConfig,
    _to_openai_messages,
    aggregate_stream,
)
from synlys_harness.types import Message, Role, ToolCall


def test_provider_config():
    """配置模型可构造。"""
    p = ModelProviderConfig(
        name="test", base_url="http://localhost:9999/v1", api_key="sk-x", model_id="qwen3",
    )
    assert p.model_id == "qwen3"


class _Chunk:
    """模拟 openai 流式 chunk。"""

    def __init__(self, deltas, usage=None):
        self.choices = [type("C", (), {"delta": type("D", (), deltas)})()]
        self.usage = usage


async def _aiter(items):
    for x in items:
        yield x


async def test_aggregate_text_and_usage():
    """文本 delta 直通，usage 透传。"""
    chunks = [
        _Chunk({"content": "你"}), _Chunk({"content": "好"}),
        _Chunk({}, usage=type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    kinds = [(type(e).__name__, getattr(e, "text", None)) for e in events]
    assert kinds == [("TextDelta", "你"), ("TextDelta", "好"), ("Usage", None)]
    assert events[-1].prompt_tokens == 10


async def test_aggregate_reasoning_deltas():
    """reasoning_content 增量聚合为 ReasoningDelta，与 TextDelta 共存且保持到达顺序。"""
    chunks = [
        _Chunk({"reasoning_content": "想"}),
        _Chunk({"reasoning_content": "一想"}),
        _Chunk({"content": "答"}),
        _Chunk({"content": "案"}),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    kinds = [(type(e).__name__, getattr(e, "text", None)) for e in events]
    assert kinds == [
        ("ReasoningDelta", "想"), ("ReasoningDelta", "一想"),
        ("TextDelta", "答"), ("TextDelta", "案"),
    ]


async def test_aggregate_tool_call_deltas():
    """分片到达的 tool call 参数被聚合成完整 ToolCallChunk。"""
    chunks = [
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 0, "id": "c1", "function": type("F", (), {"name": "python.run", "arguments": '{"code"'}),
        })()]}),
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 0, "id": None, "function": type("F", (), {"name": None, "arguments": ': "print(1)"}'}),
        })()]}),
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 1, "id": "c2", "function": type("F", (), {"name": "file.read", "arguments": '{"path":"a"}'}),
        })()]}),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    calls = [e for e in events if type(e).__name__ == "ToolCallChunk"]
    assert len(calls) == 2
    assert calls[0].id == "c1" and calls[0].name == "python.run"
    assert calls[0].arguments == {"code": "print(1)"}
    assert calls[1].name == "file.read"


async def test_aggregate_invalid_tool_args_become_error_text():
    """非法 JSON 参数聚合为带 error 的 ToolCallChunk（由 loop 转为错误结果）。"""
    chunks = [_Chunk({"tool_calls": [type("TC", (), {
        "index": 0, "id": "c9", "function": type("F", (), {"name": "x", "arguments": "{oops"}),
    })()]})]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    call = events[0]
    assert call.arguments_error is not None


async def test_aggregate_non_dict_args_become_error():
    """合法 JSON 但非对象（如数组）的参数聚合为空参数 + error，而非抛 ValidationError。"""
    chunks = [_Chunk({"tool_calls": [type("TC", (), {
        "index": 0, "id": "c1", "function": type("F", (), {"name": "x", "arguments": "[1,2]"}),
    })()]})]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    call = events[0]
    assert call.arguments == {}
    assert call.arguments_error is not None


async def test_none_usage_object_ignored():
    """全 None 字段的 usage 对象不产出 Usage 事件（每响应只保留末尾真实 usage）。"""
    chunks = [
        _Chunk({"content": "hi"}, usage=type("U", (), {"prompt_tokens": None, "completion_tokens": None})()),
        _Chunk({}, usage=type("U", (), {"prompt_tokens": 10, "completion_tokens": 2})()),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    usages = [e for e in events if type(e).__name__ == "Usage"]
    assert len(usages) == 1
    assert usages[0].prompt_tokens == 10


async def test_aggregate_calls_emitted_in_index_order():
    """工具调用按 index 数值序产出（乱序到达时）。"""
    chunks = [
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 1, "id": "c2", "function": type("F", (), {"name": "b", "arguments": "{}"}),
        })()]}),
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 0, "id": "c1", "function": type("F", (), {"name": "a", "arguments": "{}"}),
        })()]}),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    calls = [e for e in events if type(e).__name__ == "ToolCallChunk"]
    assert [c.name for c in calls] == ["a", "b"]


def test_to_openai_messages_roundtrip():
    """system/user/assistant(tool_calls)/tool 四类消息转换为 OpenAI 格式且参数可往返。"""
    messages = [
        Message(role=Role.SYSTEM, content="你是助手"),
        Message(role=Role.USER, content="你好"),
        Message(role=Role.ASSISTANT, content=None, tool_calls=[
            ToolCall(id="c1", name="python.run", arguments={"code": "print(1)"}),
        ]),
        Message(role=Role.TOOL, content="1", tool_call_id="c1"),
    ]
    out = _to_openai_messages(messages)
    assert out[0] == {"role": "system", "content": "你是助手"}
    assert out[1] == {"role": "user", "content": "你好"}
    assistant = out[2]
    assert assistant["role"] == "assistant"
    args_json = assistant["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args_json, str)
    assert json.loads(args_json) == {"code": "print(1)"}
    assert out[3]["role"] == "tool"
    assert out[3]["tool_call_id"] == "c1"
