"""按用户计算可见能力（运行期过滤与市场列表的唯一入口）。

三层汇总规则（内置优先）：
- hidden → 不可见（管理员后台另行可见，见 market_items(admin=True)）；
- 内置（policy.default_enabled=True）→ 全员强制可见可用，忽略用户安装记录，
  用户不可安装/停用/卸载（"默认启用"即"内置条目"，用户侧只读）；
- 其余 → 已安装以用户启用态为准，未安装不可见（需去市场安装）。
目录里不存在的条目一律不可见（防止凭 id 绕过）。

工具名映射由外部注入（PluginPackage 不含工具清单，工具是 loader 动态 import 的），
装配时由 PluginService 提供 {插件 id: {工具名}}。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.catalog.items import CatalogService
    from app.catalog.policy import CatalogPolicyRepo
    from app.catalog.user_caps import UserCapabilityRepo


class CapabilityService:
    """可见能力计算。"""

    def __init__(self, catalog: "CatalogService", policy: "CatalogPolicyRepo",
                 installs: "UserCapabilityRepo",
                 tool_names_by_plugin: dict[str, set[str]] | "Callable[[], dict[str, set[str]]]"
                 | None = None) -> None:
        """保存依赖。

        Args:
            catalog: 内置条目枚举。
            policy: 管理员策略。
            installs: 用户安装记录。
            tool_names_by_plugin: {插件 id: 该插件贡献的工具名}；传 dict 为固定映射，
                传零参可调用对象则每次取用时实时求值（插件可运行期安装，装配期应传
                `PluginService.tool_names_by_plugin` 方法本身）。
        """
        self.catalog = catalog
        self.policy = policy
        self.installs = installs
        if callable(tool_names_by_plugin):
            self._tool_names_provider = tool_names_by_plugin
        else:
            fixed = dict(tool_names_by_plugin or {})
            self._tool_names_provider = lambda: fixed

    @property
    def tool_names_by_plugin(self) -> dict[str, set[str]]:
        """{插件 id: 该插件贡献的工具名}（实时求值）。

        Returns:
            映射的副本。
        """
        return {pid: set(names) for pid, names in self._tool_names_provider().items()}

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

        判定口径与 visible_ids 一致（内置优先）：
        可见 = 存在 and 非 hidden and（内置 ? True : 已安装 ? 启用态 : False）。
        内置条目（default_enabled=True）全员强制可见，历史安装记录一并忽略
        （用户对内置条目无启停权）；非内置则已装看用户启停、未装不可见。

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
        states = await self.installs.install_states(user_id, kind)
        return states.get(item_id, False)

    async def visible_ids(self, user_id: str, kind: str) -> set[str]:
        """某用户在某类下可见的全部条目 id。

        判定口径（内置优先）：
        可见 = 存在 and 非 hidden and（内置 ? True : 已安装 ? 启用态 : False）。
        内置条目全员可见（忽略用户安装记录）；非内置的已停用条目一律不可见，
        绝不进入运行期上下文或列表。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。

        Returns:
            可见条目 id 集合。
        """
        items = self.catalog.list_items(kind)
        if not items:
            return set()
        # 一次取回该用户的安装状态，避免逐条查询
        states = await self.installs.install_states(user_id, kind)
        out: set[str] = set()
        for item in items:
            pol = await self.policy.get(kind, item.id)
            if pol["visibility"] == "hidden":
                continue
            # 内置条目全员可见（历史安装记录忽略）；其余以用户启停为准
            if pol["default_enabled"] or states.get(item.id):
                out.add(item.id)
        return out

    async def visible_skill_names(self, user_id: str) -> set[str]:
        """某用户可见的技能名（**只读视图，供测试/诊断用**）。

        注意：运行期过滤请用 hidden_skill_names（黑名单口径），
        白名单口径会把公共技能目录里管理员自建的技能误挡。

        Args:
            user_id: 用户 sub。

        Returns:
            技能名集合。
        """
        names = await self.visible_ids(user_id, "skill")
        for plugin_id in await self.visible_ids(user_id, "plugin"):
            pkg = self.catalog.plugins.get(plugin_id)
            if pkg is not None:
                names |= set(pkg.skills)
        return names

    async def hidden_skill_names(self, user_id: str) -> set[str]:
        """对某用户不可见的技能名。

        规则（三层语义）：
        - 内置技能（能力目录里的 skill 条目）→ 按技能策略；
        - 插件技能（插件包 skills 声明）→ 跟随其插件可见性；
        - 其它技能（公共技能目录里管理员自建/导入的）→ 不在黑名单里（始终可见）。

        Args:
            user_id: 用户 sub。

        Returns:
            不可见技能名集合。
        """
        hidden = {i.id for i in self.catalog.list_items("skill")} \
            - await self.visible_ids(user_id, "skill")
        visible_plugins = await self.visible_ids(user_id, "plugin")
        for plugin_id, pkg in self.catalog.plugins.items():
            if plugin_id not in visible_plugins:
                hidden |= set(pkg.skills)
        return hidden

    async def visible_tool_names(self, user_id: str) -> set[str]:
        """某用户可见的插件工具名（运行期工具过滤用）。

        Args:
            user_id: 用户 sub。

        Returns:
            工具名集合。
        """
        visible = await self.visible_ids(user_id, "plugin")
        mapping = self.tool_names_by_plugin  # 实时求值取一次，避免逐插件重复求值
        out: set[str] = set()
        for plugin_id in visible:
            out |= mapping.get(plugin_id, set())
        return out

    async def can_install(self, user_id: str, kind: str, item_id: str) -> bool:
        """该用户能否安装该条目（存在、未被隐藏、且非内置）。

        内置条目（default_enabled=True）对全员自动可用，无"安装"概念——
        拒绝安装避免出现"装完还能停用"的倒置（用户把自己停到比不装还差）。

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
        return pol["visibility"] != "hidden" and not pol["default_enabled"]

    async def market_items(self, user_id: str, kind: str,
                           *, admin: bool = False) -> list[dict]:
        """市场列表（条目 + 策略 + 安装状态）。

        行的状态字段口径：
        - `default_enabled`：即"内置"——True 表示全员自动可用，用户侧只读
          （无安装/启停概念，市场行只显示「内置」徽标）；
        - `installed`：是否写过安装记录（与是否停用无关；内置条目即使有
          历史记录也被判定忽略）；
        - `enabled`：已装条目是否启用，仅对已装行有意义（未装恒 False）；
        - `visible`：运行期是否真的对该用户可见 —— 可见 = 存在 and 非 hidden
          and（内置 ? True : 已安装 ? 启用态 : False）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            admin: 管理员视角（hidden 条目也返回）。

        Returns:
            条目字典列表；普通用户视角下 hidden 条目不出现。插件行额外带
            `config_schema`（配置字段声明，供用户侧市场渲染安装表单）。
        """
        states = await self.installs.install_states(user_id, kind)
        rows: list[dict] = []
        for item in self.catalog.list_items(kind):
            pol = await self.policy.get(kind, item.id)
            hidden = pol["visibility"] == "hidden"
            if hidden and not admin:
                continue
            is_installed = item.id in states
            is_enabled = states.get(item.id, False)
            # 内置优先：default_enabled=True 恒可见（历史安装记录一并忽略）
            visible = (not hidden) and (pol["default_enabled"] or is_enabled)
            row = {
                "kind": item.kind, "id": item.id, "name": item.name,
                "description": item.description, "source": item.source,
                "visibility": pol["visibility"],
                "default_enabled": pol["default_enabled"],
                "installed": is_installed,
                "enabled": is_enabled,
                "visible": visible,
            }
            # 插件行补配置 schema：用户侧市场据此渲染"安装时填配置"的表单；
            # 非插件条目无此概念，不加该字段（避免前端误判）
            if kind == "plugin":
                pkg = self.catalog.plugins.get(item.id)
                row["config_schema"] = list(pkg.config_schema) if pkg is not None else []
            rows.append(row)
        return rows
