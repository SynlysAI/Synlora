"""公共 MCP 的管理写入层（数据目录覆盖，仓库 catalog 不动）。

覆盖语义与 catalog loader 一致：`{data_dir}/public/catalog/mcp/<id>/mcp.json`
存在即遮蔽仓库内置版（`scan_catalog` 后扫的根优先）；删除覆盖 = 恢复默认。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.catalog.loader import MCP_MANIFEST, parse_mcp_manifest

if TYPE_CHECKING:
    from app.core.settings import Settings


def public_root(settings: "Settings") -> Path:
    """公共 MCP 可写层根目录（{data_root}/public/catalog/mcp）。"""
    return settings.data_root / "public" / "catalog" / "mcp"


def is_overlaid(settings: "Settings", mcp_id: str) -> bool:
    """某 id 是否存在数据目录覆盖文件。

    Args:
        settings: 应用配置。
        mcp_id: MCP id。

    Returns:
        True 表示存在覆盖副本。
    """
    return (public_root(settings) / mcp_id / MCP_MANIFEST).is_file()


def write_public(settings: "Settings", data: dict[str, Any]) -> Path:
    """校验并写入一份公共 MCP manifest（原子替换）。

    Args:
        settings: 应用配置。
        data: manifest 字典（id 必填且 kebab-case）。

    Returns:
        写入的 manifest 路径。

    Raises:
        ValueError: manifest 非法（parse_mcp_manifest 同口径）。
    """
    directory = public_root(settings) / str(data["id"])
    parse_mcp_manifest(data, directory)  # 仅校验，异常向上抛给端点转 422
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MCP_MANIFEST
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    # 原子写：临时文件 + os.replace，读侧（热重载扫描）不会读到半截 JSON
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def delete_public(settings: "Settings", mcp_id: str) -> bool:
    """删除数据目录覆盖（含包目录；不存在返回 False）。

    Args:
        settings: 应用配置。
        mcp_id: MCP id。

    Returns:
        True 表示已删除。
    """
    directory = public_root(settings) / mcp_id
    if not directory.is_dir():
        return False
    for child in sorted(directory.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    directory.rmdir()
    return True
