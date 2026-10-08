"""插件类工具的工作区文件读写：统一虚拟路径校验与沙盒 backend 读写。

路径口径（与 kbs 工具和 ocr_parse_file 的生产实践一致，双口径并用）：
- ProvisionerSandboxBackend 构造与 scope 标识用持久化相对口径（/projects/...）；
- 文件操作（download/upload_authorized_file_*）只认 runtime 绝对口径
  （/home/gem/user-data/projects/...，授权根校验）。
- 工具入参接受三种形态：工作区相对路径（uploads/a.txt）、runtime 绝对路径
  （模型从 read_file/ls 看到的形态）、持久化相对口径（/projects/...）；
  均拒绝 .. 越界与 workdir 之外的路径。
"""

import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import PurePosixPath

from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.backends.paths import runtime_workdir_path
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


def _resolve_runtime_scope(runtime: ToolRuntime) -> tuple[str, str, str, str]:
    """读取 execution runtime、用户、持久化 Workdir 与 runtime 绝对 Workdir 路径。

    Returns:
        (runtime_scope_id, uid, workdir_relative_path, runtime_workdir_path) 四元组。

    Raises:
        ValueError: 任一 scope 值缺失时抛出。
    """
    runtime_thread_id = _runtime_scope_value(runtime, "runtime_scope_id") or _runtime_scope_value(
        runtime, "thread_id"
    )
    uid = _runtime_scope_value(runtime, "uid")
    workdir_rel = (_runtime_scope_value(runtime, "workdir_relative_path") or "").strip().strip("/")
    if not runtime_thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    if not uid:
        raise ValueError("当前运行时缺少 uid")
    if not workdir_rel:
        raise ValueError("当前运行时缺少 workdir_relative_path")
    return runtime_thread_id, uid, workdir_rel, runtime_workdir_path(workdir_rel)


def resolve_virtual_path(relative_path: str, runtime: ToolRuntime) -> str:
    """把工具入参路径解析为当前 Run 工作区内的 runtime 绝对虚拟路径。

    Args:
        relative_path: 工作区相对路径（如 uploads/a.txt）、runtime 绝对路径
            （/home/gem/user-data/projects/.../uploads/a.txt，模型文件系统视图的
            形态）或持久化相对口径（/projects/.../uploads/a.txt）；三种写法都
            不允许包含 .. 段。
        runtime: LangGraph 工具运行时。

    Returns:
        归一化后的 runtime 绝对虚拟路径（/home/gem/user-data/projects/... 开头，
        沙盒 backend 文件操作的合法口径）。

    Raises:
        ValueError: 路径为空、包含 .. 段或落在当前 workdir 之外时抛出。
    """
    _, _, workdir_rel, workdir_abs = _resolve_runtime_scope(runtime)
    normalized = str(relative_path or "").strip()
    if not normalized:
        raise ValueError("文件路径不能为空")
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError("文件路径不允许包含 ..")
    # 兼容三种前缀形态：runtime 绝对、持久化相对（projects/...）、带斜杠的持久化相对
    for prefix in (workdir_abs, workdir_rel, f"/{workdir_rel}"):
        if normalized.startswith(f"{prefix}/"):
            return f"{workdir_abs}/{normalized[len(prefix) + 1:]}"
    if normalized.startswith("/"):
        # 其他绝对路径（其他 project、猜错前缀的 /home/gem/...）→ 拒绝
        raise ValueError(
            "文件路径必须位于当前工作区内：请传工作区相对路径（如 uploads/a.txt）"
            "或以当前 workdir 开头的完整路径"
        )
    return f"{workdir_abs}/{normalized.lstrip('/')}"


def _make_backend(runtime_scope_id: str, uid: str, workdir_rel: str) -> ProvisionerSandboxBackend:
    """按当前 runtime scope 构造沙盒 backend（构造参数用持久化相对口径）。"""
    return ProvisionerSandboxBackend(
        thread_id=runtime_scope_id,
        uid=uid,
        workdir_path=workdir_rel,
        create_if_missing=True,
    )


async def download_workspace_file(
    relative_path: str, runtime: ToolRuntime, *, max_bytes: int
) -> tuple[bytes, str]:
    """从当前 Run 工作区读取文件字节。

    Args:
        relative_path: 工作区相对路径或 workdir 内的 runtime 绝对路径。
        runtime: LangGraph 工具运行时。
        max_bytes: 文件大小上限（字节）。

    Returns:
        (文件字节, 文件名) 二元组。

    Raises:
        ValueError: 路径非法、超出大小上限或文件不存在时抛出。
    """
    runtime_scope_id, uid, workdir_rel, _ = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime_scope_id, uid, workdir_rel)
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
    runtime_scope_id, uid, workdir_rel, workdir_abs = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime_scope_id, uid, workdir_rel)
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
    return virtual_path[len(workdir_abs) + 1:]
