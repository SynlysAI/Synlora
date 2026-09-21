"""插件编排：安装、配置、运行上下文与专家播种。

宿主为每个已安装插件：
1. 注册其工具到共享注册表（未安装 = 工具对 LLM 不可见）；
2. 挂上其技能根（技能留在插件目录，不复制进用户技能目录）；
3. 把解密配置缓存进内存，运行期按命名空间注入 ctx.extra["plugins"]；
4. 按 manifest 的专家模板播种一个助手（内置、带 plugin_id 溯源）；
5. 注册其声明的任务连接器（manifest 的 connectors_module，接后台任务）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.catalog.loader import (
    PluginPackage,
    load_plugin_connectors,
    load_plugin_tools,
)

if TYPE_CHECKING:
    from synlys_harness import ToolRegistry

    from app.plugins.config_store import PluginConfigStore

logger = logging.getLogger(__name__)

EXPERT_ID_PREFIX = "asst-plugin-"


class PluginService:
    """插件安装状态与运行期装配。"""

    def __init__(self, registry: "ToolRegistry", config_store: "PluginConfigStore",
                 packages: dict[str, PluginPackage], skill_service: Any,
                 assistant_repo: Any, job_connectors: Any = None) -> None:
        """保存依赖。

        Args:
            registry: 共享工具注册表（插件工具注册于此）。
            config_store: 插件配置存储（安装状态来源）。
            packages: 扫描到的插件包（{id: PluginPackage}）。
            skill_service: 技能服务（挂插件技能根）。
            assistant_repo: 助手 repo（播种专家）。
            job_connectors: 任务连接器注册表（插件声明的连接器注册于此）；
                None 时跳过（未接入后台任务的宿主）。
        """
        self._registry = registry
        self._config_store = config_store
        self._packages = packages
        self._skill_service = skill_service
        self._assistant_repo = assistant_repo
        self._job_connectors = job_connectors
        self._configs: dict[str, dict] = {}
        # 安装状态（库事实：存在配置记录）与运行期配置缓存分离——密钥轮换导致
        # 单条解密失败时，_configs 会缺项，但插件仍应报告"已安装"。
        self._installed: set[str] = set()
        # 本会话已由本服务挂载的工具名（{插件 id: {工具名}}），用于区分 startup 重放与跨插件冲突。
        self._attached: dict[str, set[str]] = {}
        # 本服务已注册的连接器 kind（{插件 id: {kind}}），用途同上但对象是连接器
        self._attached_connectors: dict[str, set[str]] = {}

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

    async def startup(self, extra_plugin_ids: "set[str] | None" = None) -> None:
        """启动装配：恢复已安装插件的工具、技能根、配置缓存与专家。

        挂载范围 = 有公共配置的插件 ∪ extra_plugin_ids。注册（attach）是"能力可用性"
        的一层（进程级，谁装都该挂），与按用户的可见性过滤解耦；`_configs` 仍是公共
        配置缓存，只有用户个人安装的插件不进它（用户维度配置由 agent_service 另算）。

        专家播种失败只告警不阻断启动（与 install() 的降级策略对齐，避免单个
        非关键操作让 lifespan 失败、应用起不来）；下次启动或安装时重试。

        Args:
            extra_plugin_ids: 除公共安装外、还需挂载的插件 id（有用户个人安装的插件）。
        """
        # 安装状态（对管理员而言的"已安装"）只认公共配置，不含用户个人安装
        installed_ids = await self._config_store.installed_ids()
        self._installed = set(installed_ids)
        for plugin_id in sorted(set(installed_ids) | set(extra_plugin_ids or ())):
            package = self._packages.get(plugin_id)
            if package is None:
                logger.warning("插件配置存在但插件包缺失，已忽略: %s", plugin_id)
                continue
            self._attach(package)
            try:
                await self._seed_expert(package)  # 自愈：专家被误删则重启补种
            except Exception as exc:  # 播种失败不阻断启动（下次启动或安装时重试）
                logger.warning("插件 %s 启动期专家播种失败: %s", plugin_id, exc)
        # 只缓存有插件包的插件配置：包缺失的插件工具未注册，注入其配置无意义且可能外泄凭证。
        self._configs = {
            pid: cfg for pid, cfg in (await self._config_store.all_resolved()).items()
            if pid in self._packages
        }

    def ensure_attached(self, plugin_id: str) -> bool:
        """确保插件已挂载（注册工具 + 挂技能根；幂等）。

        安装路径的公开入口：用户个人安装插件时也要挂载——注册是"能力可用性"
        （进程级，谁装都该挂），可见性过滤是另一层的事。

        Args:
            plugin_id: 插件 id。

        Returns:
            True 表示已挂载（插件包不存在时返回 False 并告警）。
        """
        package = self._packages.get(plugin_id)
        if package is None:
            logger.warning("插件包不存在，无法挂载: %s", plugin_id)
            return False
        self._attach(package)
        return True

    def _attach(self, package: PluginPackage) -> None:
        """挂载插件资源：注册工具（幂等）+ 挂技能根。

        已由本插件挂载的工具（startup 重放）静默跳过；被其它来源占用的同名工具
        告警跳过，便于排查"工具没生效"。

        Args:
            package: 插件包。
        """
        attached = self._attached.setdefault(package.id, set())
        for fn in load_plugin_tools(package):
            name = fn.__tool_definition__.name
            if name in attached:
                continue  # 本插件已挂载（startup 重放），静默跳过
            if self._registry.find(name) is not None:
                logger.warning("工具 %s 已被其它来源注册，跳过插件 %s 的同名工具",
                               name, package.id)
                continue
            self._registry.register(fn)
            attached.add(name)
        if package.skills_root is not None:
            # plugin=<id>：技能标 source='plugin' 且带归属，管理页/会话开关据此过滤
            self._skill_service.add_root(
                package.skills_root,
                plugin=package.id,
                names=frozenset(package.skills),
            )
        self._attach_connectors(package)

    def _attach_connectors(self, package: PluginPackage) -> None:
        """注册插件声明的任务连接器（幂等；未注入注册表时静默跳过）。

        与工具注册同口径：注册是"能力可用性"（进程级，谁装都该挂），可见性
        过滤由 CapabilityService 另算；被其它来源占用的 kind 告警跳过。

        Args:
            package: 插件包。
        """
        if self._job_connectors is None:
            return
        attached = self._attached_connectors.setdefault(package.id, set())
        for connector in load_plugin_connectors(package):
            kind = str(getattr(connector, "kind", "")).strip()
            if not kind or kind in attached:
                continue  # 无名或本插件已注册（startup 重放），静默跳过
            if kind in self._job_connectors.kinds:
                logger.warning("任务类型 %s 已被其它来源注册，跳过插件 %s 的同名连接器",
                               kind, package.id)
                continue
            try:
                self._job_connectors.register(connector)
            except Exception as exc:  # noqa: BLE001 插件代码不可信：注册失败不得阻断挂载
                # 形状/映射不合法（含插件自定义对象的意外异常）：告警跳过。
                # 与 loader 对插件代码的口径一致——非法包只告警，绝不让 lifespan 失败：
                # startup() 里的 _attach 不在 try 内，且安装记录可能已落库（重启即复现）。
                logger.warning("插件 %s 的连接器 %s 注册失败: %s",
                               package.id, kind, exc)
                continue
            attached.add(kind)

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

        装配（工具注册/技能根/专家播种）失败不回滚配置；专家播种失败降级为告警，
        重启由 startup() 自愈。

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
        self._installed.add(plugin_id)
        self._attach(package)
        self._configs[plugin_id] = await self._config_store.resolved(plugin_id)
        try:
            await self._seed_expert(package)
        except Exception as exc:  # 播种失败不阻断安装：startup() 会重放自愈
            logger.warning("插件 %s 专家播种失败（重启后自愈）: %s", plugin_id, exc)
        return self.state(plugin_id)

    async def update_config(self, plugin_id: str, values: dict,
                            clear_secrets: list[str] | None = None) -> dict:
        """更新已安装插件的配置。

        Args:
            plugin_id: 插件 id。
            values: 页面提交的配置值（敏感字段留空 = 保持原值）。
            clear_secrets: 要清除的敏感字段名列表（None/空 = 不动已存值）；
                同一字段既清除又给新值时**新值生效**（见 PluginConfigStore.save）。

        Returns:
            更新后的状态（不含敏感值）。

        Raises:
            KeyError: 插件不存在。
            ValueError: 插件未安装，或必填字段缺失。
        """
        package = self.package(plugin_id)
        if await self._config_store.get_doc(plugin_id) is None:
            raise ValueError(f"插件未安装: {plugin_id}")
        self._installed.add(plugin_id)
        current = await self._config_store.resolved(plugin_id)
        # 校验必须基于"保存后实际生效的配置"：敏感字段留空保持原值（显式带新值或本次
        # 被清除除外），非敏感字段以提交值为准。
        effective = dict(current)
        for key, value in values.items():
            if _is_secret(package, key) and not str(value or "").strip():
                continue  # 敏感字段留空 = 保持原值
            effective[key] = value
        for key in clear_secrets or ():
            if _is_secret(package, key) and not str(values.get(key) or "").strip():
                effective.pop(key, None)  # 本次清除且未给新值 → 保存后该字段不存在
        self._validate(package, effective)
        await self._config_store.save(plugin_id, values, package.config_schema,
                                      clear_secrets=clear_secrets)
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
        whitelist = [str(t) for t in (expert.get("tool_whitelist") or [])]
        unknown = [t for t in whitelist if self._registry.find(t) is None]
        if unknown:
            logger.warning("插件 %s 专家白名单含未注册工具（该专家将少这些工具）: %s",
                           package.id, unknown)
        await self._assistant_repo.create({
            "_id": doc_id,
            "name": expert.get("name") or f"{package.name}专家",
            "avatar": expert.get("avatar") or "🧩",
            "description": expert.get("description") or package.description,
            "system_prompt": expert.get("system_prompt") or "",
            "tool_whitelist": whitelist,
            "skill_refs": [str(t) for t in (expert.get("skill_refs") or package.skills)],
            "mcp_refs": [str(t) for t in (expert.get("mcp_refs") or [])],
            "suggested_prompts": [
                str(t) for t in (expert.get("suggested_prompts") or [])
            ],
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

    def tool_names_by_plugin(self) -> dict[str, set[str]]:
        """已挂载的工具名按插件分组（可见性过滤用）。

        Returns:
            {插件 id: {工具名}} 的副本。
        """
        return {pid: set(names) for pid, names in self._attached.items()}

    def state(self, plugin_id: str) -> dict:
        """单个插件的状态（不含敏感值）。

        Args:
            plugin_id: 插件 id。

        Returns:
            含 id/name/version/description/config_schema/installed/configured/
            missing/config/secrets_set 的字典，另带附属内容清单：
            skills（[{name, description}]）/ experts（[{id, name}]）/
            tools（工具名列表）——插件的技能与专家统一在插件页查看，
            不再混入技能/助手管理页。
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
        installed = plugin_id in self._installed
        skills = (self._skill_service.skills_under(package.skills_root)
                  if package.skills_root is not None else [])
        experts = ([{"id": self.expert_id(plugin_id),
                     "name": str(package.expert.get("name") or package.name)}]
                   if package.expert else [])
        return {
            "id": package.id,
            "name": package.name,
            "version": package.version,
            "description": package.description,
            "config_schema": package.config_schema,
            "installed": installed,
            "configured": installed and not missing,
            "missing": missing,
            "config": {k: v for k, v in config.items() if not _is_secret(package, k)},
            "secrets_set": secrets_set,
            "skills": [{"name": s["name"], "description": s["description"]}
                       for s in skills],
            "experts": experts,
            "tools": sorted(self._attached.get(plugin_id, set())),
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
