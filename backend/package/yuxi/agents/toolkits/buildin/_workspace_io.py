"""插件类工具的工作区文件读写：统一虚拟路径校验与沙盒 backend 读写。

路径口径与 toolkits/buildin/tools.py 的 ocr_parse_file 保持一致：
输入接受工作区相对路径或以当前 workdir 开头的绝对虚拟路径，拒绝 .. 越界。
"""

import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import PurePosixPath

from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend


def _runtime_scope_value(runtime: ToolRuntime, key: str) -> str | None:
    """从 LangGraph config/context/state 中查找 runtime scope 值。"""
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    sources = (
        configurable if isinstance(configurable, dict) else {},
        getattr(runtime, "context", None),
        getattr(runtime, "state", None) if isinstance(getattr(runtime, "state", None), dict) else {},
    )
    for source in sources:
        value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_runtime_scope(runtime: ToolRuntime) -> tuple[str, str, str]:
    """读取 execution runtime、用户与 Workdir 路径。"""
    runtime_thread_id = _runtime_scope_value(runtime, "runtime_scope_id") or _runtime_scope_value(
        runtime, "thread_id"
    )
    uid = _runtime_scope_value(runtime, "uid")
    workdir_path = _runtime_scope_value(runtime, "workdir_relative_path")
    if not runtime_thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    if not uid:
        raise ValueError("当前运行时缺少 uid")
    if not workdir_path:
        raise ValueError("当前运行时缺少 workdir_relative_path")
    return runtime_thread_id, uid, workdir_path.rstrip("/")


def resolve_virtual_path(relative_path: str, runtime: ToolRuntime) -> str:
    """把工具入参路径解析为当前 Run 工作区内的虚拟绝对路径。

    Args:
        relative_path: 工作区相对路径（如 uploads/a.txt），或以当前 workdir
            开头的绝对虚拟路径；两种写法都不允许包含 .. 段。
        runtime: LangGraph 工具运行时。

    Returns:
        归一化后的虚拟绝对路径（以 workdir 开头）。

    Raises:
        ValueError: 路径为空、包含 .. 段或落在当前 workdir 之外时抛出。
    """
    _, _, workdir_path = _resolve_runtime_scope(runtime)
    normalized = str(relative_path or "").strip()
    if not normalized:
        raise ValueError("文件路径不能为空")
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError("文件路径不允许包含 ..")
    if normalized.startswith(f"{workdir_path}/"):
        return normalized
    if normalized.startswith("/"):
        # 绝对虚拟路径但不属于当前 workdir（如 /home/gem/other/...）→ 拒绝
        raise ValueError(
            "文件路径必须位于当前工作区内：请传工作区相对路径（如 uploads/a.txt）"
            "或以当前 workdir 开头的完整路径"
        )
    return f"{workdir_path}/{normalized.lstrip('/')}"


def _make_backend(runtime_scope_id: str, uid: str, workdir_path: str) -> ProvisionerSandboxBackend:
    """按当前 runtime scope 构造沙盒 backend。"""
    return ProvisionerSandboxBackend(
        thread_id=runtime_scope_id,
        uid=uid,
        workdir_path=workdir_path,
        create_if_missing=True,
    )


async def download_workspace_file(
    relative_path: str, runtime: ToolRuntime, *, max_bytes: int
) -> tuple[bytes, str]:
    """从当前 Run 工作区读取文件字节。

    Args:
        relative_path: 工作区相对路径或 workdir 内的绝对虚拟路径。
        runtime: LangGraph 工具运行时。
        max_bytes: 文件大小上限（字节）。

    Returns:
        (文件字节, 文件名) 二元组。

    Raises:
        ValueError: 路径非法、超出大小上限或文件不存在时抛出。
    """
    runtime_scope_id, uid, workdir_path = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime_scope_id, uid, workdir_path)
    suffix = PurePosixPath(virtual_path).suffix
    with tempfile.NamedTemporaryFile(
        prefix="yuxi-plugin-source-", suffix=suffix, delete=False
    ) as temp_file:
        temp_path = temp_file.name
    try:
        await asyncio.to_thread(
            backend.download_authorized_file_to_path, virtual_path, temp_path, max_bytes
        )
        with open(temp_path, "rb") as fh:
            return fh.read(), PurePosixPath(virtual_path).name
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)


async def upload_workspace_file(content: bytes, relative_path: str, runtime: ToolRuntime) -> str:
    """把字节写入当前 Run 工作区（父/子智能体共享同一 Workdir）。

    Args:
        content: 文件字节。
        relative_path: 目标工作区相对路径（如 outputs/result.json）。
        runtime: LangGraph 工具运行时。

    Returns:
        写入后的工作区相对显示路径（如 outputs/result.json）。

    Raises:
        ValueError: 路径非法时抛出。
    """
    runtime_scope_id, uid, workdir_path = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime_scope_id, uid, workdir_path)
    with tempfile.NamedTemporaryFile(
        prefix="yuxi-plugin-output-", suffix=PurePosixPath(virtual_path).suffix, delete=False
    ) as temp_file:
        temp_file.write(content)
        temp_path = temp_file.name
    try:
        await asyncio.to_thread(backend.upload_authorized_file_from_path, virtual_path, temp_path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
    return virtual_path[len(workdir_path) + 1:]
