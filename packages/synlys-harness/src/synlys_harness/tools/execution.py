"""平台无关的进程执行请求与只读资源校验。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_PROTECTED_TARGETS = {
    PurePosixPath("/"),
    PurePosixPath("/workspace"),
    PurePosixPath("/proc"),
    PurePosixPath("/sys"),
    PurePosixPath("/dev"),
    PurePosixPath("/etc"),
    PurePosixPath("/usr"),
    PurePosixPath("/bin"),
}
_EXECUTION_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ReadOnlyResource:
    """一个宿主目录到容器目录的只读绑定。"""

    source: Path
    target: PurePosixPath


@dataclass(frozen=True)
class ExecutionRequest:
    """一次无 shell 隐式解析的进程执行请求。"""

    argv: tuple[str, ...]
    workspace_root: Path
    cwd: str = "."
    resources: tuple[ReadOnlyResource, ...] = ()
    timeout_s: float = 30.0
    max_output_bytes: int = 65_536
    execution_id: str | None = None


def _contains(parent: Path, child: Path) -> bool:
    """判断 child 是否位于 parent 内或与其相同。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _target_contains(parent: PurePosixPath, child: PurePosixPath) -> bool:
    """判断容器路径 child 是否位于 parent 内或与其相同。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_execution_request(request: ExecutionRequest) -> ExecutionRequest:
    """校验并规范化执行请求。

    Args:
        request: 由可信工具适配或宿主构造的执行请求。

    Returns:
        路径已解析为绝对路径的不可变请求。

    Raises:
        ValueError: 参数、工作区、cwd 或资源挂载不合法。
    """
    if not request.argv or any(not isinstance(item, str) or not item for item in request.argv):
        raise ValueError("argv 必须包含非空字符串")
    if not math.isfinite(request.timeout_s) or request.timeout_s <= 0:
        raise ValueError("timeout_s 必须是有限正数")
    if request.max_output_bytes <= 0:
        raise ValueError("max_output_bytes 必须大于零")
    if request.execution_id is not None and not _EXECUTION_ID.fullmatch(request.execution_id):
        raise ValueError("execution_id 格式非法")

    workspace = request.workspace_root.resolve()
    if not workspace.is_absolute():
        raise ValueError("workspace_root 必须是绝对路径")
    workspace.mkdir(parents=True, exist_ok=True)
    cwd_text = str(request.cwd or ".").replace("\\", "/")
    cwd_path = PurePosixPath(cwd_text)
    if cwd_path.is_absolute() or ".." in cwd_path.parts:
        raise ValueError("cwd 必须是工作区内的相对路径")
    resolved_cwd = (workspace / Path(*cwd_path.parts)).resolve()
    if not _contains(workspace, resolved_cwd):
        raise ValueError("cwd 逃逸工作区")

    normalized: list[ReadOnlyResource] = []
    for resource in request.resources:
        source = resource.source.resolve()
        if not resource.source.is_absolute() or not source.is_dir():
            raise ValueError("资源 source 必须是存在的绝对目录")
        target = resource.target
        if not target.is_absolute() or ".." in target.parts or str(target) != target.as_posix():
            raise ValueError("资源 target 必须是规范化容器绝对路径")
        protected = target == PurePosixPath("/") or any(
            item != PurePosixPath("/") and _target_contains(item, target)
            for item in _PROTECTED_TARGETS
        )
        if protected:
            raise ValueError(f"资源 target 覆盖执行基础目录: {target}")
        if _contains(workspace, source) or _contains(source, workspace):
            raise ValueError("资源目录不得与工作区相同或互相包含")
        for previous in normalized:
            if (_target_contains(previous.target, target)
                    or _target_contains(target, previous.target)):
                raise ValueError("资源 target 不能相同或互为祖先")
        normalized.append(ReadOnlyResource(source=source, target=target))

    return ExecutionRequest(
        argv=tuple(request.argv),
        workspace_root=workspace,
        cwd="." if not cwd_path.parts else cwd_path.as_posix(),
        resources=tuple(normalized),
        timeout_s=float(request.timeout_s),
        max_output_bytes=int(request.max_output_bytes),
        execution_id=request.execution_id,
    )
