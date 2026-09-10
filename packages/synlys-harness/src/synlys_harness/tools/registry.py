"""工具注册表与 @tool 装饰器。"""
from __future__ import annotations

import inspect

from ..types import Permission, ToolDefinition, ToolExecuteFn


def tool(
    name: str,
    description: str,
    parameters: dict,
    timeout_s: float = 60.0,
    permission: Permission | None = None,
    concurrency_safe: bool = True,
):
    """把异步函数包装为 ToolDefinition 的装饰器。

    Args:
        name: 工具唯一名（如 python.run）。
        description: 给 LLM 看的能力描述。
        parameters: JSON Schema（function calling 的 parameters 字段）。
        timeout_s: 执行超时（由管线实施）。
        permission: 权限级别，默认 ALLOW。
        concurrency_safe: 是否允许与其他工具并行执行。

    Returns:
        装饰器：函数原样返回，但附加 __tool_definition__ 属性。
    """
    perm = permission or Permission.ALLOW

    def decorator(fn: ToolExecuteFn) -> ToolExecuteFn:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"工具 {name} 的 execute 必须是 async 函数")
        fn.__tool_definition__ = ToolDefinition(
            name=name, description=description, parameters=parameters,
            execute=fn, timeout_s=timeout_s, permission=perm,
            concurrency_safe=concurrency_safe,
        )
        return fn

    return decorator


class ToolRegistry:
    """工具注册表：注册、查询与 LLM schema 生成。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, fn_or_def) -> None:
        """注册工具（@tool 装饰过的函数或 ToolDefinition）。

        Args:
            fn_or_def: 带 __tool_definition__ 的函数或 ToolDefinition 实例。

        Raises:
            ValueError: 名字重复。
        """
        definition = getattr(fn_or_def, "__tool_definition__", fn_or_def)
        if not isinstance(definition, ToolDefinition):
            raise TypeError("register 需要 @tool 装饰的函数或 ToolDefinition")
        if definition.name in self._tools:
            raise ValueError(f"工具已注册: {definition.name}")
        self._tools[definition.name] = definition

    def unregister(self, name: str) -> None:
        """按名注销工具。

        Raises:
            KeyError: 工具不存在。
        """
        del self._tools[name]

    def get(self, name: str) -> ToolDefinition:
        """按名获取工具定义。

        Raises:
            KeyError: 工具不存在。
        """
        return self._tools[name]

    def find(self, name: str) -> ToolDefinition | None:
        """按名查找工具定义。

        Args:
            name: 工具名。

        Returns:
            工具定义；不存在时返回 None（不抛异常）。
        """
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        """已注册工具名列表（排序返回）。"""
        return sorted(self._tools)

    def llm_schemas(self, allowed: list[str] | None = None) -> list[dict]:
        """生成 function calling 的 tools 数组。

        Args:
            allowed: 工具白名单（None 表示全部注册工具）。

        Returns:
            OpenAI tools 格式的 schema 列表。
        """
        names = allowed if allowed is not None else list(self._tools)
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name, "description": d.description, "parameters": d.parameters,
                },
            }
            for n in names if (d := self._tools.get(n)) is not None
        ]
