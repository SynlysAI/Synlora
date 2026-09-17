"""工具注册表收口（单实例，运行装配与助手白名单校验共用）。

agent_service（装配 RunSession 用）与 assistants_api（校验助手工具白名单用）
必须看到同一份工具集：插件注册的工具名若只在一边可见，白名单校验会把它们
判为"未注册"而 422。

内置工具在模块导入期注册；插件工具在 lifespan 装配时（按已安装插件）或
安装 API 调用时注册——注册表支持运行期增删（见 ToolRegistry.register）。
"""
from __future__ import annotations

from synlys_harness import ToolPipeline, ToolRegistry, register_builtin_tools

from app.tools import knowledge_list, knowledge_search


def build_registry() -> ToolRegistry:
    """构建含全部内置工具的注册表（插件工具由装配阶段另行注册）。

    内置工具 = harness 通用机制工具 + 宿主业务工具（WeKnora 知识检索，
    属平台基础设施而非插件能力，故不进 catalog/plugins）。

    Returns:
        新的 ToolRegistry。
    """
    registry = ToolRegistry()
    register_builtin_tools(registry)
    registry.register(knowledge_list)
    registry.register(knowledge_search)
    return registry


REGISTRY = build_registry()
PIPELINE = ToolPipeline(registry=REGISTRY)
