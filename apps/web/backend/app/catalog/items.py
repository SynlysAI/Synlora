"""内置目录条目枚举。

三类内置条目**一律来自** `apps/web/backend/catalog/`（按类型分目录，只读，
首期不支持用户自建），由 `app.catalog.loader.scan_catalog()` 一次扫描得出：

- 专家：`catalog/experts/<dir>/expert.json`（插件播种的 `asst-plugin-*` 专家
  由插件运行时生成，不落在 catalog 里，天然不计入）；
- 技能：`catalog/skills/<name>/SKILL.md`（frontmatter 即元数据，无额外 manifest）；
- 插件：`catalog/plugins/<id>/plugin.json`（沿用既有插件契约）。

另有数据目录根 `{data_dir}/catalog/`（运行期安装预留），同名后者覆盖前者。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from app.services.skill_service import parse_skill_md

if TYPE_CHECKING:
    from app.catalog.loader import CatalogIndex, PluginPackage

logger = logging.getLogger(__name__)

KINDS = ("expert", "skill", "plugin")


@dataclass(frozen=True)
class CatalogItem:
    """一个内置目录条目。

    Attributes:
        kind: expert | skill | plugin。
        id: 条目 id（专家=助手 _id；技能=技能名；插件=插件 id）。
        name: 显示名。
        description: 描述。
        source: 来源（首期恒为 builtin）。
    """

    kind: str
    id: str
    name: str
    description: str
    source: str = "builtin"


class CatalogService:
    """内置条目的只读枚举（三类来源均为一次 catalog 扫描的结果）。"""

    def __init__(self, index: "CatalogIndex") -> None:
        """保存依赖。

        Args:
            index: 一次 catalog 扫描的结果（三类包）。
        """
        self._index = index

    @property
    def plugins(self) -> dict[str, "PluginPackage"]:
        """扫描到的插件包（{id: PluginPackage}）。

        Returns:
            插件包映射（只读用途；调用方不得就地修改）。
        """
        return self._index.plugins

    def list_items(self, kind: str) -> list[CatalogItem]:
        """枚举某类内置条目。

        Args:
            kind: expert | skill | plugin；未知 kind 返回空列表。

        Returns:
            条目列表（按 id 排序）。
        """
        if kind == "expert":
            return self._experts()
        if kind == "skill":
            return self._skills()
        if kind == "plugin":
            return self._plugins()
        return []

    def all_items(self) -> list[CatalogItem]:
        """枚举全部内置条目（三类合并）。

        Returns:
            条目列表（按 kind 顺序 expert/skill/plugin，各类内按 id 排序）。
        """
        return [item for kind in KINDS for item in self.list_items(kind)]

    def _experts(self) -> list[CatalogItem]:
        """内置专家条目（catalog 专家包）。

        Returns:
            条目列表（按 id 排序）。
        """
        return sorted(
            (
                CatalogItem(
                    kind="expert", id=pkg.id,
                    name=pkg.name or pkg.id,
                    description=pkg.description,
                )
                for pkg in self._index.experts.values()
            ),
            key=lambda i: i.id,
        )

    def _skills(self) -> list[CatalogItem]:
        """内置技能条目（catalog/skills 包，描述取 SKILL.md frontmatter）。

        Returns:
            条目列表（按 id 排序）。
        """
        out: list[CatalogItem] = []
        for pkg in self._index.skills.values():
            md = pkg.directory / "SKILL.md"
            try:
                meta = parse_skill_md(md.read_text(encoding="utf-8"))
                desc = str(meta.get("description") or "")
            except (OSError, ValueError, yaml.YAMLError):
                logger.warning("catalog 技能 %s 的 SKILL.md 解析失败，描述留空", pkg.name)
                desc = ""
            out.append(CatalogItem(kind="skill", id=pkg.name, name=pkg.name, description=desc))
        return sorted(out, key=lambda i: i.id)

    def _plugins(self) -> list[CatalogItem]:
        """内置插件条目（扫描到的插件包）。

        Returns:
            条目列表（按 id 排序）。
        """
        return sorted(
            (
                CatalogItem(
                    kind="plugin", id=pkg.id,
                    name=pkg.name, description=pkg.description,
                )
                for pkg in self._index.plugins.values()
            ),
            key=lambda i: i.id,
        )
