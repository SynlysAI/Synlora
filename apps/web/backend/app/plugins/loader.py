"""插件包扫描与工具加载。

插件包 = 一个目录 + `plugin.json`（manifest）。宿主按通用约定扫描根目录、
解析 manifest、按 `tools_module` 动态导入工具函数（harness `@tool` 装饰过）。
manifest 非法只跳过并告警，不阻断服务启动。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.settings import Settings

logger = logging.getLogger(__name__)

MANIFEST_NAME = "plugin.json"
REQUIRED_FIELDS = ("id", "name", "version", "tools_module")


@dataclass(frozen=True)
class PluginPackage:
    """一个插件包（插件目录 + 解析后的 manifest）。

    Attributes:
        id: 插件 id（= 目录名，工具配置命名空间）。
        name: 显示名。
        version: 版本号。
        description: 描述。
        directory: 插件目录绝对路径。
        tools_module: 工具模块文件名（相对插件目录）。
        config_schema: 配置字段 schema（驱动前端表单与校验）。
        skills: 插件自带技能名列表。
        expert: 专家模板（None = 本插件不贡献专家）。
    """

    id: str
    name: str
    version: str
    description: str
    directory: Path
    tools_module: str
    config_schema: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    expert: dict | None = None

    @property
    def skills_root(self) -> Path | None:
        """插件自带技能目录（不存在时 None）。"""
        d = self.directory / "skills"
        return d if d.is_dir() else None


def plugin_roots(settings: "Settings") -> list[Path]:
    """插件包搜索根。

    Args:
        settings: 应用配置（取数据目录）。

    Returns:
        根目录列表：随仓库的 `apps/web/backend/plugins/` + 数据目录下
        `{data_dir}/plugins/`（后者为运行期安装预留）。
    """
    return [
        Path(__file__).resolve().parents[2] / "plugins",
        settings.data_root / "plugins",
    ]


def scan_plugins(roots: list[Path]) -> dict[str, PluginPackage]:
    """扫描插件根目录，解析全部合法插件包。

    Args:
        roots: 插件根目录列表（不存在的根直接跳过）。

    Returns:
        {插件 id: PluginPackage}；非法 manifest 只告警跳过。
    """
    packages: dict[str, PluginPackage] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            manifest_path = entry / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("插件 manifest 解析失败，已跳过 %s: %s", entry.name, exc)
                continue
            missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
            if missing:
                logger.warning("插件 manifest 缺字段 %s，已跳过 %s", missing, entry.name)
                continue
            packages[data["id"]] = PluginPackage(
                id=str(data["id"]),
                name=str(data["name"]),
                version=str(data["version"]),
                description=str(data.get("description") or ""),
                directory=entry,
                tools_module=str(data["tools_module"]),
                config_schema=list(data.get("config_schema") or []),
                skills=[str(s) for s in (data.get("skills") or [])],
                expert=data.get("expert") or None,
            )
    return packages


def load_plugin_tools(package: PluginPackage) -> list[Any]:
    """动态导入插件工具模块并收集 `@tool` 装饰过的函数。

    Args:
        package: 插件包。

    Returns:
        工具函数列表（模块缺失或导入失败时返回空列表并告警）。
    """
    module_path = package.directory / package.tools_module
    if not module_path.is_file():
        logger.warning("插件 %s 的工具模块不存在: %s", package.id, module_path)
        return []
    module_name = f"synlora_plugin_{package.id}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("插件 %s 工具模块无法加载: %s", package.id, module_path)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("插件 %s 工具模块导入失败: %s", package.id, exc)
        return []
    return [obj for obj in vars(module).values()
            if hasattr(obj, "__tool_definition__")]
