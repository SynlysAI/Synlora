"""用户工作区布局与配额。"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".msi", ".ps1", ".sh", ".com", ".scr"}
MAX_FILE_BYTES = 50 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_一-鿿-]+")
DEFAULT_PROJECT_DIR = "default"
PROJECT_SUBDIRS = ("files", "output", "tmp")


def workspace_root(data_root: Path, user_id: str) -> Path:
    """用户工作区根（自动创建 files/output/tmp）。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。

    Returns:
        {data_root}/workspaces/{user_id} 路径。
    """
    root = data_root / "workspaces" / user_id
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def usage_bytes(root: Path) -> int:
    """工作区已用字节数（全目录树）。

    Args:
        root: 工作区根路径。

    Returns:
        目录树下所有文件的大小合计。
    """
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def unique_target(directory: Path, filename: str) -> Path:
    """目标路径（重名自动加序号 name_1.ext）。

    Args:
        directory: 目标目录。
        filename: 期望文件名。

    Returns:
        不与现有文件冲突的目标路径。
    """
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    i = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{i}{suffix}"
        i += 1
    return candidate


def validate_upload(filename: str, size: int) -> None:
    """上传校验（扩展名黑名单与大小上限）。

    Args:
        filename: 文件名（扩展名黑名单判断依据）。
        size: 文件字节数。

    Raises:
        ValueError: 校验失败（API 层转 422/413）。
    """
    ext = Path(filename).suffix.lower()
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"不允许的文件类型: {ext}")
    if size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过上限 {MAX_FILE_BYTES // (1024 * 1024)}MB")


def check_quota(root: Path, incoming: int, quota: int) -> None:
    """配额检查。

    Args:
        root: 工作区根路径。
        incoming: 本次将要写入的字节数。
        quota: 用户配额上限。

    Raises:
        ValueError: 超配额（API 层转 413）。
    """
    if usage_bytes(root) + incoming > quota:
        raise ValueError("超出用户工作区配额")


def sanitize_dir_name(name: str) -> str:
    """把项目名安全化为目录名（去掉路径分隔符与特殊字符）。

    Args:
        name: 用户输入的项目名。

    Returns:
        只含字母/数字/下划线/连字符/中文的目录名；全被过滤时返回空串
        （调用方须对空串做回退或拒绝）。
    """
    return _SAFE_NAME.sub("_", name.strip()).strip("_")


def project_root(data_root: Path, user_id: str, dir_name: str) -> Path:
    """项目根目录（自动创建 files/output/tmp）。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。
        dir_name: 项目目录名（须非空且不含路径分隔符）。

    Returns:
        {data_root}/workspaces/{user_id}/{dir_name} 路径。

    Raises:
        ValueError: dir_name 为空、为 . / .. 或含路径分隔符（防止越界写出用户目录）。
    """
    if not dir_name or dir_name in {".", ".."} or "/" in dir_name or "\\" in dir_name:
        raise ValueError(f"非法项目目录名: {dir_name!r}")
    root = data_root / "workspaces" / user_id / dir_name
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def migrate_legacy_layout(data_root: Path, user_id: str) -> bool:
    """把旧版 {uid}/files|output|tmp 逐个子目录迁进 {uid}/default/（幂等）。

    逐个子目录独立判断而非整体早退：只有部分子目录存在、或上次迁移中断时，
    仍能把剩余目录搬过去，不会永久遗弃旧数据。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。

    Returns:
        True 表示本次至少搬动了一个子目录，False 表示无可迁移项。

    Raises:
        OSError: 底层文件操作失败（可能已部分迁移，下次调用会续迁剩余部分）。
    """
    user_dir = data_root / "workspaces" / user_id
    target = user_dir / DEFAULT_PROJECT_DIR
    moved = False
    for sub in PROJECT_SUBDIRS:
        src = user_dir / sub
        dst = target / sub
        if not src.is_dir() or dst.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        moved = True
    return moved


def resolve_in_project(root: Path, rel: str) -> Path:
    """把相对路径解析到项目根内（越界抛 ValueError）。

    Args:
        root: 项目根目录。
        rel: 用户给的相对路径。

    Returns:
        绝对路径，保证位于 root 之内。

    Raises:
        ValueError: 路径逃逸出项目根。
    """
    candidate = (root / rel).resolve()
    if candidate != root.resolve() and root.resolve() not in candidate.parents:
        raise ValueError("路径越界")
    return candidate


def free_dir_name(user_dir: Path, base: str, taken: set[str]) -> str:
    """求一个未被占用的目录名（盘上存在或仍被活跃项目引用都算占用）。

    Args:
        user_dir: 该用户的工作区目录（{data_root}/workspaces/{user_id}）。
        base: sanitize 后的基础目录名（须非空）。
        taken: 仍被活跃项目引用的目录名集合。

    Returns:
        base 本身，或 base-2 / base-3 …

    Raises:
        ValueError: base 为空（调用方须先回退占位名或拒绝该请求）。
    """
    if not base:
        raise ValueError("base 不能为空（调用方须先回退占位名或拒绝该请求）")
    name = base
    i = 1
    while name in taken or (user_dir / name).exists():
        i += 1
        name = f"{base}-{i}"
    return name


def remove_project_dir(target: Path) -> bool:
    """删除项目目录；失败则改名为 {name}.trash-{时间戳}-{uuid 后缀} 释放目录名并保住数据。

    Args:
        target: 项目根目录。

    Returns:
        True 表示已彻底删除，False 表示退化为 trash 改名。

    Raises:
        OSError: 删除与兜底改名均失败（此时目录既未删除、名字也未释放）。
    """
    if not target.exists():
        return True
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        pass
    # 时间戳 + uuid 后缀：Windows 上 time.time_ns() 实际粒度约 15ms，
    # 仅靠时间戳仍可能撞名；uuid 后缀保证唯一（时间戳保留可读性）
    trash = target.with_name(f"{target.name}.trash-{int(time.time())}-{uuid4().hex[:8]}")
    try:
        target.rename(trash)
    except OSError as exc:
        raise OSError(f"删除项目目录失败且无法改名为 trash：{target}") from exc
    return False
