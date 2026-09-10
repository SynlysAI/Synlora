"""用户工作区布局与配额。"""
from __future__ import annotations

from pathlib import Path

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".msi", ".ps1", ".sh", ".com", ".scr"}
MAX_FILE_BYTES = 50 * 1024 * 1024


def workspace_root(data_root: Path, user_id: str) -> Path:
    """用户工作区根（自动创建 files/output/tmp）。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。

    Returns:
        {data_root}/workspaces/{user_id} 路径。
    """
    root = data_root / "workspaces" / user_id
    for sub in ("files", "output", "tmp"):
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
