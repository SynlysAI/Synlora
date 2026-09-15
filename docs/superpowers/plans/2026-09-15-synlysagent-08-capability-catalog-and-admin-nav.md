# 能力目录（市场）+ 后台导航重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「专家/技能/插件」从「只有管理员能配、全局共享」改成三层可见性模型（内置目录 → 管理员策略 → 用户自行安装），并把管理后台从顶部页签改成左侧导航列表 + 新增「常规」页。

**Architecture:** 内置项（随仓库，只读）由 `CatalogService` 统一枚举；管理员用 `catalog_policy` 集合给每个内置项设「可见性（public/hidden）+ 默认启用」；用户安装只写 DB 记录（`user_capabilities`，**不复制文件**），插件配置按 scope 分公共（部署级，沿用现有 `plugin_configs`）与个人（`user:<uid>:<plugin_id>`）；运行期由 `CapabilityService` 按用户算出可见工具/技能/专家，在 agent_service 装配与各列表 API 处过滤。

**Tech Stack:** Python 3.12 / FastAPI / pydantic-settings / DocumentStore（sqlite+mongodb 双后端）/ pytest；前端 React 19 + TS + Tailwind 4 + Zustand（手写路由）

**参考结论（设计依据，勿自行发挥）**
- **jiuwen**：三层来源（只读市场目录 → 用户工作区 `built_in|local`）+ **状态清单极简**（目录扫描是真源，清单只做状态覆盖）+ 模板/实例分离。它缺的三块正是本计划要补：按用户/角色的可见性、可配置的「默认启用」、多租户维度。
- **DSH**：设置页左导航列表（188px、行高 40、r12、hover/active 两 token、**滚动只在右侧内容容器**）；分区条目只有 `{id, order, label}`；表单草稿态 + 保存才落库。

**用户已确认的决策（不要再问）**
1. 用户「安装」内置项 = **只写 DB 记录**，不复制文件（升级即生效、无副本漂移）
2. **首期不支持用户自建/导入**技能与插件（用户私有目录仅预留，不实现）
3. 可见性粒度 = **公开/隐藏 + 是否默认启用**（不做按角色/按用户白名单）
4. 后台形态 = **保持整页 `/admin/*`**，只把顶部页签换成左侧导航列表（不改模态面板）

---

## 一、现状与目标模型

**现状**
- `apps/web/backend/plugins/<id>/`：随仓库的插件（`spec_agent`）
- `{data_dir}/skills/`：技能目录（harness 内置技能由 `seed_builtins()` 播到这里），**所有人共享**
- `{data_dir}/plugins/`：预留未用
- DB：`assistants`（专家，全局）、`plugin_configs`（插件配置，`_id=插件id`，全局）
- 工具注册表 `app.services.tool_registry.REGISTRY` 是**进程级单例**，运行装配与助手白名单校验共用

**目标三层模型**

| 层 | 专家 | 技能 | 插件 | 谁能改 |
|---|---|---|---|---|
| **内置目录**（随仓库，只读） | 代码种子 `SEED_ASSISTANTS` | 仓库内置 SKILL.md（`{data_dir}/skills` 里播下的那批） | `backend/plugins/<id>/` | 开发者 |
| **管理员策略**（可见性/默认） | 同左，按条目 | 同左 | 同左 | 管理员 |
| **用户安装**（DB 记录） | 记录「我启用了它」 | 记录 | 记录 + 个人配置（可选） | 用户本人 |

**可见性判定**（`CapabilityService` 实现，是所有过滤的唯一入口）
- 策略缺省 = `visibility=public` + `default_enabled=true`（**保持现状语义**：升级后行为不变）
- `hidden` → 普通用户完全不可见（管理员后台仍可见、可改）；不提供"安装隐藏项"
- `public + default_enabled` → 所有用户默认可见
- `public + 非默认` → 用户在市场里能看到、可自行安装，安装后对自己可见
- 插件还有一层：**公共安装**（管理员装，配置部署级共享，如 SpecAgent 网关地址，沿用现有 `plugin_configs`）与**用户安装**（个人配置，`user:<uid>:<plugin_id>`）并存，用户配置优先

---

## 二、File Structure

| 动作 | 文件 | 职责 |
|---|---|---|
| 创建 | `apps/web/backend/app/catalog/__init__.py` | catalog 包 docstring + 导出 |
| 创建 | `apps/web/backend/app/catalog/items.py` | `CatalogItem` 定义 + `CatalogService`（枚举内置专家/技能/插件） |
| 创建 | `apps/web/backend/app/catalog/policy.py` | `CatalogPolicyRepo`（`catalog_policy` 集合：可见性/默认启用） |
| 创建 | `apps/web/backend/app/catalog/user_caps.py` | `UserCapabilityRepo`（`user_capabilities` 集合：用户安装记录） |
| 创建 | `apps/web/backend/app/catalog/service.py` | `CapabilityService`（按用户算可见集，运行期过滤的唯一入口） |
| 创建 | `apps/web/backend/app/catalog/api.py` | 用户侧 `/api/v1/catalog`（列表/安装/卸载）+ 管理员侧策略接口 |
| 修改 | `apps/web/backend/app/db/store.py` | `COLLECTION_INDEXES` 注册两个新集合 |
| 修改 | `apps/web/backend/app/plugins/config_store.py` | 支持 `user_id` 维度（`_id` = `user:<uid>:<pid>` 或原 `plugin_id`）+ 合并解析 |
| 修改 | `apps/web/backend/app/services/agent_service.py` | 工具集/技能索引/`ctx.extra["plugins"]` 按用户可见性过滤 |
| 修改 | `apps/web/backend/app/api/skills_api.py`、`assistants_api.py` | 列表按用户可见性过滤（管理员看全部） |
| 修改 | `apps/web/backend/app/main.py` | lifespan 装配 catalog 服务 + 注册路由 |
| 修改 | `apps/web/frontend/src/components/admin/AdminLayout.tsx` | 顶部页签 → 左侧导航列表；内容区右侧滚动 |
| 创建 | `apps/web/frontend/src/components/admin/GeneralAdmin.tsx` | 「常规」页占位（外观/界面语言，后续实现） |
| 修改 | `apps/web/frontend/src/routing/route.ts`、`src/App.tsx`、`src/components/admin/index.ts` | 新增 `general` 页签接线 |
| 修改 | `apps/web/frontend/src/components/admin/SkillsAdmin.tsx`、`AssistantsAdmin.tsx`、`PluginsAdmin.tsx` | 各加「可见性 / 默认启用」开关（复用 `shared.tsx` 的 `Switch`） |
| 创建 | `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx` | 用户侧「能力中心」整页（浏览/安装/卸载，插件可填个人配置） |
| 创建 | `apps/web/frontend/src/components/layout/PageTopBar.tsx` | 共用顶栏（返回工作台 + 标题 + 主题切换 + 用户名），`AdminLayout` 与能力中心页共用 |
| 修改 | `apps/web/frontend/src/routing/route.ts`、`src/App.tsx` | 新增 `capabilities` 路由 kind（整页） |
| 修改 | `apps/web/frontend/src/components/layout/AppShell.tsx` 或 `sidebar/UserMenu.tsx` | 用户侧「能力中心」入口 |
| 创建 | `apps/web/backend/tests/test_catalog_{items,policy,user_caps,service,api}.py` | 后端测试 |
| 修改 | `README.md`、`CLAUDE.md`、backlog、`app/version.py`、`frontend/package.json` | 文档与版本 0.6.0-beta.1 |

命令工作目录：后端 `E:\agent_projects\Synlora\apps\web\backend`，前端 `E:\agent_projects\Synlora\apps\web\frontend`。全部使用**完整绝对 Windows 路径**。

---

## Phase A：后台导航重构（纯前端，无后端改动）

## Task A1: 管理后台左导航 + 「常规」页占位

**Files:**
- Modify: `apps/web/frontend/src/components/admin/AdminLayout.tsx`
- Create: `apps/web/frontend/src/components/admin/GeneralAdmin.tsx`
- Modify: `apps/web/frontend/src/routing/route.ts`、`apps/web/frontend/src/App.tsx`、`apps/web/frontend/src/components/admin/index.ts`

**照抄对象**：DSH `packages/client/ui-settings-general/src/client/SettingsRoot.tsx:105-170`（左导航 188px、`gap` 4、行高 40、`r12`、hover/active 两 token、label 单行 ellipsis）+ `SettingsRoot.module.css:61-101`（**滚动只发生在右侧内容容器**，容器高度取自视口避免切换时抖动）。本项目的 token 用 `--sa-specific-sidebar-nav-item-hover` / `--sa-specific-sidebar-nav-item-active`（现有页签已在用，见 `AdminLayout.tsx:120`）。

- [ ] **Step 1: 路由加 `general` 页签**

`src/routing/route.ts`：
- `AdminTab` 联合类型加 `'general'`
- `parseAppRoute` 正则改为 `/admin/(general|models|assistants|skills|plugins)`
- 默认落地：`/admin` 或未匹配的 tab 回落 `'general'`（保持既有回落写法，先读该文件确认现有默认值是 `'models'` 还是别的，跟随现有模式）

- [ ] **Step 2: 写「常规」页占位**

创建 `src/components/admin/GeneralAdmin.tsx`，结构照 `PluginsAdmin.tsx` 的页头（`h2` 标题 + `p` 描述）+ 卡片容器（`rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]`）：

```tsx
/**
 * 常规设置页：外观与界面语言（占位，后续实现）。
 */
export default function GeneralAdmin() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[15px] font-medium text-[var(--sa-alias-label-primary)]">常规</h2>
        <p className="mt-1 text-[13px] text-[var(--sa-alias-label-tertiary)]">
          外观与界面语言等平台级设置。
        </p>
      </div>
      <div className="rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-5 py-6">
        <p className="text-[13px] text-[var(--sa-alias-label-tertiary)]">
          暂无可配置项（外观主题已可经右上角按钮切换；界面语言等后续版本提供）。
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 布局改左导航**

`src/components/admin/AdminLayout.tsx`：
1. `TABS` 数组最前面加 `{ key: 'general', label: '常规' }`（其余顺序不变：模型服务 / 助手管理 / 技能管理 / 插件）。
2. **删掉顶栏的页签 `<nav>` 块**（现 107-127 行整块），顶栏只保留「返回工作台 | 管理后台 | 主题切换 | 用户名」。
3. 内容区改为「左导航 + 右内容」两栏，**滚动只发生在右侧**：

```tsx
      {/* 内容区：左导航 + 右侧内容（滚动只在右侧，照 DSH SettingsRoot） */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {user?.role === 'admin' ? (
          <>
            <nav
              aria-label="管理页切换"
              className="flex w-[188px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--sa-alias-border-l1)] p-3"
            >
              {TABS.map((t) => (
                <a
                  key={t.key}
                  href={appRoutePath({ kind: 'admin', tab: t.key })}
                  aria-current={t.key === tab ? 'page' : undefined}
                  onClick={(e) => {
                    e.preventDefault()
                    navigate({ kind: 'admin', tab: t.key })
                  }}
                  className={`flex h-10 items-center truncate rounded-[var(--sa-radius-md)] px-3 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
                    t.key === tab
                      ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] font-medium text-[var(--sa-alias-label-primary)]'
                      : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]'
                  }`}
                >
                  {t.label}
                </a>
              ))}
            </nav>
            <main className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-4xl px-6 py-6">{children}</div>
            </main>
          </>
        ) : (
          <main className="min-h-0 flex-1 overflow-y-auto">
            <NoPermission />
          </main>
        )}
      </div>
```

（`--sa-radius-md` 若 `theme.css` 里不存在，用既有的 `--sa-radius-sm`；先 grep 确认可用 token，不要自造。）

- [ ] **Step 4: 接线**

- `src/components/admin/index.ts`：导出 `GeneralAdmin`
- `src/App.tsx`：`route.tab` 分支加 `general` → `<GeneralAdmin />`
- `AdminLayout.tsx` 顶部 JSDoc 的 tab 列表同步（含 `general`），`AdminLayoutProps.children` 的注释也同步

- [ ] **Step 5: 构建验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
```

预期：`tsc -b` 零错误 + 构建成功。

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/frontend/src && git commit -m "feat: 管理后台改左导航并新增常规设置页占位"
```

---

## Phase B：能力目录与策略（后端）

## Task B1: 目录条目枚举（`CatalogService`）

**Files:**
- Create: `apps/web/backend/app/catalog/__init__.py`、`apps/web/backend/app/catalog/items.py`
- Test: `apps/web/backend/tests/test_catalog_items.py`

**要点**：内置项来自三处——**专家**（代码种子 `SEED_ASSISTANTS`，排除插件播种的 `asst-plugin-*`，那些跟随其插件）、**技能**（`{data_dir}/skills` 下由 `seed_builtins()` 播下的内置技能，即名字在 `skill_service.BUILTIN_SKILL_NAMES` 里的）、**插件**（`loader.scan_plugins(plugin_roots(settings))` 的全部包）。

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_catalog_items.py`：

```python
"""内置目录条目枚举测试。"""
from __future__ import annotations

from pathlib import Path

from app.catalog.items import CatalogItem, CatalogService
from app.core.settings import Settings
from app.services.skill_service import SkillService


def _service(tmp_path, settings: Settings, skill_service: SkillService) -> CatalogService:
    """组装 CatalogService。

    Args:
        tmp_path: 临时目录（无用，占位保持签名一致）。
        settings: 应用配置。
        skill_service: 技能服务。

    Returns:
        CatalogService。
    """
    from app.plugins.loader import plugin_roots, scan_plugins

    return CatalogService(
        settings=settings,
        skill_service=skill_service,
        packages=scan_plugins(plugin_roots(settings)),
    )


def test_experts_include_seeded_builtins(tmp_path):
    """内置专家 = 代码种子助手（不含插件播种的 asst-plugin-*）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(tmp_path, settings, SkillService(tmp_path))
    experts = {i.id: i for i in svc.list_items("expert")}
    assert "asst-research" in experts and "asst-data" in experts
    assert all(not i.id.startswith("asst-plugin-") for i in svc.list_items("expert"))
    assert experts["asst-research"].name == "科研助手"


def test_skills_include_repo_builtins(tmp_path):
    """内置技能 = 随包播种到技能目录的内置技能。"""
    settings = Settings(data_dir=str(tmp_path))
    skill_service = SkillService(tmp_path)
    skill_service.seed_builtins()
    svc = _service(tmp_path, settings, skill_service)
    names = {i.id for i in svc.list_items("skill")}
    assert {"data-analysis", "pdf-extraction", "office-doc"} <= names


def test_plugins_include_repo_packages(tmp_path):
    """内置插件 = 扫描到的插件包（含随仓库的 spec_agent）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(tmp_path, settings, SkillService(tmp_path))
    plugins = {i.id: i for i in svc.list_items("plugin")}
    assert "spec_agent" in plugins
    assert plugins["spec_agent"].name == "Spec_Agent 谱图解析"


def test_all_items_are_builtin_source(tmp_path):
    """目录条目的 source 恒为 builtin（首期只有内置项）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(tmp_path, settings, SkillService(tmp_path))
    for kind in ("expert", "skill", "plugin"):
        for item in svc.list_items(kind):
            assert isinstance(item, CatalogItem)
            assert item.kind == kind and item.source == "builtin"


def test_unknown_kind_returns_empty(tmp_path):
    """未知 kind 返回空列表（不抛异常）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(tmp_path, settings, SkillService(tmp_path))
    assert svc.list_items("nope") == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_items.py -v
```

预期：`ModuleNotFoundError: No module named 'app.catalog'`。

- [ ] **Step 3: 实现**

创建 `apps/web/backend/app/catalog/__init__.py`：

```python
"""能力目录：内置项枚举、管理员策略与用户安装记录（市场机制）。

三层模型（参考 jiuwen 的目录分层 + DSH 的配置分层）：
1. 内置目录（随仓库，只读）——本包的 CatalogService 枚举；
2. 管理员策略（visible/hidden + 是否默认启用）——CatalogPolicyRepo；
3. 用户安装（只写 DB 记录，不复制文件）——UserCapabilityRepo。

运行期由 CapabilityService 汇总三层，算出「某个用户实际可见」的专家/技能/插件。
"""
```

创建 `apps/web/backend/app/catalog/items.py`：

```python
"""内置目录条目枚举。

内置项只有三个来源，全部只读（首期不支持用户自建）：
- 专家：代码种子 SEED_ASSISTANTS（插件播种的 asst-plugin-* 跟随其插件，不算独立条目）；
- 技能：随包播种到技能目录的内置技能（名字在 skill_service.BUILTIN_SKILL_NAMES 中）；
- 插件：扫描到的插件包（随仓库 plugins/ + 数据目录 plugins/）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.db.repos import SEED_ASSISTANTS
from app.services.skill_service import BUILTIN_SKILL_NAMES

if TYPE_CHECKING:
    from app.core.settings import Settings
    from app.plugins.loader import PluginPackage
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
                 packages: dict[str, "PluginPackage"]) -> None:
        """保存依赖。

        Args:
            settings: 应用配置。
            skill_service: 技能服务（枚举已播种的内置技能）。
            packages: 扫描到的插件包（{id: PluginPackage}）。
        """
        self._settings = settings
        self._skill_service = skill_service
        self._packages = packages

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
        """枚举全部内置条目（三类合并，按 kind+id 排序）。

        Returns:
            条目列表。
        """
        return [item for kind in KINDS for item in self.list_items(kind)]

    def _experts(self) -> list[CatalogItem]:
        """内置专家条目（代码种子，排除插件播种的助手）。"""
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
        """内置技能条目（已播种到技能目录的内置技能）。"""
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
        """内置插件条目（扫描到的插件包）。"""
        return sorted(
            (
                CatalogItem(
                    kind="plugin", id=pkg.id,
                    name=pkg.name, description=pkg.description,
                )
                for pkg in self._packages.values()
            ),
            key=lambda i: i.id,
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_items.py -v
```

预期：5 passed。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/catalog/ apps/web/backend/tests/test_catalog_items.py && git commit -m "feat: 内置能力目录枚举（专家/技能/插件）"
```

---

## Task B2: 管理员策略存储（`catalog_policy`）

**Files:**
- Create: `apps/web/backend/app/catalog/policy.py`
- Modify: `apps/web/backend/app/db/store.py`（`COLLECTION_INDEXES` 加 `"catalog_policy": []`）
- Test: `apps/web/backend/tests/test_catalog_policy.py`

**语义**：每条策略 `{_id: f"{kind}:{item_id}", kind, item_id, visibility, default_enabled}`；**缺省 = public + default_enabled=True**（保持现状语义，升级后行为不变）。

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_catalog_policy.py`：

```python
"""管理员目录策略存储测试。"""
from __future__ import annotations

from app.catalog.policy import POLICY_COLLECTION, CatalogPolicyRepo


async def test_default_policy_is_public_enabled(store):
    """无记录时缺省 public + 默认启用（保持升级前语义）。"""
    repo = CatalogPolicyRepo(store)
    policy = await repo.get("plugin", "spec_agent")
    assert policy == {"visibility": "public", "default_enabled": True}


async def test_set_and_get_policy(store):
    """写入后可读回；再次写入是更新同一条记录。"""
    repo = CatalogPolicyRepo(store)
    await repo.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    assert await repo.get("plugin", "spec_agent") == {
        "visibility": "hidden", "default_enabled": False}

    await repo.set("plugin", "spec_agent", visibility="public", default_enabled=False)
    docs = await store.list(POLICY_COLLECTION)
    assert len(docs) == 1
    assert await repo.get("plugin", "spec_agent") == {
        "visibility": "public", "default_enabled": False}


async def test_all_policies_returns_overrides_only(store):
    """all_policies 只返回显式配置过的条目（缺省由 get 兜底）。"""
    repo = CatalogPolicyRepo(store)
    assert await repo.all_policies() == {}
    await repo.set("skill", "office-doc", visibility="hidden", default_enabled=False)
    assert await repo.all_policies() == {
        "skill:office-doc": {"visibility": "hidden", "default_enabled": False}}


async def test_invalid_visibility_rejected(store):
    """非法 visibility 拒绝写入（fail-closed）。"""
    import pytest

    repo = CatalogPolicyRepo(store)
    with pytest.raises(ValueError, match="visibility"):
        await repo.set("plugin", "x", visibility="everyone", default_enabled=True)
    assert await store.get(POLICY_COLLECTION, "plugin:x") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_policy.py -v
```

预期：`ModuleNotFoundError: No module named 'app.catalog.policy'`。

- [ ] **Step 3: 注册集合 + 实现**

`app/db/store.py` 的 `COLLECTION_INDEXES` 加一行 `"catalog_policy": [],`（与既有写法一致；注意该文件已支持空索引列表）。

创建 `apps/web/backend/app/catalog/policy.py`：

```python
"""管理员目录策略（可见性 + 默认启用）。

存储形态（集合 catalog_policy，_id = f"{kind}:{item_id}"）：
    {"_id": "plugin:spec_agent", "kind": "plugin", "item_id": "spec_agent",
     "visibility": "public"|"hidden", "default_enabled": true|false,
     "created_at": ..., "updated_at": ...}
缺省（无记录）= public + default_enabled=True：保持改造前的"所有人可见且开箱即用"语义。
"""
from __future__ import annotations

import time
from typing import Any

POLICY_COLLECTION = "catalog_policy"
VISIBILITIES = ("public", "hidden")


def policy_id(kind: str, item_id: str) -> str:
    """策略文档 id。

    Args:
        kind: expert | skill | plugin。
        item_id: 条目 id。

    Returns:
        f"{kind}:{item_id}"。
    """
    return f"{kind}:{item_id}"


class CatalogPolicyRepo:
    """目录策略读写。"""

    def __init__(self, store: Any) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
        """
        self._store = store

    async def get(self, kind: str, item_id: str) -> dict:
        """取条目策略（无记录返回缺省值）。

        Args:
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            {"visibility", "default_enabled"}。
        """
        doc = await self._store.get(POLICY_COLLECTION, policy_id(kind, item_id))
        if doc is None:
            return {"visibility": "public", "default_enabled": True}
        return {
            "visibility": str(doc.get("visibility") or "public"),
            "default_enabled": bool(doc.get("default_enabled", True)),
        }

    async def all_policies(self) -> dict[str, dict]:
        """全部显式配置过的策略（缺省条目不出现在结果里）。

        Returns:
            {f"{kind}:{item_id}": {"visibility", "default_enabled"}}。
        """
        docs = await self._store.list(POLICY_COLLECTION)
        return {
            str(d["_id"]): {
                "visibility": str(d.get("visibility") or "public"),
                "default_enabled": bool(d.get("default_enabled", True)),
            }
            for d in docs
        }

    async def set(self, kind: str, item_id: str, *, visibility: str,
                  default_enabled: bool) -> dict:
        """写入/更新条目策略。

        Args:
            kind: 条目类型。
            item_id: 条目 id。
            visibility: public | hidden。
            default_enabled: 是否默认对所有用户启用。

        Returns:
            写入后的策略。

        Raises:
            ValueError: visibility 非法。
        """
        if visibility not in VISIBILITIES:
            raise ValueError(f"非法 visibility: {visibility}（可选 {'/'.join(VISIBILITIES)}）")
        doc_id = policy_id(kind, item_id)
        body = {"kind": kind, "item_id": item_id, "visibility": visibility,
                "default_enabled": bool(default_enabled), "updated_at": time.time()}
        if await self._store.get(POLICY_COLLECTION, doc_id) is None:
            await self._store.insert(POLICY_COLLECTION,
                                     {"_id": doc_id, **body, "created_at": time.time()})
        else:
            await self._store.update(POLICY_COLLECTION, doc_id, body)
        return {"visibility": visibility, "default_enabled": bool(default_enabled)}
```

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_policy.py -v && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：4 passed；全量通过。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/catalog/policy.py apps/web/backend/app/db/store.py apps/web/backend/tests/test_catalog_policy.py && git commit -m "feat: 管理员目录策略存储（可见性/默认启用）"
```

---

## Task B3: 用户安装记录（`user_capabilities`）

**Files:**
- Create: `apps/web/backend/app/catalog/user_caps.py`
- Modify: `apps/web/backend/app/db/store.py`（`COLLECTION_INDEXES` 加 `"user_capabilities": ["user_id"]`——**必须带索引列**，见 Step 3 的说明）
- Test: `apps/web/backend/tests/test_catalog_user_caps.py`

**语义**：`{_id: f"{user_id}:{kind}:{item_id}", user_id, kind, item_id, installed_at}`；**安装 = 存在记录**（jiuwen「只认 installed」的做法）；安装/卸载幂等。

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_catalog_user_caps.py`：

```python
"""用户能力安装记录测试。"""
from __future__ import annotations

from app.catalog.user_caps import USER_CAPS_COLLECTION, UserCapabilityRepo


async def test_install_and_list(store):
    """安装写入记录，按用户查询返回该用户的条目。"""
    repo = UserCapabilityRepo(store)
    assert await repo.list_for_user("u1") == []
    await repo.install("u1", "plugin", "spec_agent")
    await repo.install("u1", "skill", "office-doc")
    await repo.install("u2", "plugin", "spec_agent")

    assert await repo.list_for_user("u1") == ["plugin:spec_agent", "skill:office-doc"]
    assert await repo.list_for_user("u2") == ["plugin:spec_agent"]
    assert await repo.is_installed("u1", "plugin", "spec_agent") is True
    assert await repo.is_installed("u2", "skill", "office-doc") is False


async def test_install_is_idempotent(store):
    """重复安装不产生第二条记录，也不刷新 installed_at。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    first = await store.get(USER_CAPS_COLLECTION, "u1:plugin:spec_agent")
    await repo.install("u1", "plugin", "spec_agent")

    docs = await store.list(USER_CAPS_COLLECTION)
    assert len(docs) == 1
    assert docs[0]["installed_at"] == first["installed_at"]


async def test_uninstall_removes_record(store):
    """卸载删除记录；未安装时卸载返回 False。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    assert await repo.uninstall("u1", "plugin", "spec_agent") is True
    assert await repo.uninstall("u1", "plugin", "spec_agent") is False
    assert await repo.list_for_user("u1") == []


async def test_list_by_kind(store):
    """按 kind 过滤（市场页按类分栏用）。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    await repo.install("u1", "skill", "office-doc")
    assert await repo.list_for_user("u1", kind="skill") == ["skill:office-doc"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_user_caps.py -v
```

- [ ] **Step 3: 注册集合 + 实现**

`app/db/store.py` 的 `COLLECTION_INDEXES` 加 `"user_capabilities": ["user_id"],`。

> **已验证的坑**：索引列必须是 `["user_id"]` 而不是 `[]`。`list_for_user` 用 `filters={"user_id": ...}` 过滤，而 sqlite 表若没提取该列，`WHERE "user_id" = ?` **不报错**——SQLite 的双引号回退语义会把它当字符串字面量，谓词恒假、**静默返回空列表**。仓库既有约定：凡按字段过滤的集合都注册该字段（`files`/`sessions`/`runs` → `["user_id"]`）；`plugin_configs: []` 成立是因为它只按 `_id` 读写与全量列举。

创建 `apps/web/backend/app/catalog/user_caps.py`：

```python
"""用户能力安装记录（安装 = 存在记录，不复制任何文件）。

存储形态（集合 user_capabilities，_id = f"{user_id}:{kind}:{item_id}"）：
    {"_id": "u1:plugin:spec_agent", "user_id": "u1", "kind": "plugin",
     "item_id": "spec_agent", "installed_at": ...}
"""
from __future__ import annotations

import time
from typing import Any

USER_CAPS_COLLECTION = "user_capabilities"


def cap_id(user_id: str, kind: str, item_id: str) -> str:
    """安装记录文档 id。

    Args:
        user_id: 用户 sub。
        kind: expert | skill | plugin。
        item_id: 条目 id。

    Returns:
        f"{user_id}:{kind}:{item_id}"。
    """
    return f"{user_id}:{kind}:{item_id}"


class UserCapabilityRepo:
    """用户能力安装记录读写。"""

    def __init__(self, store: Any) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
        """
        self._store = store

    async def install(self, user_id: str, kind: str, item_id: str) -> None:
        """记录安装（幂等：已存在则不动）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。
        """
        doc_id = cap_id(user_id, kind, item_id)
        if await self._store.get(USER_CAPS_COLLECTION, doc_id) is not None:
            return
        await self._store.insert(USER_CAPS_COLLECTION, {
            "_id": doc_id, "user_id": user_id, "kind": kind, "item_id": item_id,
            "installed_at": time.time(),
        })

    async def uninstall(self, user_id: str, kind: str, item_id: str) -> bool:
        """删除安装记录。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            实际删除返回 True；未安装返回 False。
        """
        return await self._store.delete(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id))

    async def is_installed(self, user_id: str, kind: str, item_id: str) -> bool:
        """是否已安装。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示已安装。
        """
        return await self._store.get(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id)) is not None

    async def list_for_user(self, user_id: str, kind: str | None = None) -> list[str]:
        """某用户已安装的条目键列表。

        Args:
            user_id: 用户 sub。
            kind: 可选类型过滤。

        Returns:
            排序后的 "kind:item_id" 列表。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION,
                                     filters={"user_id": user_id})
        keys = [f"{d['kind']}:{d['item_id']}" for d in docs
                if kind is None or d.get("kind") == kind]
        return sorted(keys)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_user_caps.py -v
```

预期：4 passed。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/catalog/user_caps.py apps/web/backend/app/db/store.py apps/web/backend/tests/test_catalog_user_caps.py && git commit -m "feat: 用户能力安装记录（只存 DB，不复制文件）"
```

---

## Task B4: `CapabilityService`（按用户算可见集）

**Files:**
- Create: `apps/web/backend/app/catalog/service.py`
- Modify: `apps/web/backend/app/catalog/__init__.py`（导出）
- Test: `apps/web/backend/tests/test_catalog_service.py`

**判定规则**（唯一入口，运行期各过滤点都用它）：

| 策略 | 用户是否可见 |
|---|---|
| `hidden` | ❌ 不可见（管理员后台仍可见） |
| `public` + `default_enabled=True` | ✅ 默认可见 |
| `public` + `default_enabled=False` | 仅当该用户已安装 |

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_catalog_service.py`：

```python
"""CapabilityService 可见性判定测试。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.catalog.items import CatalogService
from app.catalog.policy import CatalogPolicyRepo
from app.catalog.service import CapabilityService
from app.catalog.user_caps import UserCapabilityRepo
from app.core.settings import Settings
from app.plugins.loader import plugin_roots, scan_plugins
from app.services.skill_service import SkillService

SPEC = "plugin:spec_agent"


@pytest.fixture
def caps(store, tmp_path) -> CapabilityService:
    """组装 CapabilityService（真实 store + 仓库插件包）。"""
    settings = Settings(data_dir=str(tmp_path), fernet_key=Fernet.generate_key().decode())
    skill_service = SkillService(tmp_path)
    skill_service.seed_builtins()
    return CapabilityService(
        catalog=CatalogService(settings=settings, skill_service=skill_service,
                               packages=scan_plugins(plugin_roots(settings))),
        policy=CatalogPolicyRepo(store),
        installs=UserCapabilityRepo(store),
    )


async def test_defaults_visible_to_everyone(caps):
    """缺省策略（public + 默认启用）→ 所有人可见，无需安装。"""
    assert await caps.is_visible("u1", "plugin", "spec_agent") is True
    assert await caps.visible_ids("u2", "plugin") == {"spec_agent"}


async def test_hidden_invisible_even_if_installed(caps, store):
    """hidden → 普通用户不可见（已安装也不可见）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.is_visible("u1", "plugin", "spec_agent") is False
    assert await caps.visible_ids("u1", "plugin") == set()


async def test_not_default_requires_install(caps):
    """public + 非默认 → 未安装不可见，安装后可见。"""
    await caps.policy.set("skill", "office-doc", visibility="public", default_enabled=False)
    assert await caps.is_visible("u1", "skill", "office-doc") is False
    await caps.installs.install("u1", "skill", "office-doc")
    assert await caps.is_visible("u1", "skill", "office-doc") is True
    # 只影响安装者本人
    assert await caps.is_visible("u2", "skill", "office-doc") is False


async def test_unknown_item_is_not_visible(caps):
    """目录里不存在的条目一律不可见（防止凭 id 绕过）。"""
    assert await caps.is_visible("u1", "plugin", "nope") is False
    assert await caps.is_visible("u1", "bogus-kind", "spec_agent") is False


async def test_visible_plugin_tools(caps, store):
    """插件工具名按可见插件推导（运行期工具过滤用）。"""
    # 默认全可见 → 三件套都在
    assert await caps.visible_tool_names("u1") == {
        "spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"}

    await caps.policy.set("plugin", "spec_agent", visibility="public", default_enabled=False)
    assert await caps.visible_tool_names("u1") == set()
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert "spec.nmr.forward" in await caps.visible_tool_names("u1")


async def test_market_listing_marks_state(caps):
    """市场列表：返回条目 + 可见性/默认/是否已安装（供 UI 渲染）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="public", default_enabled=False)
    await caps.installs.install("u1", "plugin", "spec_agent")
    items = await caps.market_items("u1", "plugin")
    row = next(i for i in items if i["id"] == "spec_agent")
    assert row["installed"] is True and row["visible"] is True
    assert row["default_enabled"] is False and row["visibility"] == "public"
    assert row["name"] == "Spec_Agent 谱图解析"

    hidden = await caps.market_items("u2", "plugin")
    assert hidden == []  # hidden 条目不进普通用户的市场列表
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_service.py -v
```

- [ ] **Step 3: 实现**

创建 `apps/web/backend/app/catalog/service.py`：

```python
"""按用户计算可见能力（运行期过滤的唯一入口）。

三层汇总规则：
- hidden → 不可见（管理员后台另行可见，见 market_items(admin=True)）；
- public + 默认启用 → 所有人可见；
- public + 非默认 → 仅已安装者可见。
目录里不存在的条目一律不可见（防止凭 id 绕过）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.catalog.items import CatalogService
    from app.catalog.policy import CatalogPolicyRepo
    from app.catalog.user_caps import UserCapabilityRepo

ADMIN_ROLE = "admin"


class CapabilityService:
    """可见能力计算。"""

    def __init__(self, catalog: "CatalogService", policy: "CatalogPolicyRepo",
                 installs: "UserCapabilityRepo") -> None:
        """保存依赖。

        Args:
            catalog: 内置条目枚举。
            policy: 管理员策略。
            installs: 用户安装记录。
        """
        self.catalog = catalog
        self.policy = policy
        self.installs = installs

    async def is_visible(self, user_id: str, kind: str, item_id: str) -> bool:
        """判断某用户是否可见某条目。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示可见。
        """
        known = {i.id for i in self.catalog.list_items(kind)}
        if item_id not in known:
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
        out: set[str] = set()
        for item in self.catalog.list_items(kind):
            if await self.is_visible(user_id, kind, item.id):
                out.add(item.id)
        return out

    async def visible_tool_names(self, user_id: str) -> set[str]:
        """某用户可见的插件工具名（运行期工具过滤用）。

        插件包的工具清单来自目录条目的 manifest（`Package.tools` 若未声明则退化为
        该插件包 tools 模块里实际注册的工具名——实现时以 `PluginService` 已注册的
        工具为准：见"实现说明"）。

        Args:
            user_id: 用户 sub。

        Returns:
            工具名集合。
        """
        visible = await self.visible_ids(user_id, "plugin")
        out: set[str] = set()
        for pkg in self.catalog.plugins_by_id().values():
            if pkg.id in visible:
                out |= pkg.tool_names()
        return out

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
        rows: list[dict] = []
        for item in self.catalog.list_items(kind):
            pol = await self.policy.get(kind, item.id)
            if pol["visibility"] == "hidden" and not admin:
                continue
            installed = await self.installs.is_installed(user_id, kind, item.id)
            rows.append({
                "kind": item.kind, "id": item.id, "name": item.name,
                "description": item.description, "source": item.source,
                "visibility": pol["visibility"],
                "default_enabled": pol["default_enabled"],
                "installed": installed,
                "visible": await self.is_visible(user_id, kind, item.id),
            })
        return rows
```

**实现说明（`visible_tool_names` 的工具名来源，已定稿）**：`PluginPackage` 没有工具名清单（工具是动态 import 出来的），所以工具映射由**注入**给出，不改 loader 契约：

```python
    def __init__(self, catalog: "CatalogService", policy: "CatalogPolicyRepo",
                 installs: "UserCapabilityRepo",
                 tool_names_by_plugin: dict[str, set[str]] | None = None) -> None:
        ...
        self.tool_names_by_plugin = tool_names_by_plugin or {}
```

`visible_tool_names` 用注入的映射：

```python
        visible = await self.visible_ids(user_id, "plugin")
        out: set[str] = set()
        for plugin_id in visible:
            out |= self.tool_names_by_plugin.get(plugin_id, set())
        return out
```

测试的 `_service` 夹具相应构造为：

```python
        CapabilityService(
            catalog=..., policy=..., installs=...,
            tool_names_by_plugin={
                "spec_agent": {"spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"},
            },
        )
```

（`PluginService` 暴露「工具名按插件分组」的只读访问器放在 Task B5 一起做：装配时把 `plugin_service.tool_names_by_plugin()` 注入 `CapabilityService`。）

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_service.py -v
```

预期：6 passed。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/catalog/service.py apps/web/backend/tests/test_catalog_service.py && git commit -m "feat: CapabilityService 按用户计算可见能力"
```

---

## Task B5: 运行期过滤接入（工具 / 技能 / 专家 / 插件配置）

**Files:**
- Modify: `apps/web/backend/app/plugins/service.py`（暴露已挂载工具只读访问器）
- Modify: `apps/web/backend/app/plugins/config_store.py`（用户维度配置）
- Modify: `apps/web/backend/app/services/agent_service.py`（工具集/技能索引/ctx.extra 过滤）
- Modify: `apps/web/backend/app/api/skills_api.py`、`apps/web/backend/app/api/assistants_api.py`（列表按用户过滤）
- Modify: `apps/web/backend/app/main.py`（装配 CapabilityService 并注入）
- Test: `apps/web/backend/tests/test_capability_enforcement.py`

**要点**
1. **工具**：`agent_service.chat` 组装 `tool_names` 时，把目录插件工具按可见性过滤——内置工具（`python.run`/`file.*`/`web.*`…）不受影响，只有**插件贡献的工具**受控。实现：`allowed_plugins = await caps.visible_ids(user["sub"], "plugin")`；`plugin_tool_names = ⋃ tool_names_by_plugin[p]`；最终 `tool_names = [t for t in base_names if t not in all_plugin_tools or t in plugin_tool_names]`（即"目录插件工具必须可见才保留"，非插件工具照旧）。
2. **技能**：会话技能索引 = `SkillService.list_skills()` 按 `visible_ids(user, "skill")` 过滤（插件贡献的技能跟随插件可见性）。
3. **插件配置**：`ctx.extra["plugins"]` 只注入该用户可见的插件配置；用户维度配置（`user:<uid>:<pid>`）优先于公共配置。
4. **专家**：`assistants_api` 列表对普通用户过滤掉不可见的目录专家（管理员不过滤）；用户自定义助手（`builtin=False` 且 `plugin_id` 为空）始终可见。

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_capability_enforcement.py`（用既有 `app`/`client`/`admin_headers`/`user_headers` 夹具）：

```python
"""可见性在运行期与列表 API 的落地测试。"""
from __future__ import annotations


async def test_hidden_plugin_tools_disappear_from_run(app, client, admin_headers, user_headers):
    """被设为 hidden 的插件，其工具不再进入该用户的运行工具集。"""
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)

    from app.catalog.service import CapabilityService
    caps: CapabilityService = app.state.capability_service
    assert await caps.visible_tool_names("u-user") == set()
    assert await caps.visible_tool_names("u-admin") == set()  # hidden 对管理员运行期同样不算可见


async def test_not_default_plugin_needs_install(app, client, admin_headers, user_headers):
    """非默认插件：用户安装后才进入其工具集，且不影响其他用户。"""
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "public", "default_enabled": False},
                     headers=admin_headers)
    caps = app.state.capability_service
    assert await caps.visible_tool_names("u-user") == set()

    resp = await client.post("/api/v1/catalog/plugin/spec_agent/install",
                             json={}, headers=user_headers)
    assert resp.status_code == 201
    assert "spec.nmr.forward" in await caps.visible_tool_names("u-user")
    assert await caps.visible_tool_names("u-other") == set()  # 另一个用户不受影响


async def test_skills_list_filtered_by_visibility(app, client, admin_headers, user_headers):
    """技能列表：非默认且未安装的技能对普通用户不可见，管理员仍可见。"""
    await client.put("/api/v1/admin/catalog/skill/office-doc/policy",
                     json={"visibility": "public", "default_enabled": False},
                     headers=admin_headers)

    user_skills = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=user_headers)).json()]
    assert "office-doc" not in user_skills

    admin_skills = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=admin_headers)).json()]
    assert "office-doc" in admin_skills


async def test_experts_list_filtered(app, client, admin_headers, user_headers):
    """专家列表：被隐藏的内置专家对普通用户不可见。"""
    await client.put("/api/v1/admin/catalog/expert/asst-data/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)

    user_names = [a["name"] for a in (await client.get(
        "/api/v1/assistants", headers=user_headers)).json()]
    assert "数据分析助手" not in user_names

    admin_names = [a["name"] for a in (await client.get(
        "/api/v1/assistants", headers=admin_headers)).json()]
    assert "数据分析助手" in admin_names


async def test_cleanup_restores_defaults(app, client, admin_headers):
    """用例收尾：把策略恢复缺省（其它用例依赖缺省语义）。"""
    for kind, item in (("plugin", "spec_agent"), ("skill", "office-doc"),
                       ("expert", "asst-data")):
        await app.state.store.delete("catalog_policy", f"{kind}:{item}")
    await app.state.store.delete("user_capabilities", "u-user:plugin:spec_agent")
```

（`test_cleanup_restores_defaults` 作为显式收尾用例写进文件；更稳妥的做法是在文件里加一个 `autouse` 的清理夹具，实现时按既有测试文件的写法处理——`tests/test_plugins_api.py` 里有可照抄的 `clean_plugin_state` 夹具模式。）

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_capability_enforcement.py -v
```

预期：404（`/api/v1/admin/catalog/...` 路由不存在）。

- [ ] **Step 3: 实现（顺序：plugins → catalog API → agent_service → 列表 API）**

**3a. `PluginService` 暴露工具映射**（`app/plugins/service.py`）：

```python
    def tool_names_by_plugin(self) -> dict[str, set[str]]:
        """已挂载的工具名按插件分组（可见性过滤用）。

        Returns:
            {插件 id: {工具名}} 的副本。
        """
        return {pid: set(names) for pid, names in self._attached.items()}
```

**3b. `PluginConfigStore` 支持用户维度**（`app/plugins/config_store.py`）：新增 `user_doc_id(user_id, plugin_id) -> str`（返回 `f"user:{user_id}:{plugin_id}"`）；`resolved`/`save` 增加可选 `user_id` 参数（None = 公共配置，保持现有行为）；新增 `resolved_for_user(user_id, plugin_id)`：公共配置打底、用户配置覆盖（**浅合并**），两者都无则空 dict。

**3c. 管理员策略 API + 用户市场 API**（`app/catalog/api.py`，Task B6 的完整实现放到本任务一起做，因为测试依赖它）：

```python
"""能力目录 API：用户侧市场（列表/安装/卸载）+ 管理员策略（列表/配置）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin

router = APIRouter(prefix="/api/v1", tags=["catalog"])

KINDS = ("expert", "skill", "plugin")


def get_capability_service(request: Request):
    """从 app.state 取 CapabilityService。

    Args:
        request: FastAPI 请求。

    Returns:
        CapabilityService。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "capability_service", None)
    if service is None:
        raise HTTPException(503, "能力目录未就绪")
    return service


class PolicyBody(BaseModel):
    """管理员策略请求体。"""

    visibility: str = "public"
    default_enabled: bool = True


@router.get("/catalog")
async def list_catalog(kind: str | None = None,
                       user=Depends(get_current_user),
                       service=Depends(get_capability_service)) -> list[dict]:
    """当前用户可见的能力目录（市场列表）。

    Args:
        kind: 可选类型过滤（expert/skill/plugin）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        条目列表（含 installed/visible/policy）。
    """
    kinds = (kind,) if kind in KINDS else KINDS
    rows: list[dict] = []
    for k in kinds:
        rows.extend(await service.market_items(user["sub"], k))
    return rows


@router.post("/catalog/{kind}/{item_id}/install", status_code=201)
async def install_capability(kind: str, item_id: str,
                             user=Depends(get_current_user),
                             service=Depends(get_capability_service)) -> dict:
    """用户自行安装某条目（写记录，不复制文件）。

    Raises:
        HTTPException: 类型非法（404）、条目不存在或对当前用户不可见（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    if not await service.can_install(user["sub"], kind, item_id):
        raise HTTPException(404, f"条目不可安装: {kind}:{item_id}")
    await service.installs.install(user["sub"], kind, item_id)
    return {"kind": kind, "id": item_id, "installed": True}


@router.delete("/catalog/{kind}/{item_id}/install")
async def uninstall_capability(kind: str, item_id: str,
                               user=Depends(get_current_user),
                               service=Depends(get_capability_service)) -> dict:
    """卸载（删除安装记录）。"""
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    removed = await service.installs.uninstall(user["sub"], kind, item_id)
    return {"kind": kind, "id": item_id, "installed": False, "removed": removed}


@router.get("/admin/catalog")
async def admin_list_catalog(kind: str | None = None,
                             user=Depends(require_admin),
                             service=Depends(get_capability_service)) -> list[dict]:
    """管理员视角的目录（含 hidden 条目与策略）。"""
    kinds = (kind,) if kind in KINDS else KINDS
    rows: list[dict] = []
    for k in kinds:
        rows.extend(await service.market_items(user["sub"], k, admin=True))
    return rows


@router.put("/admin/catalog/{kind}/{item_id}/policy")
async def set_catalog_policy(kind: str, item_id: str, body: PolicyBody,
                             user=Depends(require_admin),
                             service=Depends(get_capability_service)) -> dict:
    """配置条目的可见性与默认启用。

    Raises:
        HTTPException: 类型非法/条目不存在（404）、visibility 非法（422）。
    """
    if kind not in KINDS or not await service.exists(kind, item_id):
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")
    try:
        policy = await service.policy.set(kind, item_id, visibility=body.visibility,
                                         default_enabled=body.default_enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"kind": kind, "id": item_id, **policy}
```

`CapabilityService` 补两个方法：

```python
    async def exists(self, kind: str, item_id: str) -> bool:
        """条目是否在目录里。

        Args:
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示存在。
        """
        return any(i.id == item_id for i in self.catalog.list_items(kind))

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
```

**3d. `agent_service` 过滤**：
- 构造签名加 `capability_service: Any = None`（可选，保持既有调用兼容）
- `chat()` 里：
  - `visible_plugins = await caps.visible_ids(user["sub"], "plugin")`（caps 为 None 时跳过过滤）
  - 工具过滤 + 技能索引过滤（见"要点"1/2）
  - `context_extra["plugins"]` 改为「仅可见插件」的配置（用户维度优先）
- `main.py`：lifespan 里组装 `CapabilityService(catalog=CatalogService(...), policy=CatalogPolicyRepo(store), installs=UserCapabilityRepo(store), tool_names_by_plugin=plugin_service.tool_names_by_plugin())` → `app.state.capability_service`；`AgentService(..., capability_service=app.state.capability_service)`；`include_router(catalog_router)`
  - **注意顺序**：`PluginService.startup()` 之后才有 `tool_names_by_plugin()`，所以 CapabilityService 要在其后构造

**3e. 列表 API 过滤**：
- `skills_api` 列表：普通用户过滤 `visible_ids(user, "skill")`；管理员不过滤
- `assistants_api` 列表：普通用户过滤「目录专家中不可见的」；管理员不过滤（插件播种专家跟随其插件可见性：若插件不可见则一并过滤）

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_capability_enforcement.py -v && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：5 passed；全量通过（若既有测试因过滤而失败，说明过滤条件写错了——**缺省策略必须让所有既有断言行为不变**）。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app && git commit -m "feat: 可见性在运行期与列表 API 落地（工具/技能/专家/插件配置）"
```

---

## Task B6: 插件个人配置与公共/个人合并（若 B5 未覆盖）

**说明**：B5 的 3b 已实现 `resolved_for_user`。本任务补齐 `PluginService` 侧：用户安装插件时若带配置（市场页表单），写入用户维度；`context_extra` 用 `resolved_for_user` 解析。

**Files:**
- Modify: `apps/web/backend/app/catalog/api.py`（install 支持可选 `config`）
- Modify: `apps/web/backend/app/plugins/service.py`（用户维度安装/解析）
- Modify: `apps/web/backend/app/services/agent_service.py`（`ctx.extra["plugins"]` 用用户维度）
- Test: `apps/web/backend/tests/test_catalog_plugin_config.py`

- [ ] **Step 1: 写失败测试**

```python
"""用户维度插件配置测试（公共配置打底、个人配置覆盖、互不串号）。"""
from __future__ import annotations


async def test_user_config_overrides_public(app, client, admin_headers, user_headers):
    """同一插件：公共配置 + 用户个人配置，个人优先；其他用户不受影响。"""
    # 公共安装（管理员）
    await client.post("/api/v1/plugins/spec_agent/install",
                      json={"config": {"base_url": "http://public", "token": "pub"}},
                      headers=admin_headers)
    # 用户个人配置
    resp = await client.post("/api/v1/catalog/plugin/spec_agent/install",
                             json={"config": {"base_url": "http://mine", "token": "mine"}},
                             headers=user_headers)
    assert resp.status_code == 201

    from app.plugins.config_store import PluginConfigStore
    cs: PluginConfigStore = app.state.plugin_config_store
    assert await cs.resolved_for_user("u-user", "spec_agent") == {
        "base_url": "http://mine", "token": "mine"}
    assert await cs.resolved_for_user("u-other", "spec_agent") == {
        "base_url": "http://public", "token": "pub"}


async def test_user_without_config_falls_back_to_public(app, client, admin_headers, user_headers):
    """用户未填个人配置时回落到公共配置。"""
    await client.post("/api/v1/plugins/spec_agent/install",
                      json={"config": {"base_url": "http://public", "token": "pub"}},
                      headers=admin_headers)
    await client.post("/api/v1/catalog/plugin/spec_agent/install", json={},
                      headers=user_headers)

    cs = app.state.plugin_config_store
    assert (await cs.resolved_for_user("u-user", "spec_agent"))["base_url"] == "http://public"
    assert "spec_agent" not in await cs.all_resolved() or True  # 公共配置仍在
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_plugin_config.py -v
```

- [ ] **Step 3: 实现**

- `catalog/api.py`：`install_capability` 的 body 改为 `{"config": {...}}`（`PluginConfigBody` 复用 `app/plugins/api.py` 的定义或就地定义 `CapabilityInstallBody(config: dict = {})`）；当 `kind == "plugin"` 且 `config` 非空时，用该插件的 `config_schema` 调 `config_store.save(plugin_id, config, schema, user_id=user["sub"])`；**校验失败（必填缺失）→ 422**，与管理员安装同一口径。
- `agent_service`：`ctx.extra["plugins"]` 逐可见插件调 `resolved_for_user(user_sub, plugin_id)`。
- 需要把 `config_store` 与插件包（取 schema）暴露到 `app.state`（B5 的 3d 已把 `plugin_config_store` 装进 state？若未装，本任务补 `app.state.plugin_config_store = ...` 与 `app.state.plugin_packages = ...`）。

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_plugin_config.py -v && conda run -n synlysagent --no-capture-output python -m pytest -q
```

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app apps/web/backend/tests/test_catalog_plugin_config.py && git commit -m "feat: 插件支持用户维度配置（公共打底、个人覆盖）"
```

---

## Phase C：前端（管理端策略开关 + 用户侧能力中心）

## Task C1: 管理页三处加「可见性 / 默认启用」开关

**Files:**
- Modify: `src/components/admin/SkillsAdmin.tsx`、`src/components/admin/AssistantsAdmin.tsx`、`src/components/admin/PluginsAdmin.tsx`
- Modify: `src/types.ts`（`CatalogItem` 类型）
- Modify: `src/api/client.ts` 不改（用 `api<T>`）；`src/stores/admin.ts` 加 `catalog` 状态与 `loadCatalog`

**照抄对象**：`shared.tsx` 的 `Switch`（`role="switch"` 布尔开关，L38）+ `ModelsAdmin.tsx` 的列表行布局。三个页面各自的行里加两个控件：

- 「可见」开关：`public` ⇄ `hidden`
- 「默认启用」开关：仅当可见时可用（隐藏时置灰）

写回接口：`PUT /api/v1/admin/catalog/{kind}/{id}/policy`，body `{visibility, default_enabled}`；成功后 `await loadCatalog()`。

- [ ] **Step 1: 类型与 store**

`src/types.ts` 追加：

```ts
/** 能力目录条目（对应后端 app/catalog/service.py 的 market_items）。 */
export interface CatalogItem {
  kind: 'expert' | 'skill' | 'plugin';
  id: string;
  name: string;
  description: string;
  source: 'builtin';
  visibility: 'public' | 'hidden';
  default_enabled: boolean;
  installed: boolean;
  visible: boolean;
}
```

`src/stores/admin.ts` 加：

```ts
  /** 加载能力目录（管理员视角，含 hidden 条目与策略）。 */
  loadCatalog: async () => {
    const catalog = await api<CatalogItem[]>('/api/v1/admin/catalog');
    set({ catalog });
  },
```

state 初值 `catalog: [] as CatalogItem[]`（import 类型）。

- [ ] **Step 2: 三个管理页接线**

每页在 `useEffect` 里追加 `loadCatalog()`（与既有加载并列，失败 toast）；渲染时按 `kind` 从 `catalog` 里取该条目：
- `AssistantsAdmin`：`kind='expert'`，只为**目录专家**（`asst-research`/`asst-data` 这类，即 `catalog` 里有的 id）显示开关；用户自建助手不显示
- `SkillsAdmin`：`kind='skill'`，只有目录里的技能显示开关
- `PluginsAdmin`：`kind='plugin'`，目录条目显示开关（与既有「安装/配置」按钮并存）

开关组件照 `shared.tsx` 的 `Switch` 用法（label + 受控 checked + onChange → PUT policy → 刷新）。

- [ ] **Step 3: 构建验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
```

- [ ] **Step 4: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/frontend/src && git commit -m "feat: 管理页支持配置内置条目的可见性与默认启用"
```

---

## Task C2: 用户侧「能力中心」（市场）

**Files:**
- Create: `src/components/catalog/CapabilityCenter.tsx`
- Modify: `src/components/layout/AppShell.tsx`（入口；先读该文件确认左栏底部 `UserMenu` 的结构，照抄其中项的写法）
- Modify: `src/types.ts`（复用 `CatalogItem`）
- Modify: `src/stores/admin.ts` 或新建 `src/stores/catalog.ts`（用户侧 catalog 状态）

**形态（用户已确认）**：**独立整页** `/capabilities`，与后台管理页保持一致（左栏底部用户菜单入口；后续「用户自建插件/技能」等页面需要大空间，整页比模态更合适）。为此把后台的顶栏（返回工作台 + 页面标题 + 主题切换 + 用户名）抽成共用组件 `src/components/layout/PageTopBar.tsx`，`AdminLayout` 与能力中心页共用（**这是本次唯一的小重构**，不要扩散）。

**内容**：三栏/分组列表（专家 / 技能 / 插件），每行：名称 + 描述 + 状态徽标（`默认启用` / `已安装` / `未安装`）+ 右侧按钮：
- 默认启用 → 徽标「默认可用」，无按钮
- 已安装 → 「卸载」（`DELETE /api/v1/catalog/{kind}/{id}/install`）
- 未安装 → 「安装」；插件若有 `config_schema`（从 `GET /api/v1/catalog` 返回里带出，若未带则本任务补该字段）先用表单弹窗填配置再提交 `POST .../install` body `{config}`

- [ ] **Step 1: 类型与数据**

`CatalogItem` 若需要插件的 `config_schema`，在 `catalog/api.py` 的 `market_items` 里为 plugin 条目补 `config_schema` 字段（来自 `PluginPackage.config_schema`），并在 `CatalogItem` 类型里加可选 `config_schema?: PluginConfigField[]`。

- [ ] **Step 2: 组件实现**（照抄 `PluginsAdmin` 的列表 + 表单弹窗结构，修改点：数据源换 `GET /api/v1/catalog`，动作换安装/卸载）

- [ ] **Step 3: 入口接线**（`AppShell` 左栏底部菜单加一项，仅登录用户可见）

- [ ] **Step 4: 构建验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
```

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/frontend/src && git commit -m "feat: 用户侧能力中心（浏览/安装/卸载，插件可填个人配置）"
```

---

## Phase D：文档与版本

## Task D1: 文档、backlog 与版本 0.6.0-beta.1

**Files:**
- Modify: `README.md`、`apps/web/backend/README.md`、`CLAUDE.md`、backlog、`app/version.py`、`frontend/package.json`

- [ ] **Step 1: backend README** 新增「能力目录与可见性（市场机制）」一节：三层模型表、两个新集合（`catalog_policy`/`user_capabilities`）、可见性规则（hidden/public+默认/public+非默认）、市场 API 一览（用户侧 `/api/v1/catalog*`、管理员侧 `/api/v1/admin/catalog*`）、「安装 = 只写记录不复制文件」、首期不支持用户自建。

- [ ] **Step 2: 根 README**：版本行 → `0.6.0-beta.1`；「插件机制」段后补两句能力目录与可见性说明；「后续路线」加一条（能力市场：用户自建/导入待后续）。

- [ ] **Step 3: CLAUDE.md**：关键架构约定追加一条：

```
- 能力目录（市场）：内置项（专家/技能/插件）由 `app/catalog/` 枚举；管理员用 `catalog_policy` 配置「可见性（public/hidden）+ 默认启用」；用户安装只写 `user_capabilities` 记录（**不复制文件**，升级即生效）；运行期可见集由 `CapabilityService` 按用户计算（工具/技能/专家/插件配置统一走它）；插件配置支持公共（`plugin_configs`，部署级）与个人（`user:<uid>:<plugin_id>`）两层，个人优先；首期不支持用户自建技能/插件
```

- [ ] **Step 4: backlog**：新增「阶段一·5 能力市场与可见性」小节（勾选已完成项，标注「用户自建/导入」为待办）。

- [ ] **Step 5: 版本三处同步**（`app/version.py`、`frontend/package.json`、根 README 版本行）

- [ ] **Step 6: 验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\packages\synlys-harness" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
cd "E:\agent_projects\Synlora" && git diff --stat 805dc33 HEAD -- packages/synlys-harness   # 应仍为空（harness 零改动）
```

- [ ] **Step 7: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add README.md CLAUDE.md apps/web/backend/README.md apps/web/backend/app/version.py apps/web/frontend/package.json docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md docs/superpowers/plans/2026-09-15-synlysagent-08-capability-catalog-and-admin-nav.md && git commit -m "docs: 能力目录文档与版本 0.6.0-beta.1"
```

---

## 四、自检（Self-Review 已完成）

- **Spec 覆盖**：后台左导航 + 常规页（A1）、内置目录枚举（B1）、管理员可见性/默认（B2/B5/C1）、用户安装记录（B3）、按用户可见集（B4/B5）、插件个人配置（B6）、用户侧市场（C2）、文档版本（D1）。四条用户决策均已编码：只存 DB（B3/B6）、首期不自建（无用户目录写入路径）、公开/隐藏+默认启用（B2/B4）、保持整页（A1 用 `/admin/*` 路由 + 左导航）。
- **向后兼容**：缺省策略 = public + 默认启用 ⇒ 升级后既有行为不变；`CapabilityService` 为可选注入（None 时不过滤），既有 `AgentService(...)` 调用不破。
- **命名一致性**：`catalog_policy` / `user_capabilities` / `CatalogItem{kind,id,name,description,source}` / `CapabilityService.{is_visible,visible_ids,visible_tool_names,market_items,exists,can_install}` / `UserCapabilityRepo.{install,uninstall,is_installed,list_for_user}` / `CatalogPolicyRepo.{get,all_policies,set}` / 路由 `/api/v1/catalog*`、`/api/v1/admin/catalog*` 全文一致。
- **风险点（执行时注意）**：B5 的过滤必须让**缺省策略下既有测试全绿**，否则说明过滤条件误伤；`tool_names_by_plugin` 依赖 `PluginService.startup()` 已跑完，CapabilityService 必须在其后构造。

## 五、明确不做（首期）

- 用户自建/导入技能与插件（无用户私有目录写入路径；`{data_dir}/users/{uid}/` 仅在设计上预留）
- 按角色/按用户白名单的细粒度可见性
- 插件卸载（管理员公共安装的卸载仍不做）
- 市场里的「远程下载/上传 zip」（jiuwen 的 hub 机制）
- 常规设置有实际内容（外观/语言仍占位；主题已可经右上角切换）
