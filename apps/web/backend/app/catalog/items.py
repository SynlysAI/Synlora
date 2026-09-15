"""内置目录条目枚举。

内置项只有三个来源，全部只读（首期不支持用户自建）：
- 专家：代码种子 SEED_ASSISTANTS（插件播种的 asst-plugin-* 跟随其插件，不算独立条目）；
- 技能：随仓库播种到技能目录的内置技能（名字在 skill_service.BUILTIN_SKILL_NAMES 中）；
- 插件：扫描到的插件包（随仓库 catalog/plugins/ + 数据目录 catalog/plugins/）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.db.repos import SEED_ASSISTANTS
from app.services.skill_service import BUILTIN_SKILL_NAMES

if TYPE_CHECKING:
    from app.catalog.loader import CatalogIndex, PluginPackage
    from app.core.settings import Settings
    from app.services.skill_service import SkillService

KINDS = ("expert", "skill", "plugin")
EXPERT_ID_PREFIX = "asst-plugin-"  # 插件播种专家不计入目录


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
    """内置条目的只读枚举。"""

    def __init__(self, settings: "Settings", skill_service: "SkillService",
                 index: "CatalogIndex") -> None:
        """保存依赖。

        Args:
            settings: 应用配置。
            skill_service: 技能服务（枚举已播种的内置技能）。
            index: catalog 扫描结果（本类只用到其中的插件包）。
        """
        self._settings = settings
        self._skill_service = skill_service
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
        """内置专家条目（代码种子，排除插件播种的助手）。

        Returns:
            条目列表。
        """
        return sorted(
            (
                CatalogItem(
                    kind="expert", id=str(seed["_id"]),
                    name=str(seed.get("name") or seed["_id"]),
                    description=str(seed.get("description") or ""),
                )
                for seed in SEED_ASSISTANTS
                if not str(seed["_id"]).startswith(EXPERT_ID_PREFIX)
            ),
            key=lambda i: i.id,
        )

    def _skills(self) -> list[CatalogItem]:
        """内置技能条目（已播种到技能目录的内置技能）。

        Returns:
            条目列表。
        """
        return sorted(
            (
                CatalogItem(
                    kind="skill", id=s["name"],
                    name=s["name"], description=str(s.get("description") or ""),
                )
                for s in self._skill_service.list_skills()
                if s["name"] in BUILTIN_SKILL_NAMES
            ),
            key=lambda i: i.id,
        )

    def _plugins(self) -> list[CatalogItem]:
        """内置插件条目（扫描到的插件包）。

        Returns:
            条目列表。
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
