# 能力中心改版实施计划（左导航分类 + 卡片市场 + 详情页）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把用户侧能力中心 `/capabilities` 从「顶部页签 + 行列表」改造成「左导航分类 + 卡片式市场/我的 + 路由级详情页」，并补后端能力详情接口。

**Architecture:** 后端新增一个只读接口 `GET /api/v1/me/capabilities/{kind}/{item_id}`，按「用户自建 → catalog 内置（可见性判定）」解析，返回条目全量字段与 `origin` 四态；前端新增两条路由（列表 / 详情），列表页左侧类型导航照 `AdminLayout`、右侧卡片网格照 jiuwen `PageCard` + `card-grid-auto`，详情页照 jiuwen `DefinitionDetailPage` 结构。

**Tech Stack:** Python 3.12 / FastAPI / pytest（后端）；React 19 + TypeScript + Tailwind 4 + Zustand（前端）。

**参考规格：** `docs/superpowers/specs/2026-09-16-synlysagent-capability-center-redesign-design.md`

**参考实现（照抄对象，动手前先读）：**
- 卡片与网格：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\components\ui\PageCard\PageCard.css`、`src/index.css` 的 `.card-grid-auto`
- 列表页工具行：`...\components\ConnectorMarket\MarketplacePage.tsx`
- 详情页：`...\components\AgentManagementPanel\DefinitionDetailPage.tsx`
- 左导航：本项目 `apps/web/frontend/src/components/admin/AdminLayout.tsx`

---

## 文件结构

**后端（`apps/web/backend/`）**

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/catalog/items.py` | 修改 | `CatalogService` 增加 `experts` / `skills` 只读 property（与既有 `plugins` 对称） |
| `app/catalog/api.py` | 修改 | 新增 `GET /me/capabilities/{kind}/{item_id}`；两个自建解析 helper |
| `tests/test_capability_detail.py` | 新建 | 详情接口测试（可见性、自建隔离、字段形状） |

**前端（`apps/web/frontend/src/`）**

| 文件 | 动作 | 职责 |
|---|---|---|
| `types.ts` | 修改 | 新增 `CapabilityDetail` 类型 |
| `routing/route.ts` | 修改 | 路由联合类型加 `capabilityKind` / `capability-detail` |
| `App.tsx` | 修改 | 把 route 传给 `CapabilityCenter` |
| `stores/catalog.ts` | 修改 | 新增 `loadDetail(kind, id)` |
| `components/catalog/CapabilityCard.tsx` | 新建 | 卡片（照 `PageCard` 实现） |
| `components/catalog/CapabilityModals.tsx` | 新建 | 从 MinePanel / CapabilityCenter 抽出的 `SkillModal` / `ExpertModal` / `PluginInstallModal` |
| `components/catalog/CapabilityCenter.tsx` | 重写 | 外壳（顶栏 + 左导航）+ 列表页（页签 + 搜索 + 网格） |
| `components/catalog/CapabilityDetail.tsx` | 新建 | 详情页 |
| `components/catalog/MinePanel.tsx` | 修改 | 行列表改卡片网格；改用抽出的 Modal |

**版本与文档**：`app/version.py`、`apps/web/frontend/package.json`、`README.md`

---

## Task 1: 后端能力详情接口

**Files:**
- Modify: `apps/web/backend/app/catalog/items.py`（在 `plugins` property 后插入）
- Modify: `apps/web/backend/app/catalog/api.py`（文件末尾追加）
- Test: `apps/web/backend/tests/test_capability_detail.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `apps/web/backend/tests/test_capability_detail.py`：

```python
"""能力详情接口测试（GET /api/v1/me/capabilities/{kind}/{item_id}）。"""
from __future__ import annotations


async def test_builtin_expert_detail(client, user_headers):
    """内置专家详情：返回 system_prompt / avatar / tool_whitelist，未安装时 origin='market'。"""
    res = await client.get("/api/v1/me/capabilities/expert/asst-data", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "expert"
    assert body["id"] == "asst-data"
    assert body["origin"] == "market"
    assert body["avatar"] == "📊"
    assert "数据分析助手" in body["system_prompt"]
    assert "python.run" in body["tool_whitelist"]


async def test_builtin_skill_detail(client, user_headers):
    """内置技能详情：返回 SKILL.md 正文。"""
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "skill"
    assert body["id"] == "office-doc"
    assert body["content"].strip() != ""


async def test_builtin_plugin_detail(client, user_headers):
    """内置插件详情：返回配置字段声明与附属清单。"""
    res = await client.get("/api/v1/me/capabilities/plugin/spec_agent", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "plugin"
    assert isinstance(body["config_schema"], list)
    assert isinstance(body["tools"], list)
    assert isinstance(body["experts"], list)
    assert isinstance(body["skills"], list)


async def test_installed_item_origin_installed(client, user_headers):
    """已安装条目 origin='installed'，enabled 反映安装态。"""
    install = await client.put(
        "/api/v1/me/capabilities/skill/office-doc",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "installed"
    assert body["enabled"] is True


async def test_my_skill_detail_origin_mine(client, user_headers):
    """自建技能详情：origin='mine' 且带正文。"""
    created = await client.post("/api/v1/me/skills", headers=user_headers, json={
        "name": "my-detail-skill",
        "description": "详情测试技能",
        "content": "# 我的技能\n\n正文若干。",
    })
    assert created.status_code == 201
    res = await client.get("/api/v1/me/capabilities/skill/my-detail-skill", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "mine"
    assert body["description"] == "详情测试技能"
    assert "正文若干" in body["content"]


async def test_my_expert_detail_origin_mine(client, user_headers):
    """自建专家详情：origin='mine' 且带回 system_prompt（编辑回填依赖此字段）。"""
    created = await client.post("/api/v1/me/experts", headers=user_headers, json={
        "name": "我的专家",
        "avatar": "🚀",
        "description": "自建",
        "system_prompt": "你是测试专家。",
        "tool_whitelist": ["python.run"],
    })
    assert created.status_code == 201
    expert_id = created.json()["id"]
    res = await client.get(f"/api/v1/me/capabilities/expert/{expert_id}", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "mine"
    assert body["system_prompt"] == "你是测试专家。"
    assert body["tool_whitelist"] == ["python.run"]


async def test_mine_isolation(client, user_headers, admin_headers):
    """他人自建技能对本用户 404（多租户隔离）。"""
    await client.post("/api/v1/me/skills", headers=user_headers, json={
        "name": "my-private-skill", "description": "私密", "content": "# x",
    })
    res = await client.get("/api/v1/me/capabilities/skill/my-private-skill", headers=admin_headers)
    assert res.status_code == 404


async def test_hidden_item_not_found(client, admin_headers, user_headers):
    """hidden 条目对普通用户 404（不泄露存在性）。"""
    put = await client.put(
        "/api/v1/admin/catalog/skill/pdf-extraction/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": False},
    )
    assert put.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/pdf-extraction", headers=user_headers)
    assert res.status_code == 404


async def test_unknown_kind_and_id_not_found(client, user_headers):
    """未知类型与未知条目一律 404。"""
    assert (await client.get(
        "/api/v1/me/capabilities/nope/x", headers=user_headers)).status_code == 404
    assert (await client.get(
        "/api/v1/me/capabilities/skill/no-such-skill", headers=user_headers)).status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_capability_detail.py -v
```

Expected: 全部 FAIL（404 Not Found —— 端点还没实现；`test_builtin_*` 会因路由不存在而 404）。

- [ ] **Step 3: `CatalogService` 暴露 experts / skills 包映射**

在 `apps/web/backend/app/catalog/items.py` 的 `plugins` property 之后插入：

```python
    @property
    def experts(self) -> dict[str, "ExpertPackage"]:
        """扫描到的专家包（{id: ExpertPackage}）。

        Returns:
            专家包映射（只读用途；调用方不得就地修改）。
        """
        return self._index.experts

    @property
    def skills(self) -> dict[str, "SkillPackage"]:
        """扫描到的技能包（{name: SkillPackage}）。

        Returns:
            技能包映射（只读用途；调用方不得就地修改）。
        """
        return self._index.skills
```

- [ ] **Step 4: 实现详情端点的两个自建 helper**

在 `apps/web/backend/app/catalog/api.py` 的 `_public_ready_keys` 函数之后插入：

```python
async def _own_skill_detail(request: Request, user_id: str, name: str) -> dict | None:
    """用户自建技能详情（非自建返回 None）。

    Args:
        request: FastAPI 请求（取技能服务）。
        user_id: 用户 sub。
        name: 技能名。

    Returns:
        详情字典（origin='mine'）；该用户没有此名自建技能时 None。
    """
    svc = getattr(request.app.state, "skill_service", None)
    if svc is None:
        return None
    own = {s["name"]: s for s in svc.list_own_skills(user_id)}
    row = own.get(name)
    if row is None:
        return None
    return {
        "kind": "skill",
        "id": name,
        "name": name,
        "description": str(row.get("description") or ""),
        "origin": "mine",
        "enabled": True,
        "content": svc.read_body(name, user_id) or "",
    }


async def _own_expert_detail(request: Request, user_id: str, expert_id: str) -> dict | None:
    """用户自建专家详情（非自建返回 None）。

    Args:
        request: FastAPI 请求（取专家服务）。
        user_id: 用户 sub。
        expert_id: 专家 id。

    Returns:
        详情字典（origin='mine'）；该用户没有此专家时 None。
    """
    svc = getattr(request.app.state, "expert_service", None)
    if svc is None:
        return None
    own = await svc.get_own(user_id, expert_id)
    if own is None:
        return None
    return {
        "kind": "expert",
        "id": expert_id,
        "name": str(own.get("name") or expert_id),
        "description": str(own.get("description") or ""),
        "origin": "mine",
        "enabled": True,
        "avatar": str(own.get("avatar") or ""),
        "system_prompt": str(own.get("system_prompt") or ""),
        "tool_whitelist": [str(t) for t in (own.get("tool_whitelist") or [])],
    }
```

- [ ] **Step 5: 实现详情端点**

在 `apps/web/backend/app/catalog/api.py` 文件末尾追加：

```python
@router.get("/me/capabilities/{kind}/{item_id}")
async def capability_detail(kind: str, item_id: str, request: Request,
                            user=Depends(get_current_user),
                            service=Depends(get_capability_service)) -> dict:
    """当前用户视角下某能力的详情（用户侧能力中心详情页）。

    解析顺序：用户自建（技能/专家，落 `users/<uid>/`）优先 → catalog 内置条目。
    内置条目按市场同一可见性口径判定：hidden 一律 404（不泄露存在性），
    未安装条目也可读（否则用户无法在安装前判断内容）。

    `origin` 四态供前端决定渲染与动作：`mine` 自建（可编辑/删除）、
    `installed` 已安装（可启停/卸载）、`builtin` 内置（普通用户只读）、
    `market` 市场可见未安装（可安装）。

    内容一律从 `catalog_roots()` 链上读（repo 的 `catalog/` +
    数据目录 `public/catalog/` 同名覆盖），不在数据库另存副本——服务初始
    状态即与代码仓库一致。本端点只读，管理员编辑走既有管理后台。

    Args:
        kind: 条目类型（expert/skill/plugin）。
        item_id: 条目 id（技能 = 技能名）。
        request: FastAPI 请求（取技能/专家/插件服务）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        详情字典：公共字段 + origin/enabled + 按 kind 的特有字段。

    Raises:
        HTTPException: 类型非法、条目不存在或不可见（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    user_id = user["sub"]

    # 自建优先：用户自己创建的内容不在 catalog 里
    if kind == "skill":
        own = await _own_skill_detail(request, user_id, item_id)
        if own is not None:
            return own
    elif kind == "expert":
        own = await _own_expert_detail(request, user_id, item_id)
        if own is not None:
            return own

    item = next((i for i in service.catalog.list_items(kind) if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")
    pol = await service.policy.get(kind, item_id)
    if pol["visibility"] == "hidden":
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")

    installed = await service.installs.is_installed(user_id, kind, item_id)
    enabled = await service.installs.is_enabled(user_id, kind, item_id)
    if pol["default_enabled"]:
        origin = "builtin"
    elif installed:
        origin = "installed"
    else:
        origin = "market"
    row: dict[str, Any] = {
        "kind": kind,
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "origin": origin,
        # 内置条目恒为可用态（与 market_items 的 visible 口径一致）
        "enabled": bool(pol["default_enabled"]) or enabled,
    }

    if kind == "expert":
        pkg = service.catalog.experts.get(item_id)
        if pkg is not None:
            row["avatar"] = pkg.avatar
            row["system_prompt"] = pkg.system_prompt
            row["tool_whitelist"] = list(pkg.tool_whitelist)
    elif kind == "skill":
        svc = getattr(request.app.state, "skill_service", None)
        row["content"] = (svc.read_body(item_id, user_id) if svc is not None else None) or ""
    elif kind == "plugin":
        row["config_ready_keys"] = sorted(await _public_ready_keys(request, item_id))
        plugin_service = getattr(request.app.state, "plugin_service", None)
        if plugin_service is not None:
            state = plugin_service.state(item_id)
            row["config_schema"] = state["config_schema"]
            row["skills"] = state["skills"]
            row["experts"] = state["experts"]
            row["tools"] = state["tools"]
    return row
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_capability_detail.py -v
```

Expected: 9 passed。

- [ ] **Step 7: 跑全量后端测试确认无回归**

```bash
cd apps/web/backend && conda run -n synlysagent python -m pytest -q
```

Expected: 全绿（若 `test_hidden_item_not_found` 之外的用例因策略库在测试间共享而受影响，检查是否有其他测试依赖 `pdf-extraction` 的 public 状态）。

- [ ] **Step 8: 提交**

```bash
git add apps/web/backend/app/catalog/items.py apps/web/backend/app/catalog/api.py apps/web/backend/tests/test_capability_detail.py
git commit -m "新增能力详情接口 GET /me/capabilities/{kind}/{item_id}"
```

---

## Task 2: 前端类型与路由扩展

**Files:**
- Modify: `apps/web/frontend/src/types.ts`
- Modify: `apps/web/frontend/src/routing/route.ts`
- Modify: `apps/web/frontend/src/components/layout/AppShell.tsx`（用户菜单入口构造路由对象，必须同步）

- [ ] **Step 1: 加 `CapabilityDetail` 类型**

在 `apps/web/frontend/src/types.ts` 的 `MyCapability` 之后插入：

```ts
/** 能力详情（对应后端 GET /me/capabilities/{kind}/{item_id}）。 */
export interface CapabilityDetail {
  kind: 'expert' | 'skill' | 'plugin'
  id: string
  name: string
  description: string
  /** 来源四态：自建 / 已装 / 内置 / 市场可见未装。 */
  origin: 'mine' | 'installed' | 'builtin' | 'market'
  enabled: boolean
  /** 已安装后被管理员下架（仅该情形出现此键）：详情可读、只能卸载。 */
  revoked?: boolean
  /** 仅专家：头像 emoji。 */
  avatar?: string
  /** 仅专家：人设提示词。 */
  system_prompt?: string
  /** 仅专家：可用工具名（空 = 全部内置工具）。 */
  tool_whitelist?: string[]
  /** 仅技能：SKILL.md 正文。 */
  content?: string
  /** 仅插件：配置字段声明。 */
  config_schema?: PluginConfigField[]
  /** 仅插件：管理员公共配置已就绪的字段名。 */
  config_ready_keys?: string[]
  /** 仅插件：自带技能清单。 */
  skills?: Array<{ name: string; description: string }>
  /** 仅插件：播种的专家清单。 */
  experts?: Array<{ id: string; name: string }>
  /** 仅插件：注册的工具名。 */
  tools?: string[]
}
```

- [ ] **Step 2: 扩展路由表**

把 `apps/web/frontend/src/routing/route.ts` 中的 `AppRoute` 类型与两个函数整体替换为：

```ts
/** 能力类型（能力中心左导航）。 */
export type CapabilityKind = 'expert' | 'skill' | 'plugin'

/** 应用路由（判别联合，kind 即分支）。 */
export type AppRoute =
  | { kind: 'chat-new' }
  | { kind: 'chat-session'; sessionId: string }
  | { kind: 'admin'; tab: AdminTab }
  | { kind: 'capabilities'; capabilityKind: CapabilityKind }
  | { kind: 'capability-detail'; capabilityKind: CapabilityKind; itemId: string }
  | { kind: 'not-found'; pathname: string }

/** 能力类型白名单（解析与构造共用，避免两处口径漂移）。 */
const CAPABILITY_KINDS = 'expert|skill|plugin'

/**
 * 解析 pathname 为应用路由。
 *
 * Args:
 *     pathname: location.pathname（尾斜杠容忍，多个也只去一层）。
 *
 * Returns:
 *     对应的 AppRoute；无法识别的路径返回 not-found（保留原路径）。
 */
export function parseAppRoute(pathname: string): AppRoute {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (path === '/' || path === '/chat' || path === '/chat/new') return { kind: 'chat-new' }
  const chatMatch = path.match(/^\/chat\/([^/]+)$/)
  if (chatMatch) return { kind: 'chat-session', sessionId: decodeURIComponent(chatMatch[1]) }
  const adminMatch = path.match(/^\/admin\/(general|models|assistants|skills|plugins)$/)
  if (adminMatch) return { kind: 'admin', tab: adminMatch[1] as AdminTab }
  // /capabilities 不带类型时按专家渲染（不重写 URL，旧链接继续可用）
  if (path === '/capabilities') return { kind: 'capabilities', capabilityKind: 'expert' }
  const detailMatch = path.match(new RegExp(`^/capabilities/(${CAPABILITY_KINDS})/([^/]+)$`))
  if (detailMatch) {
    return {
      kind: 'capability-detail',
      capabilityKind: detailMatch[1] as CapabilityKind,
      itemId: decodeURIComponent(detailMatch[2]),
    }
  }
  const listMatch = path.match(new RegExp(`^/capabilities/(${CAPABILITY_KINDS})$`))
  if (listMatch) return { kind: 'capabilities', capabilityKind: listMatch[1] as CapabilityKind }
  return { kind: 'not-found', pathname }
}

/**
 * 构造路由对应的路径（navigate 写入 history 时用）。
 *
 * Args:
 *     route: 目标路由。
 *
 * Returns:
 *     可放入地址栏的路径字符串。
 */
export function appRoutePath(route: AppRoute): string {
  if (route.kind === 'chat-new') return '/chat/new'
  if (route.kind === 'chat-session') return `/chat/${encodeURIComponent(route.sessionId)}`
  if (route.kind === 'admin') return `/admin/${route.tab}`
  if (route.kind === 'capabilities') return `/capabilities/${route.capabilityKind}`
  if (route.kind === 'capability-detail') {
    return `/capabilities/${route.capabilityKind}/${encodeURIComponent(route.itemId)}`
  }
  return route.pathname
}
```

同时更新文件头注释里 `/capabilities` 那一行：

```
 * - `/capabilities[/<kind>[/<id>]]` → 用户侧能力中心（列表 / 详情，任意登录用户）
```

- [ ] **Step 3: 同步路由构造点（AppShell 用户菜单）**

`apps/web/frontend/src/components/layout/AppShell.tsx` 的 `UserMenu` 里有唯一一处**构造**该路由对象的地方，类型加字段后会编译失败：

```tsx
navigate({ kind: 'capabilities' })
```

改为：

```tsx
navigate({ kind: 'capabilities', capabilityKind: 'expert' })
```

（行为等价：裸 `/capabilities` 本就由 `parseAppRoute` 解析为专家。）
同文件里的兄弟 `href="/capabilities"` 不必改——它经解析器解析，仍然正确。

- [ ] **Step 4: 类型检查（应保持绿）**

```bash
cd apps/web/frontend && npx tsc -b
```

Expected: 通过。`App.tsx` 现有的 `route.kind === 'capabilities'` 判断在新联合类型下依然合法（只是暂不处理新详情路由），所以本步不破坏构建。**`App.tsx` 的改动放到 Task 6**（与 `CapabilityCenter` 重写同步，避免留下 props 不匹配的中间态）。

- [ ] **Step 5: 提交（与 Task 3 一起，见 Task 3 Step 4）**

---

## Task 3: catalog store 增加详情方法

**Files:**
- Modify: `apps/web/frontend/src/stores/catalog.ts`

- [ ] **Step 1: 加 import 与方法签名**

在 `apps/web/frontend/src/stores/catalog.ts` 顶部 import 改为：

```ts
import type { CapabilityDetail, CatalogItem } from '@/types'
```

在 `interface CatalogState` 的 `setEnabled` 之后加：

```ts
  /** 拉取单个能力详情（详情页与编辑回填共用；不缓存，每次实时读）。 */
  loadDetail: (kind: CatalogItem['kind'], itemId: string) => Promise<CapabilityDetail>
```

- [ ] **Step 2: 实现**

在 store 的 `setEnabled` 实现之后追加：

```ts
  loadDetail: async (kind, itemId) => {
    // 路径与 PUT 同源（/me/capabilities/{kind}/{item_id}），语义为「我视角下的这个能力」；
    // 后端按「自建 → catalog 可见性」解析，前端不需要知道条目来源
    return api<CapabilityDetail>(
      `/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`,
    )
  },
```

- [ ] **Step 3: 构建验证**

```bash
cd apps/web/frontend && npx tsc -b
```

Expected: 通过（纯新增类型与 store 方法，不破坏既有调用）。

- [ ] **Step 4: 提交**

```bash
git add apps/web/frontend/src/types.ts apps/web/frontend/src/routing/route.ts apps/web/frontend/src/stores/catalog.ts apps/web/frontend/src/components/layout/AppShell.tsx
git commit -m "前端能力中心：加详情类型、列表/详情路由与详情拉取方法"
```

---

## Task 4: 抽出能力编辑弹窗（并修专家回填 bug）

**Files:**
- Create: `apps/web/frontend/src/components/catalog/CapabilityModals.tsx`
- Modify: `apps/web/frontend/src/components/catalog/MinePanel.tsx`（删除内联 Modal，改 import）

- [ ] **Step 1: 新建 `CapabilityModals.tsx`**

```tsx
/**
 * 能力编辑/安装弹窗集（技能、专家、插件安装）。
 *
 * 从 MinePanel / CapabilityCenter 抽出，供列表页与详情页共用。
 * `ExpertModal` 编辑态会先拉一次详情把 `system_prompt` / `tool_whitelist` /
 * `avatar` 回填——列表接口不回传这些字段，此前打开编辑框即空白、一保存就把
 * 原人设提示词覆盖掉。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft, type SkillDraft } from '@/stores/myCapabilities'
import type { CatalogItem, MyCapability } from '@/types'

/** 技能编辑模态（自建：新建与编辑共用）。 */
export function SkillModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createSkill = useMyCapabilitiesStore((s) => s.createSkill)
  const updateSkill = useMyCapabilitiesStore((s) => s.updateSkill)
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(editing)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 编辑态先拉正文（列表只有名称与描述）
  useEffect(() => {
    if (initial === null) return
    let alive = true
    loadDetail('skill', initial.id)
      .then((detail) => {
        if (!alive) return
        setDescription(detail.description)
        setContent(detail.content ?? '')
      })
      .catch((err) => {
        if (alive) setError(errorText(err))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [initial, loadDetail])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const draft: SkillDraft = { name: name.trim(), description: description.trim(), content }
      if (editing) await updateSkill(initial.id, { description: draft.description, content: draft.content })
      else await createSkill(draft)
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? `编辑技能 ${initial?.name}` : '新建技能'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {!editing && (
          <label className={labelClass}>
            技能名（kebab-case）
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-skill"
              required
              autoFocus
              className={inputClass}
            />
          </label>
        )}
        <label className={labelClass}>
          描述（做什么 + 何时用）
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            autoFocus={editing}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          正文（Markdown，不含 frontmatter）
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            required
            disabled={loading}
            className={`${inputClass} resize-y font-mono`}
          />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving || loading} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 专家编辑模态（自建：新建与编辑共用）。 */
export function ExpertModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createExpert = useMyCapabilitiesStore((s) => s.createExpert)
  const updateExpert = useMyCapabilitiesStore((s) => s.updateExpert)
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [avatar, setAvatar] = useState('')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState('')
  const [loading, setLoading] = useState(editing)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 编辑态先拉详情回填（人设提示词与工具白名单列表接口不返回）
  useEffect(() => {
    if (initial === null) return
    let alive = true
    loadDetail('expert', initial.id)
      .then((detail) => {
        if (!alive) return
        setAvatar(detail.avatar ?? '')
        setDescription(detail.description)
        setSystemPrompt(detail.system_prompt ?? '')
        setTools((detail.tool_whitelist ?? []).join(', '))
      })
      .catch((err) => {
        if (alive) setError(errorText(err))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [initial, loadDetail])

  const draft = (): ExpertDraft => ({
    name: name.trim(),
    avatar: avatar.trim(),
    description: description.trim(),
    system_prompt: systemPrompt,
    tool_whitelist: tools.split(',').map((t) => t.trim()).filter(Boolean),
  })

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      if (editing) await updateExpert(initial.id, draft())
      else await createExpert(draft())
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? `编辑专家 ${initial?.name}` : '新建专家'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
            disabled={loading}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          头像 emoji（可空）
          <input value={avatar} onChange={(e) => setAvatar(e.target.value)} disabled={loading} className={inputClass} />
        </label>
        <label className={labelClass}>
          描述
          <input value={description} onChange={(e) => setDescription(e.target.value)} disabled={loading} className={inputClass} />
        </label>
        <label className={labelClass}>
          人设提示词
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            required
            disabled={loading}
            className={`${inputClass} resize-y`}
          />
        </label>
        <label className={labelClass}>
          可用工具（逗号分隔，留空 = 全部内置工具）
          <input
            value={tools}
            onChange={(e) => setTools(e.target.value)}
            placeholder="python.run, file.read"
            disabled={loading}
            className={inputClass}
          />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving || loading} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/**
 * 插件安装模态：字段完全由 item.config_schema 驱动。
 *
 * 市场是**首次安装**，没有"当前值"可保留，因此敏感字段的占位直接用 schema
 * 自带的 placeholder（而不是"留空保持不变"）。
 */
export function PluginInstallModal({
  item,
  onClose,
  onDone,
}: {
  item: CatalogItem
  onClose: () => void
  /** 成功后回调（父级关模态并提示）。 */
  onDone: (message: string) => void
}) {
  const install = useCatalogStore((s) => s.install)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交：POST /catalog/plugin/{id}/install（422 缺必填等 detail 内联展示）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    // 文本值统一 trim（避免粘贴带入的首尾空白原样入库，运行期调用才失败）
    const config = Object.fromEntries(
      Object.entries(form).map(([k, v]) => [k, v.trim()]),
    )
    try {
      await install(item.kind, item.id, config)
      onDone(`已安装 ${item.name}`)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={`安装 ${item.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* 公共配置打底提示：有 ready 字段才显示（无打底时留空会被必填校验挡下） */}
        {(item.config_ready_keys?.length ?? 0) > 0 && (
          <p className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 text-xs text-[var(--sa-alias-label-secondary)]">
            标注系统默认的字段已由管理员统一配置，可留空直接安装；填写则覆盖为你的个人配置（仅自己可见）。
          </p>
        )}
        {(item.config_schema ?? []).map((field, index) => {
          const hasDefault = (item.config_ready_keys ?? []).includes(field.key)
          return (
            <label key={field.key} className={labelClass}>
              {field.label}
              {hasDefault && (
                <span className="pl-1 text-xs text-[var(--sa-alias-label-caption)]">（系统默认）</span>
              )}
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={form[field.key] ?? ''}
                onChange={(e) => setForm((f) => ({ ...f, [field.key]: e.target.value }))}
                placeholder={field.placeholder ?? ''}
                // 原生必填校验；敏感字段留空表示不覆盖；有系统默认的字段可留空
                required={Boolean(field.required) && !field.secret && !hasDefault}
                autoFocus={index === 0}
                autoComplete={field.type === 'password' ? 'new-password' : 'off'}
                className={inputClass}
              />
              {field.description ? (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
              ) : null}
            </label>
          )
        })}

        {error && <FormError>{error}</FormError>}

        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className={secondaryButtonClass}>
            取消
          </button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '安装中…' : '安装'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
```

- [ ] **Step 2: MinePanel 改为引用抽出后的 Modal**

在 `apps/web/frontend/src/components/catalog/MinePanel.tsx`：
1. 删除文件内的 `SkillModal` 与 `ExpertModal` 两个函数定义（原第 30-192 行整段）；
2. 顶部 import 区加入：

```tsx
import { ExpertModal, SkillModal } from './CapabilityModals'
```

3. 删除不再使用的 import（`FormError`、`Modal`、`inputClass`、`labelClass`、`primaryButtonClass`、`secondaryButtonClass`、`ExpertDraft`、`SkillDraft`、`FormEvent`、`useState` 中未用到的部分）。改后 MinePanel 顶部 import 应为：

```tsx
import { useEffect, useState } from 'react'
import { GrayBadge } from '@/components/admin/shared'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'
import { ExpertModal, SkillModal } from './CapabilityModals'
```

- [ ] **Step 3: 构建验证**

```bash
cd apps/web/frontend && npm run build
```

Expected: 构建通过（`tsc -b` 无错误；删掉的 Modal 内联定义不能留下未用 import）。

- [ ] **Step 4: 提交**

```bash
git add apps/web/frontend/src/components/catalog/CapabilityModals.tsx apps/web/frontend/src/components/catalog/MinePanel.tsx
git commit -m "抽出能力编辑弹窗，专家编辑回填人设提示词"
```

---

## Task 5: 卡片组件（照抄 jiuwen PageCard）

**Files:**
- Create: `apps/web/frontend/src/components/catalog/CapabilityCard.tsx`

**照抄依据**：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\components\ui\PageCard\PageCard.css`
（卡片 `height:160px; padding:24px; gap:18px; border-radius:16px`；头像 `48×48` 圆角 `10px`；
标题 `14px/600`；标签 chip `height:20px; padding:0 8px; font-size:12px`；
描述 `12px/22px` 两行截断；动作按钮 `32×32` 圆角 `8px`；hover `box-shadow:0 8px 24px rgba(0,0,0,.08)`）

- [ ] **Step 1: 新建卡片组件**

```tsx
/**
 * 能力卡片（市场与「我的」共用）。
 *
 * 尺寸与视觉逐值照抄 jiuwenswarm `components/ui/PageCard/PageCard.css`：
 * 卡片 160px 高 / padding 24px / 圆角 16px / 1px 描边，hover 描边转透明并加
 * `0 8px 24px rgba(0,0,0,.08)` 阴影；左上 48×48 圆角 10px 头像；标题 14px/600；
 * 标签 chip 20px 高 12px 字；描述 12px 两行截断；右上 32×32 圆角 8px 动作按钮。
 *
 * 点卡片本体进详情页；动作按钮 stopPropagation（照 PageCard 的处理）。
 */
import type { MouseEvent, ReactNode } from 'react'

interface CapabilityCardProps {
  /** 卡片标题（能力名）。 */
  title: string
  /** 描述（两行截断）。 */
  description: string
  /** 头像 emoji（专家有则用）；缺省用标题首字母色块。 */
  avatar?: string
  /** 状态徽标（可多个）。 */
  badges?: ReactNode
  /** 右上动作按钮图标（null = 不渲染按钮）。 */
  actionIcon?: ReactNode
  /** 动作按钮的无障碍标签与 tooltip。 */
  actionLabel?: string
  /** 动作按钮点击（已内部 stopPropagation）。 */
  onAction?: () => void
  /** 动作进行中：按钮禁用。 */
  actionBusy?: boolean
  /** 点卡片本体。 */
  onClick: () => void
}

/** 能力卡片。 */
export default function CapabilityCard({
  title,
  description,
  avatar,
  badges,
  actionIcon,
  actionLabel,
  onAction,
  actionBusy = false,
  onClick,
}: CapabilityCardProps) {
  /** 动作按钮点击：不冒泡到卡片（否则会同时触发进详情）。 */
  const handleAction = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    onAction?.()
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className="flex h-40 cursor-pointer flex-col gap-[18px] rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-6 transition-[border-color,box-shadow] duration-[var(--sa-duration-base)] hover:border-transparent hover:shadow-[0_8px_24px_0_rgba(0,0,0,0.08)] focus-visible:border-transparent focus-visible:shadow-[0_8px_24px_0_rgba(0,0,0,0.08)] focus-visible:outline-none"
    >
      {/* 头部：头像 + 标题/标签 + 动作按钮 */}
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-[var(--sa-radius-md)] text-[20px] font-bold ${
            avatar ? '' : 'bg-[var(--sa-alias-state-business-tertiary)] text-[var(--sa-alias-link)]'
          }`}
        >
          {avatar || (title.trim().slice(0, 1).toUpperCase() || '?')}
        </span>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate text-sm font-semibold text-[var(--sa-alias-label-primary)]">
              {title}
            </span>
          </div>
          {badges && <div className="flex min-w-0 flex-wrap items-center gap-1.5">{badges}</div>}
        </div>

        {actionIcon && (
          <button
            type="button"
            title={actionLabel}
            aria-label={actionLabel}
            disabled={actionBusy}
            onClick={handleAction}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-[var(--sa-alias-interactive-bg-active)] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] hover:text-[var(--sa-alias-link)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {actionIcon}
          </button>
        )}
      </div>

      {/* 描述：两行截断（照 PageCard 的 -webkit-line-clamp:2） */}
      <div className="line-clamp-2 text-xs leading-[22px] text-[var(--sa-alias-label-secondary)]">
        {description || '无描述'}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 构建验证**

```bash
cd apps/web/frontend && npx tsc -b
```

Expected: 通过。本任务是新增一个自包含组件，暂时没有消费者（导出的组件未被引用不算错误），Task 6/8 会接上。

- [ ] **Step 3: 提交**

```bash
git add apps/web/frontend/src/components/catalog/CapabilityCard.tsx
git commit -m "能力中心：新增卡片组件（照抄 jiuwen PageCard 规格）"
```

---

## Task 6: 列表页改版（左导航 + 页签 + 卡片网格）

**Files:**
- Modify: `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx`（重写）

- [ ] **Step 1: App.tsx 传 route 给能力中心**

把 `apps/web/frontend/src/App.tsx` 中的：

```tsx
  if (route.kind === 'capabilities') return <CapabilityCenter key={user.sub} />
```

替换为：

```tsx
  // 能力中心：列表与详情共用一个外壳（左导航常驻），任意登录用户可访问
  if (route.kind === 'capabilities' || route.kind === 'capability-detail') {
    return <CapabilityCenter key={user.sub} route={route} />
  }
```

（此步与外壳重写同步，避免留下 props 不匹配的中间态。）

- [ ] **Step 2: 重写 `CapabilityCenter.tsx`**

```tsx
/**
 * 用户侧「能力中心」整页（/capabilities，任意登录用户可见）。
 *
 * 结构：顶栏 + 左导航（专家/技能/插件，照 AdminLayout 的 nav）+ 右侧内容。
 * 右侧按路由渲染列表页（本文件内）或详情页（CapabilityDetail）。
 *
 * 列表页：顶部「市场 | 我的」页签（组件内 state，照 jiuwen ConnectorMarket 的
 * topTab——范围是同一列表的过滤视图，不是独立资源）+ 搜索（前端过滤）+
 * 卡片网格（card-grid-auto，照 jiuwen）。
 *
 * 卡片动作：市场卡未安装给「安装」快按钮（带 config_schema 的插件先弹配置框）；
 * 我的卡给「启用/停用」快按钮。其余操作都在详情页（点卡片进入）。
 * 内置条目（default_enabled）对普通用户只读。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { PageTopBar, ToastHost } from '@/components/layout'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import { useRouterStore } from '@/routing/router'
import type { CapabilityKind, AppRoute } from '@/routing/route'
import type { CatalogItem, MyCapability } from '@/types'
import CapabilityCard, { CardBadge } from './CapabilityCard'
import CapabilityDetail from './CapabilityDetail'
import MinePanel from './MinePanel'
import { PluginInstallModal } from './CapabilityModals'

/** 左导航项（类型 → 展示名）。 */
const NAV: Array<{ kind: CapabilityKind; label: string }> = [
  { kind: 'expert', label: '专家' },
  { kind: 'skill', label: '技能' },
  { kind: 'plugin', label: '插件' },
]

/** 类型图标（16px 线性，与全站图标风格一致）。 */
function kindIcon(kind: CapabilityKind): ReactNode {
  const common = {
    width: 13,
    height: 13,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  if (kind === 'expert') {
    return (
      <svg {...common}>
        <circle cx="8" cy="5.5" r="2.75" />
        <path d="M2.75 13.5c0-2.6 2.35-4.25 5.25-4.25s5.25 1.65 5.25 4.25" />
      </svg>
    )
  }
  if (kind === 'skill') {
    return (
      <svg {...common}>
        <path d="M8 2.5 9.6 6l3.65.35-2.75 2.5.8 3.65L8 10.7l-3.3 1.8.8-3.65-2.75-2.5L6.4 6Z" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path d="M6.5 2.5h3v1.6a2.2 2.2 0 0 1 1.1 1.9V12a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 2.6 12V6a2.2 2.2 0 0 1 1.1-1.9V2.5" />
      <path d="M4 8.5h5" />
    </svg>
  )
}

/** 安装图标（市场卡快按钮）。 */
const INSTALL_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <path d="M8 3.5v9M3.5 8h9" />
  </svg>
)

/** 启停图标（我的卡快按钮）。 */
function toggleIcon(enabled: boolean): ReactNode {
  return enabled ? (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M5.5 11.5h5" />
    </svg>
  ) : (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 4.5 10.5 8 6 11.5" />
    </svg>
  )
}

/** 适配某类型的卡片网格（无条目时渲染空态卡）。 */
function CardGrid({ items, empty, children }: {
  items: number
  empty: string
  children: ReactNode
}) {
  if (items === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--sa-radius-lg)] border border-dashed border-[var(--sa-alias-border-l2)] py-16 text-[13px] text-[var(--sa-alias-label-caption)]">
        {empty}
      </div>
    )
  }
  return (
    <div className="grid justify-center gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">
      {children}
    </div>
  )
}

/** 能力中心整页壳 + 列表页。 */
export default function CapabilityCenter({ route }: { route: AppRoute }) {
  const capabilityKind: CapabilityKind =
    route.kind === 'capabilities' || route.kind === 'capability-detail'
      ? route.capabilityKind
      : 'expert'

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <PageTopBar title="能力中心" />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左导航：类型切换（照 AdminLayout 的 nav 样式） */}
        <nav
          aria-label="能力类型切换"
          className="flex w-[188px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--sa-alias-border-l1)] p-3"
        >
          {NAV.map((item) => {
            const active = item.kind === capabilityKind
            return (
              <button
                key={item.kind}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => useRouterStore.getState().navigate({
                  kind: 'capabilities', capabilityKind: item.kind,
                })}
                className={`flex h-10 items-center gap-2 rounded-[var(--sa-radius-md)] px-3 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
                  active
                    ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] font-medium text-[var(--sa-alias-label-primary)]'
                    : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]'
                }`}
              >
                {kindIcon(item.kind)}
                <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
              </button>
            )
          })}
        </nav>

        {/* 右侧内容：详情页整页接管，列表页走 CapabilityList */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {route.kind === 'capability-detail' ? (
            <CapabilityDetail capabilityKind={route.capabilityKind} itemId={route.itemId} />
          ) : (
            <CapabilityList capabilityKind={capabilityKind} />
          )}
        </main>
      </div>
      <ToastHost />
    </div>
  )
}

/** 列表页（市场 / 我的）。 */
function CapabilityList({ capabilityKind }: { capabilityKind: CapabilityKind }) {
  const byKind = useCatalogStore((s) => s.byKind)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
  const install = useCatalogStore((s) => s.install)
  const setEnabled = useCatalogStore((s) => s.setEnabled)

  const [tab, setTab] = useState<'market' | 'mine'>('market')
  const [marketQuery, setMarketQuery] = useState('')
  const [mineQuery, setMineQuery] = useState('')
  /** 需要先填配置再安装的插件（null 关闭）。 */
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)
  /** 快按钮进行中的条目（`kind:id`）。 */
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    loadMarket().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadMarket])

  const rows = byKind[capabilityKind]
  const query = tab === 'market' ? marketQuery : mineQuery
  const setQuery = tab === 'market' ? setMarketQuery : setMineQuery

  /** 搜索过滤（名称 + 描述）。 */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (r) => r.name.toLowerCase().includes(q) || r.description.toLowerCase().includes(q),
    )
  }, [rows, query])

  const goDetail = (id: string) =>
    useRouterStore.getState().navigate({
      kind: 'capability-detail', capabilityKind, itemId: id,
    })

  /** 安装：带配置 schema 的插件先开表单，其余直装。 */
  const handleInstall = async (item: CatalogItem) => {
    if (item.config_schema && item.config_schema.length > 0) {
      setConfiguring(item)
      return
    }
    setBusy(`${item.kind}:${item.id}`)
    try {
      await install(item.kind, item.id)
      toast('success', `已安装 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy(null)
    }
  }

  /** 启用/停用（我的页签的快按钮）。 */
  const handleToggle = async (item: MyCapability) => {
    setBusy(`${item.kind}:${item.id}`)
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto w-full max-w-[1200px] px-6 py-6">
      {/* 工具行：页签 + 搜索 + 我的态新建入口 */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          {([['market', '市场'], ['mine', '我的']] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-selected={tab === key}
              role="tab"
              onClick={() => setTab(key)}
              className={
                tab === key
                  ? 'rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-[5px] text-[13px] font-medium text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-md)] border border-transparent px-3 py-[5px] text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {label}
            </button>
          ))}
        </div>

        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={tab === 'market' ? '搜索可用能力' : '搜索我的能力'}
          aria-label={tab === 'market' ? '搜索可用能力' : '搜索我的能力'}
          className="h-8 max-w-[320px] min-w-0 flex-1 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-2.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
        />
      </div>

      <div className="pt-5">
        {tab === 'market' ? (
          <CardGrid items={filtered.length} empty={query ? '无匹配能力' : `暂无可用${NAV.find((n) => n.kind === capabilityKind)?.label ?? ''}`}>
            {filtered.map((item) => (
              <CapabilityCard
                key={item.id}
                title={item.name}
                description={item.description}
                badges={
                  <>
                    {item.default_enabled ? (
                      <CardBadge>内置 · 全员可用</CardBadge>
                    ) : item.installed ? (
                      <CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge>
                    ) : null}
                  </>
                }
                actionIcon={!item.installed && !item.default_enabled ? INSTALL_ICON : undefined}
                actionLabel={`安装 ${item.name}`}
                actionBusy={busy === `${item.kind}:${item.id}`}
                onAction={() => void handleInstall(item)}
                onClick={() => goDetail(item.id)}
              />
            ))}
          </CardGrid>
        ) : (
          <MinePanel
            capabilityKind={capabilityKind}
            query={mineQuery}
            onOpen={(item) => goDetail(item.id)}
            onToggle={(item) => void handleToggle(item)}
            busyKey={busy}
          />
        )}
      </div>

      {configuring && (
        <PluginInstallModal
          item={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(message) => {
            setConfiguring(null)
            toast('success', message)
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 3: 构建验证（预期 `CapabilityDetail` 缺失、`MinePanel` props 未更新）**

```bash
cd apps/web/frontend && npx tsc -b
```

Expected: 报 `CapabilityDetail` 模块缺失、`MinePanel` props 不匹配 —— Task 7、Task 8 修复。此步只确认错误是预期的两项。

- [ ] **Step 4: 提交（与 Task 7 一起，见 Task 8 Step 3）**

---

## Task 7: 详情页

**Files:**
- Create: `apps/web/frontend/src/components/catalog/CapabilityDetail.tsx`

**照抄依据**：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\components\AgentManagementPanel\DefinitionDetailPage.tsx`
（返回按钮 → header：大头像 + 名称 + 徽标行 + 右侧动作按钮组 → 限宽 body）

- [ ] **Step 1: 新建详情页**

```tsx
/**
 * 能力详情页（/capabilities/<kind>/<id>）。
 *
 * 结构照 jiuwenswarm `AgentManagementPanel/DefinitionDetailPage.tsx`：
 * 返回按钮 → header（大头像 + 名称 + 徽标行 + 右侧动作按钮组）→ 限宽 body。
 * 不做 jiuwen 的「文件」tab（我们一个技能一份 SKILL.md、一个专家一份 json）。
 *
 * 动作按 origin 四态分支：
 * - market      → 安装（插件先弹配置框）
 * - installed   → 启用/停用 + 卸载
 * - mine        → 编辑 + 删除
 * - builtin     → 普通用户只读；管理员给「在管理后台编辑」跳转
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import { useAuthStore } from '@/stores/auth'
import { useRouterStore } from '@/routing/router'
import type { CapabilityKind } from '@/routing/route'
import type { CapabilityDetail as Detail, CatalogItem, MyCapability } from '@/types'
import { CardBadge } from './CapabilityCard'
import { ExpertModal, PluginInstallModal, SkillModal } from './CapabilityModals'

/** 类型展示名。 */
const KIND_LABEL: Record<CapabilityKind, string> = {
  expert: '专家',
  skill: '技能',
  plugin: '插件',
}

/** 内置条目的管理后台落点。 */
const ADMIN_ROUTE: Record<CapabilityKind, { tab: 'skills' | 'assistants' | 'plugins' }> = {
  skill: { tab: 'skills' },
  expert: { tab: 'assistants' },
  plugin: { tab: 'plugins' },
}

/** 徽标行（类型 · 来源 · 启用态）。 */
function badgesOf(detail: Detail): ReactNode {
  const originLabel: Record<Detail['origin'], string> = {
    mine: '自建',
    installed: '已安装',
    builtin: '内置 · 全员可用',
    market: '未安装',
  }
  return (
    <>
      <CardBadge>{KIND_LABEL[detail.kind]}</CardBadge>
      <CardBadge>{originLabel[detail.origin]}</CardBadge>
      {detail.origin === 'installed' && !detail.revoked && (
        <CardBadge>{detail.enabled ? '已启用' : '已停用'}</CardBadge>
      )}
      {detail.revoked && <CardBadge>已被管理员下架</CardBadge>}
    </>
  )
}

/** 内容块：等宽 pre（技能正文 / 专家人设提示词共用）。 */
function CodeBlock({ text }: { text: string }) {
  return (
    <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-code-block)] p-4 font-mono text-[12.5px] leading-[20px] text-[var(--sa-alias-label-primary)]">
      {text}
    </pre>
  )
}

/** 小标题 + 内容块。 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-[13px] font-medium text-[var(--sa-alias-label-secondary)]">{title}</h2>
      {children}
    </section>
  )
}

/** 标签池（插件附属清单用）。 */
function ChipList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <span className="text-[13px] text-[var(--sa-alias-label-caption)]">无</span>
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((chip) => (
        <span
          key={chip}
          className="inline-flex h-6 items-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 text-xs text-[var(--sa-alias-label-primary)]"
        >
          {chip}
        </span>
      ))}
    </div>
  )
}

/** 详情页动作按钮样式。 */
const actionClass =
  'h-8 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-3 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] disabled:cursor-not-allowed disabled:opacity-50'
const primaryActionClass =
  'h-8 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-button-primary-fill)] bg-[var(--sa-alias-button-primary-fill)] px-3 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'

/** 能力详情页。 */
export default function CapabilityDetail({
  capabilityKind,
  itemId,
}: {
  capabilityKind: CapabilityKind
  itemId: string
}) {
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const install = useCatalogStore((s) => s.install)
  const uninstall = useCatalogStore((s) => s.uninstall)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
  const deleteSkill = useMyCapabilitiesStore((s) => s.deleteSkill)
  const deleteExpert = useMyCapabilitiesStore((s) => s.deleteExpert)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<MyCapability | null | undefined>(undefined)
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)

  /** 拉取详情（写操作后也走它刷新）。 */
  const refresh = useCallback(async () => {
    setError('')
    try {
      setDetail(await loadDetail(capabilityKind, itemId))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [loadDetail, capabilityKind, itemId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** 返回列表（保留左导航，回到同类型的市场列表）。 */
  const goBack = () =>
    useRouterStore.getState().navigate({ kind: 'capabilities', capabilityKind })

  /** 包装写操作：置忙 → 执行 → 刷新详情 → 提示；返回是否成功（删除后据此决定是否返回列表）。 */
  const run = async (label: string, fn: () => Promise<void>): Promise<boolean> => {
    setBusy(true)
    try {
      await fn()
      toast('success', label)
      await refresh()
      return true
    } catch (err) {
      toast('error', errorText(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-[1200px] px-6 py-6 text-[13px] text-[var(--sa-alias-label-caption)]">
        加载中…
      </div>
    )
  }
  if (error || !detail) {
    return (
      <div className="mx-auto flex w-full max-w-[1200px] flex-col items-start gap-3 px-6 py-6">
        <button type="button" onClick={goBack} className={actionClass}>← 返回</button>
        <p className="text-[13px] text-[var(--sa-alias-state-error-primary)]">{error || '条目不存在'}</p>
        <button type="button" onClick={() => void refresh()} className={actionClass}>重试</button>
      </div>
    )
  }

  /** 详情对象转编辑弹窗需要的 MyCapability 形状。 */
  const asMyCapability: MyCapability = {
    kind: detail.kind,
    id: detail.id,
    name: detail.name,
    description: detail.description,
    source: detail.origin === 'mine' ? 'mine' : detail.origin === 'builtin' ? 'builtin' : 'installed',
    enabled: detail.enabled,
    builtin: detail.origin !== 'mine',
  }

  return (
    <div className="mx-auto w-full max-w-[1200px] px-6 py-6">
      <button
        type="button"
        onClick={goBack}
        className="mb-5 flex items-center gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6.5 3 3 8l3.5 5M13 8H3.2" />
        </svg>
        返回
      </button>

      {/* header：头像 + 名称 + 徽标 + 动作组 */}
      <header className="flex flex-wrap items-start gap-4 pb-6">
        <span
          aria-hidden="true"
          className="flex h-16 w-16 shrink-0 items-center justify-center rounded-[var(--sa-radius-md)] bg-[var(--sa-alias-state-business-tertiary)] text-[28px] font-bold text-[var(--sa-alias-link)]"
        >
          {detail.avatar || (detail.name.trim().slice(0, 1).toUpperCase() || '?')}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h1 className="min-w-0 truncate text-[18px] font-semibold text-[var(--sa-alias-label-primary)]">
            {detail.name}
          </h1>
          <div className="flex flex-wrap items-center gap-1.5">{badgesOf(detail)}</div>
        </div>

        {/* 动作按钮组（按 origin 分支） */}
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {detail.origin === 'market' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (detail.config_schema && detail.config_schema.length > 0) {
                  setConfiguring({
                    kind: detail.kind, id: detail.id, name: detail.name,
                    description: detail.description, source: 'builtin',
                    visibility: 'public', default_enabled: false,
                    installed: false, enabled: false, visible: false,
                    config_schema: detail.config_schema,
                    config_ready_keys: detail.config_ready_keys,
                  })
                  return
                }
                void run(`已安装 ${detail.name}`, () => install(detail.kind, detail.id))
              }}
              className={primaryActionClass}
            >
              安装
            </button>
          )}

          {detail.origin === 'installed' && (
            <>
              {/* 被管理员下架的条目只保留卸载（启用一个已下架的能力没有意义） */}
              {!detail.revoked && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(
                    detail.enabled ? `已停用 ${detail.name}` : `已启用 ${detail.name}`,
                    () => setEnabled(detail.kind, detail.id, !detail.enabled),
                  )}
                  className={actionClass}
                >
                  {detail.enabled ? '停用' : '启用'}
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(`已卸载 ${detail.name}`, () => uninstall(detail.kind, detail.id))}
                className={actionClass}
              >
                卸载
              </button>
            </>
          )}

          {detail.origin === 'mine' && (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(asMyCapability)}
                className={actionClass}
              >
                编辑
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void (async () => {
                  const remove = detail.kind === 'skill'
                    ? () => deleteSkill(detail.id)
                    : () => deleteExpert(detail.id)
                  // 删除成功后条目已不存在，详情页必须退回列表；失败则留在原地让用户看到提示
                  if (await run(`已删除 ${detail.name}`, remove)) goBack()
                })()}
                className={actionClass}
              >
                删除
              </button>
            </>
          )}

          {detail.origin === 'builtin' && (
            isAdmin ? (
              <button
                type="button"
                onClick={() => useRouterStore.getState().navigate({
                  kind: 'admin', tab: ADMIN_ROUTE[detail.kind].tab,
                })}
                className={actionClass}
              >
                在管理后台编辑
              </button>
            ) : (
              <span className="text-[13px] text-[var(--sa-alias-label-caption)]">自动可用</span>
            )
          )}
        </div>
      </header>

      <div className="flex flex-col gap-6">
        {detail.description && (
          <Section title="描述">
            <p className="text-[13px] leading-[22px] text-[var(--sa-alias-label-primary)]">
              {detail.description}
            </p>
          </Section>
        )}

        {detail.kind === 'expert' && (
          <>
            <Section title="人设提示词">
              <CodeBlock text={detail.system_prompt || '（空）'} />
            </Section>
            <Section title="可用工具">
              <ChipList items={detail.tool_whitelist ?? []} />
              {(detail.tool_whitelist ?? []).length === 0 && (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">留空表示全部内置工具可用</span>
              )}
            </Section>
          </>
        )}

        {detail.kind === 'skill' && (
          <Section title="技能正文（SKILL.md）">
            <CodeBlock text={detail.content || '（空）'} />
          </Section>
        )}

        {detail.kind === 'plugin' && (
          <>
            <Section title="配置字段">
              {(detail.config_schema ?? []).length === 0 ? (
                <span className="text-[13px] text-[var(--sa-alias-label-caption)]">该插件无需配置</span>
              ) : (
                <div className="flex flex-col gap-2">
                  {(detail.config_schema ?? []).map((field) => (
                    <div
                      key={field.key}
                      className="flex flex-wrap items-center gap-2 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l1)] px-3 py-2"
                    >
                      <span className="text-[13px] text-[var(--sa-alias-label-primary)]">{field.label}</span>
                      <span className="font-mono text-xs text-[var(--sa-alias-label-caption)]">{field.key}</span>
                      {field.required && <CardBadge>必填</CardBadge>}
                      {(detail.config_ready_keys ?? []).includes(field.key) && (
                        <CardBadge>系统默认已就绪</CardBadge>
                      )}
                      {field.description && (
                        <span className="w-full text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Section>
            <Section title="自带技能">
              <ChipList items={(detail.skills ?? []).map((s) => s.name)} />
            </Section>
            <Section title="播种专家">
              <ChipList items={(detail.experts ?? []).map((e) => e.name)} />
            </Section>
            <Section title="注册工具">
              <ChipList items={detail.tools ?? []} />
            </Section>
          </>
        )}
      </div>

      {/* 编辑弹窗（自建技能/专家） */}
      {editing !== undefined && detail.kind === 'skill' && (
        <SkillModal
          initial={editing}
          onClose={(changed) => {
            setEditing(undefined)
            if (changed) void refresh()
          }}
        />
      )}
      {editing !== undefined && detail.kind === 'expert' && (
        <ExpertModal
          initial={editing}
          onClose={(changed) => {
            setEditing(undefined)
            if (changed) void refresh()
          }}
        />
      )}

      {/* 插件安装配置弹窗 */}
      {configuring && (
        <PluginInstallModal
          item={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(message) => {
            setConfiguring(null)
            toast('success', message)
            void refresh()
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: 构建验证（预期仅剩 MinePanel props 一项）**

```bash
cd apps/web/frontend && npx tsc -b
```

Expected: 仅剩 `MinePanel` props 不匹配（Task 8 修复）。

- [ ] **Step 3: 提交（与 Task 8 一起）**

---

## Task 8: MinePanel 卡片化

**Files:**
- Modify: `apps/web/frontend/src/components/catalog/MinePanel.tsx`

- [ ] **Step 1: 重写 `MinePanel.tsx`**

```tsx
/**
 * 「我的」面板：自建 + 已安装 + 内置只读（技能/专家/插件）。
 *
 * 子页签（全部/已启用/已停用/内置）与过滤逻辑照旧；渲染改为卡片网格
 * （复用 CapabilityCard，与市场页视觉统一）。
 *
 * 内置条目对普通用户只读（无启停/编辑/删除入口，标「自动可用」），
 * 与后端 `default_enabled` 口径一致。
 */
import { useEffect, useMemo, useState } from 'react'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'
import CapabilityCard, { CardBadge } from './CapabilityCard'
import { ExpertModal, SkillModal } from './CapabilityModals'

type SubTab = 'all' | 'enabled' | 'disabled' | 'builtin'

const SUB_TABS: Array<{ key: SubTab; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'enabled', label: '已启用' },
  { key: 'disabled', label: '已停用' },
  { key: 'builtin', label: '内置' },
]

interface MinePanelProps {
  /** 当前类型（左导航选中项）。 */
  capabilityKind: 'expert' | 'skill' | 'plugin'
  /** 搜索词（由列表页统一持有，与市场页各自独立）。 */
  query: string
  /** 点卡片进详情。 */
  onOpen: (item: MyCapability) => void
  /** 卡片上的启用/停用快按钮。 */
  onToggle: (item: MyCapability) => void
  /** 快按钮进行中的条目键（`kind:id`）。 */
  busyKey: string | null
}

/** 「我的」面板。 */
export default function MinePanel({ capabilityKind, query, onOpen, onToggle, busyKey }: MinePanelProps) {
  const items = useMyCapabilitiesStore((s) => s.items)
  const builtinItems = useMyCapabilitiesStore((s) => s.builtinItems)
  const loaded = useMyCapabilitiesStore((s) => s.loaded)
  const loadMine = useMyCapabilitiesStore((s) => s.loadMine)
  const [sub, setSub] = useState<SubTab>('enabled')
  const [editingSkill, setEditingSkill] = useState<MyCapability | null | undefined>(undefined)
  const [editingExpert, setEditingExpert] = useState<MyCapability | null | undefined>(undefined)

  useEffect(() => {
    loadMine().catch((err) => toast('error', `加载我的能力失败：${errorText(err)}`))
  }, [loadMine])

  // 内置页签走只读数据源，其余页签过滤自己的列表；再按当前类型与搜索词收窄
  const rows = useMemo(() => {
    const source = sub === 'builtin'
      ? builtinItems
      : items.filter((item) => {
          if (sub === 'enabled') return item.enabled
          if (sub === 'disabled') return !item.enabled
          return true
        })
    const byKind = source.filter((item) => item.kind === capabilityKind)
    const q = query.trim().toLowerCase()
    if (!q) return byKind
    return byKind.filter(
      (item) => item.name.toLowerCase().includes(q) || item.description.toLowerCase().includes(q),
    )
  }, [sub, items, builtinItems, capabilityKind, query])

  return (
    <div className="flex flex-col gap-3">
      {/* 子页签 + 自建入口 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {SUB_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setSub(t.key)}
              className={
                sub === t.key
                  ? 'rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-sm)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setEditingSkill(null)}
            className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
          >
            + 新建技能
          </button>
          <button
            type="button"
            onClick={() => setEditingExpert(null)}
            className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
          >
            + 新建专家
          </button>
        </div>
      </div>

      {loaded && rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--sa-radius-lg)] border border-dashed border-[var(--sa-alias-border-l2)] py-16 text-[13px] text-[var(--sa-alias-label-caption)]">
          {sub === 'builtin'
            ? '暂无平台内置条目'
            : query
              ? '无匹配能力'
              : '还没有条目，去「市场」安装，或点右上角自己创建一个'}
        </div>
      ) : (
        <div className="grid justify-center gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">
          {rows.map((item) => (
            <CapabilityCard
              key={`${item.kind}:${item.id}`}
              title={item.name}
              description={item.description}
              badges={
                <>
                  {item.source === 'builtin' ? (
                    <CardBadge>内置 · 全员可用</CardBadge>
                  ) : (
                    <CardBadge>{item.source === 'mine' ? '自建' : '已安装'}</CardBadge>
                  )}
                  {item.source !== 'builtin' && (
                    <CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge>
                  )}
                  {item.revoked && <CardBadge>已被管理员下架</CardBadge>}
                </>
              }
              actionIcon={
                item.source === 'builtin'
                  ? undefined
                  : (
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      {item.enabled ? <path d="M5.5 11.5h5" /> : <path d="M6 4.5 10.5 8 6 11.5" />}
                    </svg>
                  )
              }
              actionLabel={item.enabled ? `停用 ${item.name}` : `启用 ${item.name}`}
              actionBusy={busyKey === `${item.kind}:${item.id}`}
              onAction={() => onToggle(item)}
              onClick={() => onOpen(item)}
            />
          ))}
        </div>
      )}

      {editingSkill !== undefined && (
        <SkillModal
          initial={editingSkill}
          onClose={(changed) => {
            setEditingSkill(undefined)
            if (changed) {
              toast('success', '已保存技能')
              void loadMine()
            }
          }}
        />
      )}
      {editingExpert !== undefined && (
        <ExpertModal
          initial={editingExpert}
          onClose={(changed) => {
            setEditingExpert(undefined)
            if (changed) {
              toast('success', '已保存专家')
              void loadMine()
            }
          }}
        />
      )}
    </div>
  )
}
```

注意：删除入口只在详情页（面板内不重复提供删除按钮，避免同一动作两处入口）。

- [ ] **Step 2: 构建验证**

```bash
cd apps/web/frontend && npm run build
```

Expected: 构建通过（`tsc -b` 无错误，vite 产出 dist）。

- [ ] **Step 3: 提交**

```bash
git add apps/web/frontend/src/App.tsx apps/web/frontend/src/components/catalog/
git commit -m "能力中心改版：左导航分类 + 卡片式市场/我的 + 详情页"
```

---

## Task 9: 版本号与文档

**Files:**
- Modify: `apps/web/backend/app/version.py`
- Modify: `apps/web/frontend/package.json`
- Modify: `README.md`

- [ ] **Step 1: 同步版本号**

`apps/web/backend/app/version.py`：

```python
APP_VERSION = "0.10.0-beta.1"
```

`apps/web/frontend/package.json`：`"version": "0.10.0-beta.1"`

- [ ] **Step 2: 更新 README**

在 README 的能力中心条目里，把「市场/我的两栏 + 行列表」的描述改为：

```markdown
- 用户侧「能力中心」（`/capabilities`）：左侧类型导航（专家 / 技能 / 插件），
  右侧卡片式「市场 / 我的」两态，支持搜索与一键安装/启停；
  点击卡片进入**详情页**（`/capabilities/<类型>/<条目 id>`）查看技能正文、
  专家人设提示词、插件配置字段与附属清单，并在此执行安装/启停/编辑/卸载。
  内置条目对普通用户只读（管理员可在详情页跳转管理后台编辑）。
```

- [ ] **Step 3: 全量验证**

```bash
cd apps/web/backend && conda run -n synlysagent python -m pytest -q
cd apps/web/frontend && npm run build
```

Expected: 后端全绿；前端构建通过。

- [ ] **Step 4: 提交**

```bash
git add apps/web/backend/app/version.py apps/web/frontend/package.json README.md
git commit -m "能力中心改版同步版本号 0.10.0-beta.1 与 README"
```

---

## 手工验收（用户自测）

启动后端（前端 dist 已构建，单端口托管）：

```bash
cd apps/web/backend && conda run -n synlysagent python -m uvicorn app.main:app --port 8005
```

逐条对照规格第 10 节验收标准，重点：

1. `/capabilities`、`/capabilities/skill`、`/capabilities/plugin` 三个入口 URL 渲染正确，左导航高亮同步
2. 卡片 hover 阴影、两行描述截断、动作按钮位置与 jiuwen 观感一致
3. 点卡片进详情；**直接刷新详情 URL** 仍正确渲染；后退回列表
4. 详情页能看到：专家人设提示词、技能 SKILL.md 正文、插件配置字段与附属清单
5. 内置条目（如 `asst-data`、`office-doc`、`spec_agent`）普通用户无任何编辑入口；管理员可见「在管理后台编辑」
6. 「我的」里编辑自建专家：弹窗打开即带出原人设提示词（bug 已修）
