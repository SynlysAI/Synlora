# 能力中心改版设计（左导航分类 + 卡片市场 + 详情页）

日期：2026-09-16
状态：待实施
关联：`docs/superpowers/plans/2026-09-15-synlysagent-11-capability-center-ui.md`（上一版行列表实现）

## 1. 背景与目标

用户侧能力中心 `/capabilities` 现为「顶部市场/我的横页签 + 按类型分组的**行列表**」，
信息密度低、视觉朴素，且没有条目详情（技能看不到 SKILL.md 正文、专家看不到人设提示词）。

本次改版：

1. 左侧改为**类型导航**（专家 / 技能 / 插件），右侧内容区为卡片式「市场 / 我的」；
2. 卡片点击进**详情页**（路由级），详情页可查看条目全量信息并执行安装/启停/编辑/卸载；
3. 后端补**能力详情接口**，同时修掉专家编辑弹窗看不到原人设提示词的既有缺陷。

### 核心设计纪律

UI 必须照抄参考项目，禁止自行发挥：

- 卡片 / 网格 → jiuwen `components/ui/PageCard/PageCard.css`、`src/index.css` 的 `.card-grid-auto`
- 列表页工具行（页签 + 搜索 + 新建）→ jiuwen `components/ConnectorMarket/MarketplacePage.tsx`
- 详情页布局 → jiuwen `components/AgentManagementPanel/DefinitionDetailPage.tsx`
- 左导航 → 本项目 `components/admin/AdminLayout.tsx`（已有范式，与全站左导航一致）

## 2. 范围

### 做

- 路由：`/capabilities/<kind>` 列表页、`/capabilities/<kind>/<id>` 详情页
- 列表页：左导航 + 「市场/我的」页签 + 搜索 + 卡片网格 + 空态
- 详情页：条目全量信息 + 动作按钮组
- 后端：新增 `GET /api/v1/me/capabilities/{kind}/{item_id}` 详情接口 + 接口测试
- 内置条目（专家/技能/插件）对普通用户**纯预览只读**，管理员在详情页给管理后台跳转入口
- 修复：专家编辑弹窗打开时 `system_prompt` 为空（保存即覆盖丢失）
- 版本号与 README 同步

### 不做

- 不改 `catalog_policy` 可见性逻辑、不改 `PUT /me/capabilities` 启停语义
- 不改会话侧能力装配（`CapabilityService`）、不改管理后台
- 不做 jiuwen 详情页的「文件」tab（我们一个技能一份 SKILL.md、一个专家一份 json，无文件树可看）
- 不做「去使用」（把能力塞进新会话）入口——会话侧入口仍是输入框「+ 插件」面板，本次不扩
- 不做分页（条目量小，网格滚动即可；jiwen 的 CatalogPage 分页不搬）

## 3. 路由设计

`src/routing/route.ts` 扩展（保持「URL 是唯一事实源」惯例）：

| 路径 | 路由 |
|---|---|
| `/capabilities` | `{ kind: 'capabilities', capabilityKind: 'expert' }`（解析层直接视为专家，**不重写 URL**，旧链接可用） |
| `/capabilities/<kind>` | `{ kind: 'capabilities', capabilityKind }` |
| `/capabilities/<kind>/<id>` | `{ kind: 'capability-detail', capabilityKind, itemId }` |

- `kind` 非法（非 expert/skill/plugin）→ not-found
- `itemId` 走 `encodeURIComponent` / `decodeURIComponent`
- 「市场 / 我的」页签是**组件内 state**，不进路由（照 jiuwen `topTab`）：它是同一列表的过滤视图，不是独立资源

## 4. 列表页

### 4.1 布局

```
PageTopBar「能力中心」
└─ flex（min-h-0 flex-1）
   ├─ nav 左导航 w-[188px]（照 AdminLayout：flex-col gap-1 border-r border-l1 p-3）
   │   项：h-10 px-3 rounded-md text-[13px]
   │   active：bg-[--sa-specific-sidebar-nav-item-active] font-medium
   │   非 active：text-[--sa-alias-label-secondary] hover:bg-[--sa-specific-sidebar-nav-item-hover]
   └─ main（min-h-0 flex-1 overflow-y-auto）
      └─ page-shell：width min(1200px, calc(100% - 48px))，水平居中
         ├─ 工具行（flex items-center gap-3）
         │   ├─ 「市场 | 我的」页签（chat-picker-panel__tabs 风格：文字按钮 + active 高亮）
         │   ├─ 搜索框（flex-1，前端过滤 name/description）
         │   └─ 「我的」态：+ 新建技能 / + 新建专家
         ├─ 我的子筛选（仅「我的」态）：全部 / 已启用 / 已停用 / 内置
         └─ 卡片网格（.card-grid-auto 见 4.3）
```

滚动只发生在 main 内（照 AdminLayout）。

### 4.2 页签与筛选

- 页签：市场 / 我的（组件内 state，默认「市场」，切换不重置搜索词——各自独立保存）
- 搜索：前端 `toLowerCase().includes()` 过滤 `name` 与 `description`，空态区分「无匹配」与「暂无条目」
- 我的子筛选沿用现有 `SubTab`（`all` / `enabled` / `disabled` / `builtin`）与过滤逻辑，仅换渲染

### 4.3 卡片（照抄 jiuwen `PageCard.css`，数值逐条映射 token）

| 属性 | jiuwen 值 | 本项目写法 |
|---|---|---|
| 卡片尺寸 | `height:160px; min-width:360px; padding:24px; gap:18px` | 同值（Tailwind 任意值） |
| 圆角 | `border-radius:16px` | `--sa-radius-lg`（=16px） |
| 描边 | `outline:1px solid --color-border-default` | `border border-[--sa-alias-border-l2]` |
| hover | `outline-color:transparent; box-shadow:0 8px 24px rgba(0,0,0,.08)` | 同值 |
| 头像 | `48×48; border-radius:10px; font-size:20px; font-weight:700` | `--sa-radius-md`（=10px） |
| 标题 | `14px / 600 / line-height:20px`，单行截断 | `text-sm font-semibold` |
| 标签 chip | `height:20px; padding:0 8px; font-size:12px; border-radius:--radius-sm` | `--sa-radius-sm` |
| 描述 | `12px / line-height:22px`，`-webkit-line-clamp:2` | 同值 |
| 动作按钮 | `32×32; border-radius:8px`，常显于 header 右侧 | 同值同位置（不做 hover 才显现） |

网格（照 `card-grid-auto`）：

```css
display: grid;
grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
gap: 16px;
align-content: start;
justify-content: center;
```

卡片内容：

```
[头像] 名称（+ 状态徽标）        [动作按钮 32×32]
[来源 chip] [启用态 chip]
描述（两行截断）
```

- 头像：专家有 `avatar`（emoji）则渲染 emoji；否则首字母色块（底 `--sa-alias-state-business-tertiary`、字 `--sa-alias-link`）
- 徽标（复用现有 `GrayBadge`）：
  - 市场卡：`内置 · 全员可用`（`default_enabled`）｜ `已启用` / `已停用`（已安装）
  - 我的卡：`自建` / `已安装` / `内置 · 全员可用` + `已被管理员下架`（`revoked`）
- 动作按钮：
  - 市场卡：未安装 → `+`（安装，带 `config_schema` 的插件先弹配置框）；已安装 / 内置 → 无按钮
  - 我的卡：非内置 → 启用/停用切换按钮；内置 → 无按钮（标「自动可用」）
- 点卡片本体 → 详情页；动作按钮 `stopPropagation`
- 空态：整行虚线卡（`col-span-full`，`rounded-[--sa-radius-lg] border border-dashed py-16`）

## 5. 详情页

照 jiuwen `DefinitionDetailPage` 的信息结构（不含文件 tab）：

```
← 返回（文字按钮 + 左箭头，返回列表并保留原页签态）
header（flex，gap-4）
  [头像 64×64]  名称（h1，text-lg font-semibold）
                徽标行：[类型] [来源] [启用态 / 内置]
                （右）动作按钮组
描述段落（text-sm / leading-22px，全文不截断）
内容块（按 kind 分支）
```

内容块：

| kind | 内容 |
|---|---|
| 专家 | 人设提示词（等宽 `pre` 块，`whitespace-pre-wrap`）+ 可用工具 chips（空 = 「全部内置工具」） |
| 技能 | SKILL.md 正文（等宽 `pre` 块，`whitespace-pre-wrap`） |
| 插件 | 配置字段清单（label / 必填 / 「系统默认已就绪」标记）+ 附属技能 chips + 播种专家 chips + 注册工具 chips |

动作按钮组（按 `origin` 四值分支，与第 6.1 节的返回口径一一对应）：

- `market`（市场可见未安装）：安装（有 `config_schema` 先弹 `PluginInstallModal`）
- `installed`（已安装的内置条目）：启用/停用 + 卸载
- `mine`（用户自建技能/专家）：编辑（复用 `SkillModal` / `ExpertModal`）+ 删除
- `builtin`（内置，全员自动可用）：**普通用户纯预览**（标「自动可用」，无任何编辑入口）；
  管理员额外显示「在管理后台编辑」，跳 `/admin/skills` ｜ `/admin/assistants` ｜ `/admin/plugins` 对应页

内置条目对普通用户一律只读，是本次改版的红线：卡片、详情页都不出现编辑/删除入口。
判定依据是 `origin === 'builtin'`，与角色（`user.role === 'admin'`）取交集才给管理员跳转。

**`revoked` 行（被管理员下架）**：详情页显示「已被管理员下架」徽标，
动作只保留**卸载**（不带启停/编辑），与「我的」列表的清理意图一致。

加载 / 错误态：居中态 + 重试按钮（照现有 `agent-management-state` 结构，用本项目 token）。

## 6. 后端接口

### 6.1 `GET /api/v1/me/capabilities/{kind}/{item_id}`

与既有 `PUT /me/capabilities/{kind}/{item_id}` 同路径、同语义域（「我视角下的这个能力」）。

**解析顺序**

1. `kind` 非法 → 404
2. 技能 / 专家：先查**用户自建**（用户层技能根 / `ExpertService` 用户专家）→ 命中返回 `origin="mine"`，携正文或 `system_prompt`
3. 否则查 catalog 条目 + policy，按**列表可达性**判定放行
4. 命中返回 `origin`：`default_enabled` → `"builtin"`；已写安装记录 → `"installed"`；两者皆非 → `"market"`（未安装的市场条目也能看详情，否则用户无法在安装前判断）

**hidden 条目的放行规则（revoked 场景）**

`hidden` 且未安装 → 404（不泄露存在性）。但**已安装后被管理员下架**（`hidden` + 已安装 + 非内置）的行，
`/me/skills`、`/me/experts` 会刻意保留（带 `revoked: true`）供用户清理，详情必须同样放行，
否则「我的」里点进去是死路。判定条件与 `revoked` 标记**共用同一个谓词**：

```
revoked = hidden and installed and not default_enabled
visible = (not hidden) or revoked
```

返回体在 `revoked` 为真时带 `"revoked": true`（其余情形不带该键，与「kind 特有字段按需出现」同口径）。

配套：`PUT /me/capabilities/{kind}/{item_id}` 对 hidden 条目**只放行卸载**（`installed=false` 且确有安装记录），
使「我的」里的卸载按钮可用；其余 hidden 请求仍 404。

**返回体**

```json
{
  "kind": "expert|skill|plugin",
  "id": "条目 id（技能 = 技能名）",
  "name": "显示名",
  "description": "描述",
  "origin": "mine|installed|builtin|market",
  "enabled": true,
  "avatar": "🧪",
  "system_prompt": "...",
  "tool_whitelist": ["python.run"],
  "content": "SKILL.md 正文",
  "config_schema": [ ... ],
  "config_ready_keys": [ ... ],
  "skills": [ ... ], "experts": [ ... ], "tools": [ ... ]
}
```

kind 特有字段按需返回，不相关字段不出现（与 `market_items` 只给插件加 `config_schema` 同口径，避免前端误判）。

**实现要点**

- 复用 `CapabilityService` 的 catalog / policy / installs，不新增可见性逻辑
- 自建技能正文读取走既有 `SkillService`（用户层优先、公共层兜底的同名唯一解析）
- 插件附属清单复用现有 `PluginInfo` 组装路径（`skills` / `experts` / `tools`）

### 6.1.1 内置条目的内容来源（只读预览）

内置条目（`origin ∈ {builtin, installed, market}`）的内容一律按 `catalog_roots()` 的既有
优先级解析：

```
repo 的 apps/web/backend/catalog/          （随代码仓库，初始状态即与此一致）
  └─ 被 {data_dir}/public/catalog/ 的同名条目覆盖（管理员编辑后的落点）
```

- **技能正文 / 专家人设提示词 / 插件配置声明**都从这条链上读，不在数据库里另存副本——
  服务初始运行状态 = 代码仓库内容，天然一致，无需额外初始化步骤
- 管理员编辑内置内容走既有管理后台（`/admin/skills`、`/admin/assistants`、`/admin/plugins`），
  写入数据目录公共层覆盖 repo 版本；本接口只读，**不提供任何写入路径**
- 用户侧自建（`origin === "mine"`，落 `users/<uid>/`）可编辑，不受此约束

### 6.2 既有缺陷修复

`GET` 详情接口同时服务「专家编辑回填」：`ExpertModal` 打开时先拉详情，
把 `system_prompt` / `tool_whitelist` / `avatar` 填进表单（现在这三项起点为空，
保存即覆盖原内容）。

## 7. 前端数据层

- `stores/catalog.ts`：新增 `loadDetail(kind, id)`，详情按 `kind:id` 缓存；`install` / `uninstall` / `setEnabled` 成功后失效对应缓存（列表刷新逻辑不变）
- `stores/myCapabilities.ts`：`loadSkillDetail(name)` / `loadExpertDetail(id)` 共用同一详情方法（同接口）
- 详情页按路由参数在挂载时拉取，自带 `loading / error / retry` 三态
- 组件拆分（`components/catalog/`）：
  - `CapabilityCenter.tsx` — 路由分发 + 左导航 + 页签壳
  - `CapabilityCard.tsx` — 卡片（PageCard 照抄实现）
  - `CapabilityDetail.tsx` — 详情页
  - `MinePanel.tsx` — 保留（渲染改为卡片网格），子筛选逻辑不动

## 8. 测试

后端新增接口写集成测试（`apps/web/backend/tests/`，沿用现有 fixture 与认证方式）：

1. 市场可见条目详情返回全量字段（技能带正文、专家带 `system_prompt`）
2. hidden 条目 → 404
3. 自建技能/专家详情 → `origin="mine"` 且仅本人可取（他人 404）
4. 未知 kind / 未知 id → 404

前端不写单测（沿用项目惯例：`npm run build` 通过即交付，真机由用户自测）。

## 9. 版本与文档

- `apps/web/backend/app/version.py` 与 `apps/web/frontend/package.json` 同步
  `0.9.0-beta.1` → `0.10.0-beta.1`（向下兼容的新功能，升次版本号）
- `README.md` 更新能力中心功能说明（左导航分类 + 卡片市场 + 详情页）

## 10. 验收标准

1. `/capabilities` 自动落到专家列表；左导航三类型切换正常；URL 随之变化
2. 市场/我的页签切换正常，搜索可过滤，我的子筛选正常
3. 卡片样式与 jiuwen PageCard 一致（尺寸、圆角、hover 阴影、两行描述截断）
4. 点卡片进详情页；直接刷新详情 URL 仍能正确渲染；浏览器后退回列表
5. 详情页能看到技能正文 / 专家人设提示词 / 插件配置字段与附属清单
6. 详情页动作（安装/启停/编辑/卸载）与列表卡片动作功能等价、状态同步刷新
7. 专家编辑弹窗打开时能看到原人设提示词，保存后内容正确
8. 内置条目：普通用户（含已安装态）在列表与详情页均无编辑/删除入口；
   管理员在详情页可见「在管理后台编辑」并跳转正确
9. 内置条目详情内容与代码仓库 `apps/web/backend/catalog/` 一致（换仓库内容重启后即同步）
10. 后端 4 条接口测试通过；前端 `npm run build` 通过
