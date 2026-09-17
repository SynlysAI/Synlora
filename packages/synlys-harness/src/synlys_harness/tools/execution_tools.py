"""统一进程执行协议的模型工具适配。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..types import ToolContext, ToolResult
from .execution import ExecutionRequest
from .registry import tool
from .sandbox import DEFAULT_LOCAL_EXECUTOR

PYTHON_TIMEOUT_S = 60.0
SHELL_TIMEOUT_S = 60.0
MAX_OUTPUT_BYTES = 65_536


def _resources(ctx: ToolContext) -> tuple[Any, ...]:
    """读取宿主注入的可信执行资源列表。"""
    return tuple(ctx.extra.get("execution_resources") or ())


@tool(
    name="python.run",
    description=(
        "在用户沙箱中执行 Python 代码（隔离模式，可读写沙箱文件，输出受限）。"
        "默认工作目录为工作区根：files/ 是上传与交付文件，output/ 放产物，tmp/ 放临时文件。"
    ),
    parameters={"type": "object", "properties": {
        "code": {"type": "string", "description": "要执行的 Python 源码"},
    }, "required": ["code"]},
    timeout_s=70,
)
async def python_run(ctx: ToolContext, args: dict) -> ToolResult:
    """在用户工作区根执行 Python 源码。"""
    if ctx.workspace_root is None:
        return ToolResult(ok=False, content="当前会话没有工作区", error="no_workspace")
    executor = ctx.extra.get("code_executor") or DEFAULT_LOCAL_EXECUTOR
    resources = _resources(ctx)
    if resources and not callable(getattr(executor, "execute", None)):
        return ToolResult(
            ok=False,
            content="当前执行器不支持技能资源挂载",
            error="executor_capability_missing",
        )
    if resources:
        result = await executor.execute(ExecutionRequest(
            argv=("python", "-I", "-X", "utf8", "-c", args["code"]),
            workspace_root=ctx.workspace_root,
            cwd=".",
            resources=resources,
            timeout_s=PYTHON_TIMEOUT_S,
            max_output_bytes=MAX_OUTPUT_BYTES,
        ))
    else:
        result = await executor.run(
            args["code"], cwd=ctx.workspace_root, timeout_s=PYTHON_TIMEOUT_S,
        )
    result.data["cwd"] = str(ctx.workspace_root)
    return result


@tool(
    name="shell.run",
    description="在临时 Docker 沙箱内执行一次 Bash 命令；每次调用互相独立。",
    parameters={"type": "object", "properties": {
        "command": {"type": "string", "description": "非空 Bash 命令"},
        "cwd": {"type": "string", "default": ".",
                "description": "相对工作区目录，默认工作区根"},
    }, "required": ["command"]},
    timeout_s=70,
    concurrency_safe=False,
)
async def shell_run(ctx: ToolContext, args: dict) -> ToolResult:
    """仅通过 Docker 执行器运行 Bash，绝不降级到宿主 shell。"""
    if ctx.workspace_root is None:
        return ToolResult(ok=False, content="当前会话没有工作区", error="no_workspace")
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(ok=False, content="command 不能为空", error="invalid_arguments")
    executor = ctx.extra.get("code_executor")
    if executor is None or getattr(executor, "sandbox", "") != "docker":
        return ToolResult(
            ok=False,
            content="shell.run 仅在 Docker 沙箱可用",
            error="shell_unavailable",
            data={"sandbox": getattr(executor, "sandbox", "unavailable")},
        )
    return await executor.execute(ExecutionRequest(
        argv=("/bin/bash", "--noprofile", "--norc", "-c", command),
        workspace_root=ctx.workspace_root,
        cwd=str(args.get("cwd") or "."),
        resources=_resources(ctx),
        timeout_s=SHELL_TIMEOUT_S,
        max_output_bytes=MAX_OUTPUT_BYTES,
    ))
