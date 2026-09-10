"""LLM 后端单测：tool-call delta 聚合与流式文本。"""
from synlys_harness.models.backend import ModelProviderConfig, aggregate_stream


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
