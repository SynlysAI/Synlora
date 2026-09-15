"""共享工具注册表测试（运行装配与白名单校验必须同一实例）。"""
from __future__ import annotations


def test_shared_registry_singleton():
    """两侧取到同一实例，且含内置工具。"""
    # 校验侧挂在校验函数所在的 deps 上（assistants_api / me_api 都复用它）
    from app.api import deps
    from app.services import agent_service
    from app.services.tool_registry import PIPELINE, REGISTRY

    assert deps._REGISTRY is REGISTRY
    assert agent_service._REGISTRY is REGISTRY
    assert agent_service._PIPELINE is PIPELINE
    assert PIPELINE._registry is REGISTRY
    assert "python.run" in REGISTRY.names
    assert "file.read" in REGISTRY.names


def test_registry_is_empty_of_plugin_tools_by_default():
    """未安装任何插件时，注册表里没有插件工具（宿主启动时才注册）。"""
    from app.services.tool_registry import REGISTRY

    assert [n for n in REGISTRY.names if n.startswith("spec.")] == []


def test_registry_supports_runtime_registration():
    """注册表支持运行期注册/注销（插件安装/卸载依赖这一能力）。"""
    from synlys_harness import ToolContext, ToolResult, tool
    from app.services.tool_registry import REGISTRY

    @tool(name="tmp.demo", description="临时工具",
          parameters={"type": "object", "properties": {}})
    async def tmp_demo(ctx: ToolContext, args: dict) -> ToolResult:
        """临时工具。"""
        return ToolResult(ok=True, content="ok")

    REGISTRY.register(tmp_demo)
    try:
        assert "tmp.demo" in REGISTRY.names
        assert REGISTRY.find("tmp.demo") is not None
    finally:
        REGISTRY.unregister("tmp.demo")
    assert "tmp.demo" not in REGISTRY.names
