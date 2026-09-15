"""内置内容（catalog）扫描：专家 / 技能 / 插件。

目录位置即类型（一眼可辨，加一个目录即扩展）：
    <root>/experts/<dir>/expert.json    → 专家
    <root>/skills/<name>/SKILL.md       → 技能（frontmatter 即元数据，无额外 manifest）
    <root>/plugins/<id>/plugin.json     → 插件（沿用既有插件契约）
两个根：随仓库的 `apps/web/backend/catalog/` + `{data_dir}/catalog/`（运行期安装预留）。
非法/缺字段/放错位置的包只告警跳过，不阻断启动；同名后者覆盖前者。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.settings import Settings

logger = logging.getLogger(__name__)

EXPERTS_DIR = "experts"
SKILLS_DIR = "skills"
PLUGINS_DIR = "plugins"

MANIFEST_NAME = "plugin.json"          # 插件 manifest（内容不变）
REQUIRED_FIELDS = ("id", "name", "version", "tools_module")
EXPERT_MANIFEST = "expert.json"
EXPERT_REQUIRED = ("id", "name", "system_prompt")
SKILL_FILE = "SKILL.md"


@dataclass(frozen=True)
class PluginPackage:
    """一个插件包（插件目录 + 解析后的 manifest）。

    Attributes:
        id: 插件 id（manifest 声明，通常与目录名一致；工具配置命名空间）。
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


@dataclass(frozen=True)
class ExpertPackage:
    """内置专家包（catalog/experts/<dir>/expert.json）。

    Attributes:
        id: 专家 id（= 播种出的助手 _id）。
        name: 显示名。
        avatar: 头像 emoji。
        description: 描述。
        system_prompt: 人设提示词。
        tool_whitelist: 该专家可用的工具名列表。
        directory: 包目录绝对路径。
    """

    id: str
    name: str
    avatar: str
    description: str
    system_prompt: str
    tool_whitelist: list[str]
    directory: Path


@dataclass(frozen=True)
class SkillPackage:
    """内置技能包（catalog/skills/<name>/SKILL.md）。

    description 由 CatalogService 从 SKILL.md frontmatter 解析，此处不重复解析。

    Attributes:
        name: 技能名（= 目录名）。
        directory: 包目录绝对路径。
    """

    name: str
    directory: Path


@dataclass(frozen=True)
class CatalogIndex:
    """一次扫描的结果（三类分开，调用方各取所需）。

    Attributes:
        experts: {专家 id: ExpertPackage}。
        skills: {技能名: SkillPackage}。
        plugins: {插件 id: PluginPackage}。
    """

    experts: dict[str, ExpertPackage]
    skills: dict[str, SkillPackage]
    plugins: dict[str, PluginPackage]


def catalog_roots(settings: "Settings") -> list[Path]:
    """内置内容搜索根。

    Args:
        settings: 应用配置（取数据目录）。

    Returns:
        根目录列表：随仓库的 `apps/web/backend/catalog/` + 数据目录下
        `{data_dir}/catalog/`（后者为运行期安装预留）。
    """
    return [
        Path(__file__).resolve().parents[2] / "catalog",
        settings.data_root / "catalog",
    ]


def scan_catalog(roots: list[Path]) -> CatalogIndex:
    """扫描全部内置内容根，解析三类包。

    同名 id 出现在多个根时，遍历顺序靠后的根覆盖靠前的
    （`catalog_roots()` 先返回仓库根、后返回数据目录根，故数据目录版优先）。

    Args:
        roots: 根目录列表（不存在的根直接跳过）。

    Returns:
        CatalogIndex；三类条目各自的非法包只告警跳过，不抛异常。
    """
    index = CatalogIndex(experts={}, skills={}, plugins={})
    for root in roots:
        if not root.is_dir():
            continue
        index.experts.update(_scan_experts(root))
        index.skills.update(_scan_skills(root))
        index.plugins.update(_scan_plugins(root))
    return index


def _scan_experts(root: Path) -> dict[str, ExpertPackage]:
    """扫描 `<root>/experts` 下的专家包。

    Args:
        root: catalog 根目录。

    Returns:
        {专家 id: ExpertPackage}；缺必填字段的包只告警跳过。
    """
    out: dict[str, ExpertPackage] = {}
    experts_dir = root / EXPERTS_DIR
    if not experts_dir.is_dir():
        return out
    for entry in sorted(experts_dir.iterdir()):
        manifest_path = entry / EXPERT_MANIFEST
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("专家 manifest 解析失败，已跳过 %s: %s", entry.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("专家 manifest 非对象，已跳过 %s", entry.name)
            continue
        expert_id = data.get("id")
        if not isinstance(expert_id, str) or not expert_id.strip():
            logger.warning("专家 manifest id 非法，已跳过 %s", entry.name)
            continue
        missing = [k for k in EXPERT_REQUIRED if not data.get(k)]
        if missing:
            logger.warning("专家 manifest 缺字段 %s，已跳过 %s", missing, entry.name)
            continue
        if expert_id in out:
            logger.warning("专家 id 重复，后者覆盖前者: %s（%s）", expert_id, entry)
        out[expert_id] = ExpertPackage(
            id=expert_id,
            name=str(data["name"]),
            avatar=str(data.get("avatar") or ""),
            description=str(data.get("description") or ""),
            system_prompt=str(data["system_prompt"]),
            tool_whitelist=[str(t) for t in (data.get("tool_whitelist") or [])],
            directory=entry,
        )
    return out


def _scan_skills(root: Path) -> dict[str, SkillPackage]:
    """扫描 `<root>/skills` 下的技能包（有 SKILL.md 即收录）。

    Args:
        root: catalog 根目录。

    Returns:
        {技能名: SkillPackage}（技能名 = 目录名）。
    """
    out: dict[str, SkillPackage] = {}
    skills_dir = root / SKILLS_DIR
    if not skills_dir.is_dir():
        return out
    for entry in sorted(skills_dir.iterdir()):
        if not (entry / SKILL_FILE).is_file():
            continue  # 放错位置/不完整的目录：静默跳过（技能元数据在 frontmatter 里，此处不解析）
        if entry.name in out:
            logger.warning("技能名重复，后者覆盖前者: %s（%s）", entry.name, entry)
        out[entry.name] = SkillPackage(name=entry.name, directory=entry)
    return out


def _scan_plugins(root: Path) -> dict[str, PluginPackage]:
    """扫描 `<root>/plugins` 下的插件包，解析 manifest。

    Args:
        root: catalog 根目录。

    Returns:
        {插件 id: PluginPackage}；非法 manifest 只告警跳过。
    """
    packages: dict[str, PluginPackage] = {}
    plugins_dir = root / PLUGINS_DIR
    if not plugins_dir.is_dir():
        return packages
    for entry in sorted(plugins_dir.iterdir()):
        manifest_path = entry / MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("插件 manifest 解析失败，已跳过 %s: %s", entry.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("插件 manifest 非对象，已跳过 %s", entry.name)
            continue
        plugin_id = data.get("id")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            logger.warning("插件 manifest id 非法，已跳过 %s", entry.name)
            continue
        missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
        if missing:
            logger.warning("插件 manifest 缺字段 %s，已跳过 %s", missing, entry.name)
            continue
        schema = data.get("config_schema") or []
        if not isinstance(schema, list) or any(
                not isinstance(f, dict) or not str(f.get("key") or "").strip()
                for f in schema):
            logger.warning("插件 manifest 的 config_schema 非法（缺 key），已跳过 %s", entry.name)
            continue
        if plugin_id in packages:
            logger.warning("插件 id 重复，后者覆盖前者: %s（%s）", plugin_id, entry)
        packages[plugin_id] = PluginPackage(
            id=plugin_id,
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
    module_name = f"synlora_plugin_{re.sub(r'[^0-9a-zA-Z_]', '_', package.id)}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("插件 %s 工具模块无法加载: %s", package.id, module_path)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)  # 清掉半初始化模块，避免污染后续导入
        logger.warning("插件 %s 工具模块导入失败: %s", package.id, exc)
        return []
    return [obj for obj in vars(module).values()
            if hasattr(obj, "__tool_definition__")]
