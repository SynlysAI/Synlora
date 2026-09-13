"""工具四段执行管线：pre-execute → execute → post-execute → 返回。"""
from __future__ import annotations

import asyncio

from ..types import Permission, ToolContext, ToolResult
from .registry import ToolRegistry


class ToolPipeline:
    """统一工具执行入口：校验、超时、异常捕获与输出截断。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """初始化管线。

        Args:
            registry: 工具注册表。
        """
        self._registry = registry

    async def run(
        self,
        name: str,
        ctx: ToolContext,
        args: dict,
        allowed: list[str] | None = None,
        max_output_chars: int = 65_536,
    ) -> ToolResult:
        """执行一次工具调用（四段管线）。

        Args:
            name: 工具名。
            ctx: 执行上下文。
            args: LLM 给出的参数。
            allowed: 本次 run 的工具白名单（None 表示注册表全部）。
            max_output_chars: post-execute 截断阈值。

        Returns:
            ToolResult（任何失败都体现为 ok=False，不抛异常）。
            持有 OS 资源（子进程/文件句柄）的工具必须在 execute 内自行处理取消与清理，
            管线超时只取消等待。
        """
        # --- pre-execute ---
        def err(code: str, message: str = "") -> ToolResult:
            return ToolResult(ok=False, content=message or code, error=code)

        definition = self._registry.find(name)
        if definition is None:
            return err("unknown_tool", f"工具不存在: {name}")
        if allowed is not None and name not in allowed:
            return err("denied", f"工具不在白名单: {name}")
        if definition.permission is Permission.DENY:
            return err("denied", f"工具 {name} 已被禁用")
        if definition.permission is Permission.ASK_USER:
            # 强制审批（硬约束）：管线在执行前打断，宿主经 ctx.extra 注入
            # approval_handler（发审批事件并等用户答复）；fail-closed——
            # 无回路或用户未明确允许一律拒绝。与 ask_user 工具（模型主动
            # 问询的软约束）共用宿主的同一条事件/回答回路。
            handler = ctx.extra.get("approval_handler")
            if not callable(handler):
                return err("denied", f"工具 {name} 需要用户审批，但当前环境不支持审批回路")
            reply = await handler({
                "tool": name, "args": args,
                "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
            })
            if str(reply).strip() != "允许":
                return err("denied", f"用户拒绝执行 {name}")
        if not isinstance(args, dict):
            return err("invalid_arguments", "参数必须是 JSON 对象")
        for key in definition.parameters.get("required", []):
            if key not in args:
                return err("invalid_arguments", f"缺少必填参数: {key}")

        # --- execute（around：超时 + 异常捕获）---
        try:
            async with asyncio.timeout(definition.timeout_s):
                result = await definition.execute(ctx, args)
        except TimeoutError:
            return err("timeout", f"工具执行超时（>{definition.timeout_s}s）")
        except Exception as exc:  # noqa: BLE001 工具错误必须对 LLM 可见
            return ToolResult(ok=False, content=f"工具执行出错: {exc}", error="tool_exception")

        if not isinstance(result, ToolResult):
            return err("invalid_result", f"工具 {name} 返回了非法结果类型: {type(result).__name__}")

        # --- post-execute（截断）---
        if len(result.content) > max_output_chars:
            result.content = result.content[:max_output_chars]
            result.truncated = True
        return result
