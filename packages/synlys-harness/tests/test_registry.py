"""ToolRegistry 单测。"""
import pytest

from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import Permission, ToolResult


@tool(
    name="echo",
    description="回声工具",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    timeout_s=5,
)
async def echo(ctx, args):
    """回声。"""
    return ToolResult(ok=True, content=args["text"])


def test_register_and_get():
    """装饰器注册后可按名获取。"""
    reg = ToolRegistry()
    reg.register(echo)
    assert reg.get("echo").name == "echo"
    assert reg.get("echo").permission is Permission.ALLOW


def test_duplicate_register_raises():
    """重名注册抛 ValueError。"""
    reg = ToolRegistry()
    reg.register(echo)
    with pytest.raises(ValueError):
        reg.register(echo)


def test_llm_schemas_filter():
    """llm_schemas 只包含白名单内工具且为 function calling 格式。"""
    reg = ToolRegistry()
    reg.register(echo)
    schemas = reg.llm_schemas(allowed=["echo"])
    assert schemas == [{
        "type": "function",
        "function": {"name": "echo", "description": "回声工具", "parameters": {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
        }},
    }]
    assert reg.llm_schemas(allowed=["not-registered"]) == []


def test_unregister():
    """注销后 get 抛 KeyError。"""
    reg = ToolRegistry()
    reg.register(echo)
    reg.unregister("echo")
    with pytest.raises(KeyError):
        reg.get("echo")
