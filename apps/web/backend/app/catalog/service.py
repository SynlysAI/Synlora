"""按用户计算可见能力（运行期过滤与市场列表的唯一入口）。

三层汇总规则：
- hidden → 不可见（管理员后台另行可见，见 market_items(admin=True)）；
- public + 默认启用 → 所有人可见；
- public + 非默认 → 仅已安装者可见。
目录里不存在的条目一律不可见（防止凭 id 绕过）。

工具名映射由外部注入（PluginPackage 不含工具清单，工具是 loader 动态 import 的），
装配时由 PluginService 提供 {插件 id: {工具名}}。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.catalog.items import CatalogService
    from app.catalog.policy import CatalogPolicyRepo
    from app.catalog.user_caps import UserCapabilityRepo


class CapabilityService:
    """可见能力计算。"""

    def __init__(self, catalog: "CatalogService", policy: "CatalogPolicyRepo",
                 installs: "UserCapabilityRepo",
                 tool_names_by_plugin: dict[str, set[str]] | None = None) -> None:
        """保存依赖。

        Args:
            catalog: 内置条目枚举。
            policy: 管理员策略。
            installs: 用户安装记录。
            tool_names_by_plugin: {插件 id: 该插件贡献的工具名}（可见性过滤工具用）。
        """
        self.catalog = catalog
        self.policy = policy
        self.installs = installs
        self.tool_names_by_plugin = tool_names_by_plugin or {}

    async def exists(self, kind: str, item_id: str) -> bool:
        """条目是否在目录里。

        Args:
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示存在。
        """
        return any(i.id == item_id for i in self.catalog.list_items(kind))

    async def is_visible(self, user_id: str, kind: str, item_id: str) -> bool:
        """判断某用户是否可见某条目。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示可见。
        """
        if not await self.exists(kind, item_id):
            return False
        pol = await self.policy.get(kind, item_id)
        if pol["visibility"] == "hidden":
            return False
        if pol["default_enabled"]:
            return True
        return await self.installs.is_installed(user_id, kind, item_id)

    async def visible_ids(self, user_id: str, kind: str) -> set[str]:
        """某用户在某类下可见的全部条目 id。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。

        Returns:
            可见条目 id 集合。
        """
        items = self.catalog.list_items(kind)
        if not items:
            return set()
        # 一次取回该用户的安装记录，避免逐条查询
        installed = set(await self.installs.list_for_user(user_id, kind=kind))
        out: set[str] = set()
        for item in items:
            pol = await self.policy.get(kind, item.id)
            if pol["visibility"] == "hidden":
                continue
            if pol["default_enabled"] or f"{kind}:{item.id}" in installed:
                out.add(item.id)
        return out

    async def visible_skill_names(self, user_id: str) -> set[str]:
        """某用户可见的技能名（内置技能按策略 + 插件技能跟随其插件可见性）。

        Args:
            user_id: 用户 sub。

        Returns:
            技能名集合。
        """
        names = await self.visible_ids(user_id, "skill")
        for plugin_id in await self.visible_ids(user_id, "plugin"):
            pkg = self.catalog.packages.get(plugin_id)
            if pkg is not None:
                names |= set(pkg.skills)
        return names

    async def visible_tool_names(self, user_id: str) -> set[str]:
        """某用户可见的插件工具名（运行期工具过滤用）。

        Args:
            user_id: 用户 sub。

        Returns:
            工具名集合。
        """
        visible = await self.visible_ids(user_id, "plugin")
        out: set[str] = set()
        for plugin_id in visible:
            out |= self.tool_names_by_plugin.get(plugin_id, set())
        return out

    async def can_install(self, user_id: str, kind: str, item_id: str) -> bool:
        """该用户能否安装该条目（存在且未被管理员隐藏）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示可安装。
        """
        if not await self.exists(kind, item_id):
            return False
        pol = await self.policy.get(kind, item_id)
        return pol["visibility"] != "hidden"

    async def market_items(self, user_id: str, kind: str,
                           *, admin: bool = False) -> list[dict]:
        """市场列表（条目 + 策略 + 安装状态）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            admin: 管理员视角（hidden 条目也返回）。

        Returns:
            条目字典列表；普通用户视角下 hidden 条目不出现。
        """
        installed = set(await self.installs.list_for_user(user_id, kind=kind))
        rows: list[dict] = []
        for item in self.catalog.list_items(kind):
            pol = await self.policy.get(kind, item.id)
            hidden = pol["visibility"] == "hidden"
            if hidden and not admin:
                continue
            is_installed = f"{kind}:{item.id}" in installed
            rows.append({
                "kind": item.kind, "id": item.id, "name": item.name,
                "description": item.description, "source": item.source,
                "visibility": pol["visibility"],
                "default_enabled": pol["default_enabled"],
                "installed": is_installed,
                "visible": (not hidden) and (pol["default_enabled"] or is_installed),
            })
        return rows
