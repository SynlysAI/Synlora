"""插件编排：安装、配置、运行上下文与专家播种。

宿主为每个已安装插件：
1. 注册其工具到共享注册表（未安装 = 工具对 LLM 不可见）；
2. 挂上其技能根（技能留在插件目录，不复制进用户技能目录）；
3. 把解密配置缓存进内存，运行期按命名空间注入 ctx.extra["plugins"]；
4. 按 manifest 的专家模板播种一个助手（内置、带 plugin_id 溯源）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.plugins.loader import PluginPackage, load_plugin_tools

if TYPE_CHECKING:
    from synlys_harness import ToolRegistry

    from app.plugins.config_store import PluginConfigStore

logger = logging.getLogger(__name__)

EXPERT_ID_PREFIX = "asst-plugin-"


class PluginService:
    """插件安装状态与运行期装配。"""

    def __init__(self, registry: "ToolRegistry", config_store: "PluginConfigStore",
                 packages: dict[str, PluginPackage], skill_service: Any,
                 assistant_repo: Any) -> None:
        """保存依赖。

        Args:
            registry: 共享工具注册表（插件工具注册于此）。
            config_store: 插件配置存储（安装状态来源）。
            packages: 扫描到的插件包（{id: PluginPackage}）。
            skill_service: 技能服务（挂插件技能根）。
            assistant_repo: 助手 repo（播种专家）。
        """
        self._registry = registry
        self._config_store = config_store
        self._packages = packages
        self._skill_service = skill_service
        self._assistant_repo = assistant_repo
        self._configs: dict[str, dict] = {}

    @staticmethod
    def expert_id(plugin_id: str) -> str:
        """插件播种专家的助手 id。

        Args:
            plugin_id: 插件 id。

        Returns:
            助手文档 id（asst-plugin-<id>）。
        """
        return f"{EXPERT_ID_PREFIX}{plugin_id}"

    def package(self, plugin_id: str) -> PluginPackage:
        """取插件包。

        Args:
            plugin_id: 插件 id。

        Returns:
            插件包。

        Raises:
            KeyError: 插件不存在。
        """
        if plugin_id not in self._packages:
            raise KeyError(f"插件不存在: {plugin_id}")
        return self._packages[plugin_id]

    async def startup(self) -> None:
        """启动装配：恢复已安装插件的工具、技能根、配置缓存与专家。"""
        for plugin_id in await self._config_store.installed_ids():
            package = self._packages.get(plugin_id)
            if package is None:
                logger.warning("插件配置存在但插件包缺失，已忽略: %s", plugin_id)
                continue
            self._attach(package)
            await self._seed_expert(package)  # 自愈：专家被误删则重启补种
        # 只缓存有插件包的插件配置：包缺失的插件工具未注册，注入其配置无意义且可能外泄凭证。
        self._configs = {
            pid: cfg for pid, cfg in (await self._config_store.all_resolved()).items()
            if pid in self._packages
        }

    def _attach(self, package: PluginPackage) -> None:
        """挂载插件资源：注册工具（幂等）+ 挂技能根。

        Args:
            package: 插件包。
        """
        for fn in load_plugin_tools(package):
            name = fn.__tool_definition__.name
            if self._registry.find(name) is None:
                self._registry.register(fn)
        if package.skills_root is not None:
            self._skill_service.add_root(package.skills_root)

    def _validate(self, package: PluginPackage, values: dict) -> None:
        """校验必填配置。

        Args:
            package: 插件包。
            values: 提交的配置值。

        Raises:
            ValueError: 存在未填的必填字段（消息含缺失字段名）。
        """
        missing = [
            f["key"] for f in package.config_schema
            if f.get("required") and not str(values.get(f["key"]) or "").strip()
        ]
        if missing:
            raise ValueError(f"缺少必填配置: {', '.join(missing)}")

    async def install(self, plugin_id: str, values: dict) -> dict:
        """安装插件：校验 → 存配置 → 挂资源 → 播种专家。

        Args:
            plugin_id: 插件 id。
            values: 页面提交的配置值。

        Returns:
            安装后的状态（不含敏感值）。

        Raises:
            KeyError: 插件不存在。
            ValueError: 必填字段缺失。
        """
        package = self.package(plugin_id)
        self._validate(package, values)
        await self._config_store.save(plugin_id, values, package.config_schema)
        self._attach(package)
        self._configs[plugin_id] = await self._config_store.resolved(plugin_id)
        await self._seed_expert(package)
        return self.state(plugin_id)

    async def update_config(self, plugin_id: str, values: dict) -> dict:
        """更新已安装插件的配置。

        Args:
            plugin_id: 插件 id。
            values: 页面提交的配置值（敏感字段留空 = 保持原值）。

        Returns:
            更新后的状态（不含敏感值）。

        Raises:
            KeyError: 插件不存在。
            ValueError: 插件未安装，或必填字段缺失。
        """
        package = self.package(plugin_id)
        if await self._config_store.get_doc(plugin_id) is None:
            raise ValueError(f"插件未安装: {plugin_id}")
        current = await self._config_store.resolved(plugin_id)
        merged = {**current, **{k: v for k, v in values.items() if v}}
        self._validate(package, merged)
        await self._config_store.save(plugin_id, values, package.config_schema)
        self._configs[plugin_id] = await self._config_store.resolved(plugin_id)
        return self.state(plugin_id)

    async def _seed_expert(self, package: PluginPackage) -> None:
        """按 manifest 专家模板播种助手（已存在则不动）。

        Args:
            package: 插件包。
        """
        if not package.expert:
            return
        doc_id = self.expert_id(package.id)
        if await self._assistant_repo.get(doc_id) is not None:
            return
        expert = package.expert
        await self._assistant_repo.create({
            "_id": doc_id,
            "name": expert.get("name") or f"{package.name}专家",
            "avatar": expert.get("avatar") or "🧩",
            "description": expert.get("description") or package.description,
            "system_prompt": expert.get("system_prompt") or "",
            "tool_whitelist": list(expert.get("tool_whitelist") or []),
            "model_provider_id": None,
            "knowledge_base_ids": list(expert.get("knowledge_base_ids") or []),
            "builtin": True,
            "plugin_id": package.id,
        })

    def context_extra(self) -> dict[str, dict]:
        """已安装插件的解密配置（运行期注入 ctx.extra["plugins"]）。

        Returns:
            {插件 id: 扁平配置} 的副本。
        """
        return {pid: dict(cfg) for pid, cfg in self._configs.items()}

    def state(self, plugin_id: str) -> dict:
        """单个插件的状态（不含敏感值）。

        Args:
            plugin_id: 插件 id。

        Returns:
            含 id/name/version/description/config_schema/installed/configured/
            missing/config/secrets_set 的字典。
        """
        package = self._packages[plugin_id]
        config = self._configs.get(plugin_id, {})
        secrets_set = {
            f["key"]: bool(config.get(f["key"]))
            for f in package.config_schema if f.get("secret")
        }
        missing = [
            f["key"] for f in package.config_schema
            if f.get("required") and not config.get(f["key"])
        ]
        return {
            "id": package.id,
            "name": package.name,
            "version": package.version,
            "description": package.description,
            "config_schema": package.config_schema,
            "installed": plugin_id in self._configs,
            "configured": plugin_id in self._configs and not missing,
            "missing": missing,
            "config": {k: v for k, v in config.items() if not _is_secret(package, k)},
            "secrets_set": secrets_set,
        }

    def list_states(self) -> list[dict]:
        """全部可用插件的状态（按 id 排序）。

        Returns:
            状态字典列表。
        """
        return [self.state(pid) for pid in sorted(self._packages)]


def _is_secret(package: PluginPackage, key: str) -> bool:
    """判断字段在插件 schema 里是否标记为敏感。

    Args:
        package: 插件包。
        key: 字段名。

    Returns:
        True 表示敏感字段（不应回传前端）。
    """
    return any(f["key"] == key and f.get("secret") for f in package.config_schema)
