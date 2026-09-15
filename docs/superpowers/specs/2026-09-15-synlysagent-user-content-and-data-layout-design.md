# 用户级内容（技能/专家）与数据目录分层设计

- 日期：2026-09-15
- 状态：已与用户逐节评审通过（目录布局、安装模型、重名规则、专家落点、API 形态、前端落点六项决策）
- 参考实现：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\components\SkillPanel\index.tsx`（市场/我的两栏 + 子页签）、`utils/mySkills.ts`（"我的"判定口径）、`get_agent_skills_dir()`（技能唯一实体库）

## 1. 背景与目标

现状（编码前）：

- 内置内容三类统一在 `apps/web/backend/catalog/{experts,skills,plugins}/`（**单一来源、只读**），管理员用 `catalog_policy` 配可见性与"默认启用"，用户安装只写 `user_capabilities` 记录（**不复制文件**）。
- 技能磁盘层只有两层：catalog 只读根 + `{data_root}/skills` 公共可写层（管理员自建）。**没有用户级技能**。
- 专家经 `seed_experts` 从 `catalog/experts/*/expert.json` 播种进 `assistants` 集合（`builtin=true`）。
- 数据目录按"类型"平铺：`{data_root}/workspaces/<uid>/<项目>/`、`{data_root}/sessions/<sid>/events.jsonl`、`{data_root}/skills/`。

目标：

1. **用户可自建技能与专家**，且自建内容落在**各自的用户目录**里。
2. 内置内容改为**"安装后才可用"**——不再默认对所有人生效。
3. 数据目录改为**按用户分层**，公共层与用户层分开。

非目标（本期不做）：用户自建插件、技能/专家的跨用户分享或发布、用户配额计量、内置内容的 fork 定制。

## 2. 核心决策（逐条已确认）

| # | 决策点 | 结论 | 理由 |
|---|---|---|---|
| D1 | 目录布局 | `data_root/{public/{skills,catalog}, users/<uid>/{workspaces,sessions,skills,experts}}` | 根目录只剩"公共 / 用户"两层；公共可写技能层从 `{data}/skills` 挪进 `public/skills` |
| D2 | 内置内容的"安装"落地 | **记录式**：写 `user_capabilities`（含 `enabled`），**不复制文件** | 复制会带来版本漂移（平台更新内置技能后已装用户收不到）+ 插件代码复制必然工具名冲突；jiuwen 同样是"全局唯一实体库 + 安装记录" |
| D3 | 默认可见性 | `catalog_policy` 的 `default_enabled` **默认关闭**（`visibility=public` 仍在市场可见） | "所有用户都必须安装后才能使用"；`default_enabled` 保留为部署时的例外开关（管理员可让特定条目开箱可用） |
| D4 | 重名规则 | 技能名**全局唯一**：自建技能与内置/公共同名 → **创建即拒绝** | 运行期技能索引是名字集合（`hidden_skill_names` 黑名单口径），同名会让"装的是哪个、能不能删"含混 |
| D5 | 内置技能可否 fork/编辑 | **不可**。想定制就自建一个换名的 | 避免"已装副本不进更新"，也避免与 D2 的单一来源矛盾 |
| D6 | 用户专家落点 | 文件 `users/<uid>/experts/<dir>/expert.json`（与内置**同构**），**读时幂等实例化**进 `assistants` | `assistant_id` 绑定、会话恢复、助手管理页都不用改；文件为事实源，编辑"先动文件再刷记录"（照 `rename_project` 纪律） |
| D7 | 能力开关 API | 单个 `PUT /api/v1/me/capabilities/{kind}/{id}`，body `{installed?, enabled?}` | 两类状态用同一端点表达，避免 install/uninstall/enable/disable 四个动作端点 |
| D8 | 前端落点 | 改造现有 `/capabilities` 能力中心整页为「市场 / 我的」两栏，**不新增路由** | 维持"能力中心为独立整页"的既有约定 |

## 3. 目录布局

```
data_root/
├── public/
│   ├── skills/                    # 公共可写技能层（管理员自建/导入，可写可删）
│   └── catalog/                   # 运行期安装预留根（保留现状语义）
└── users/<uid>/                   # <uid> = AI⁴MS 用户文档 _id（token sub），不可变
    ├── workspaces/<项目目录>/{files,output,tmp}
    ├── sessions/<sid>/events.jsonl
    ├── skills/<name>/SKILL.md      # 用户自建技能
    └── experts/<dir>/expert.json   # 用户自建专家（与 catalog/experts 同构）
```

内置内容（repo 的 `apps/web/backend/catalog/`）**不落用户目录**，始终单一来源。

目录名用 `uid` 而非用户名：`sub` 是平台主键（workspaces/会话/项目/能力安装都按它存，见 `app/services/ai4ms_identity.py` 头部说明），用户名可变、有大小写与重名问题，改名会牵动整棵目录树。需要可读性时在 `users/<uid>/` 下放 `profile.json`，不把用户名塞进路径。

## 4. 安装与可见性模型（三类统一）

| 概念 | 落点 | 说明 |
|---|---|---|
| 市场条目 | `catalog/{skills,experts,plugins}` | 内置可安装源，不复制 |
| 安装记录 | `user_capabilities` 集合 | 新增 `enabled: bool`；默认 `true` |
| 我的技能/专家 | 自建（`users/<uid>/skills|experts`）∪ 已安装的内置条目 | 照 `computeMySkills` 口径合并，带 `source: mine|installed` |
| 插件 | 全局装载一次（代码）+ 该用户 installed+enabled 才注入 `ctx.extra["plugins"]` | 代码不复制；停用=不注入，不卸载 |

可见性判定链（复用现有 `CapabilityService`，只加一层）：

```
exists(条目在 catalog) → visibility != hidden → 已安装 ? 启用态 : default_enabled
```

即**用户的显式动作优先于平台默认**：装过就以他的启用/停用为准（停用即不可见，与未装同等），没装过才看 `default_enabled`。这样「默认启用」的条目被用户停用后真正失效，而不是徽标显示"已停用"却照样注入运行期。

- "已装但停用"的技能名要进 `hidden_skill_names`（与"未装"同样是不可见），插件同理不进 `ctx.extra`。
- 市场列表返回每个条目的 `installed` / `enabled`，前端据此渲染按钮态。
- **hidden 优先于已装**（保持现状语义，`visible_ids` 对 hidden 直接跳过）：管理员把条目下架后，已装用户运行期也不可见；「我的」列表仍列出该条目但标注"已被管理员下架"，且不可启用。

## 5. 命名与冲突

- 技能名沿用 `kebab-case` 校验（`SkillService.NAME_OK`）。
- 创建自建技能时检查三类占用：该用户名下已有同名、公共层同名、内置目录同名 → 一律 409 拒绝并提示换名。
- 目录名 = 技能名（与现有 catalog 技能一致）。
- 专家 id 形如 `<uid>:<dir>`，`source_dir` 记相对路径；展示名（`name`）允许重名，靠 id 区分。

## 6. 后端 API

```
# 市场（可安装的源）
GET   /api/v1/market/{kind}                    kind = skill|expert|plugin
                                               → [{id, name, description, source, installed, enabled}]
# 我的（自建 ∪ 已装）
GET   /api/v1/me/skills                        → 自建 + 已装内置技能（含 source/enabled/builtin）
POST  /api/v1/me/skills                        创建自建技能（同名 409）
PUT   /api/v1/me/skills/{name}                 改自建技能（非自建 → 403）
DELETE /api/v1/me/skills/{name}                删自建技能（非自建 → 403）
GET   /api/v1/me/experts                       自建 + 已装内置专家
POST  /api/v1/me/experts                       PUT|DELETE /api/v1/me/experts/{id}
# 能力开关（三类统一）
PUT   /api/v1/me/capabilities/{kind}/{id}      body: {installed?: bool, enabled?: bool}
```

`POST /api/v1/me/capabilities/...` 的安装路径仍走现有 `CapabilityService.can_install`（hidden 条目不可装）；卸载即删记录（自建内容不受影响）。

## 7. 运行时装配

| 位置 | 改动 |
|---|---|
| `SkillService` | 增加 per-user 技能根 `users/<uid>/skills`；扫描顺序：用户根 → 公共层 → 只读根（catalog/插件），名字全局唯一故无遮蔽问题 |
| 技能索引注入 | 运行期可见技能 = 自建 ∪ 已装且 `enabled` 的内置（经 `CapabilityService`） |
| `hidden_skill_names` | 增补"已装但停用"的技能名（与未装同处理） |
| 插件注入 | `ctx.extra["plugins"]` 只注入 installed + enabled + 已配置的插件 |
| 专家 | 用户专家文件读时实例化进 `assistants`（`builtin=false`、`owner=<uid>`、`source_dir`）。实例化触发点只有两处：`GET /api/v1/me/experts`（列我的）与会话装配时按 expert id 解析；两者都走同一个幂等函数（按 `_id` 存在即跳过，照 `seed_experts` 范式）；内置专家播种不变 |
| 会话落盘 | `agent_service._jsonl_path` 由 `sessions/<sid>/events.jsonl` 改为 `users/<uid>/sessions/<sid>/events.jsonl`（`user["sub"]` 在 `chat()` 入参里现成） |
| 数据目录常量 | `workspace.py` 的 `user_root/project_root`、`ProjectService`、`SkillService` 的路径根统一改到 `users/<uid>/...` 与 `public/...`；`catalog_roots()` 的第二根由 `{data_dir}/catalog` 改为 `{data_dir}/public/catalog` |

## 8. 前端

改造 `apps/web/frontend/src/components/catalog/`（能力中心，路由 `/capabilities`）：

- 顶层两栏：**市场** / **我的**（照 jiuwen `activeTab: 'marketplace' | 'my'`）
- 「我的」子页签：`全部 / 已启用 / 已停用 / 内置`（照 `mySkillsSubTab`）
- 卡片动作：未装 → 「安装」；已装且启用 → 「停用」「卸载」；已装且停用 → 「启用」「卸载」；自建 → 「编辑」「删除」
- 三类（技能/专家/插件）同构；插件卡片额外带配置表单入口（走既有 `config_schema`）
- 「我的」为空时给引导文案指向市场

视觉与交互一律照抄 DSH / jiuwen 对应实现（见 CLAUDE.md 参考映射表），不自行发挥。

## 9. 实施纪律

- **不迁移历史数据**：旧的 `{data}/sessions/`、`{data}/workspaces/`、`{data}/skills/` 直接删除重建，不做兼容层、不写迁移脚本。
- 旧的 `sessions/<sid>` 扁平路径读取逻辑一并删掉，不留回退分支。
- 版本号按语义化规则升次版本（向下兼容的新功能）。

## 10. 风险与取舍

| 风险 | 取舍 |
|---|---|
| 三层来源（用户/公共/内置）让"这个技能哪来的"变复杂 | 用 `source: mine|installed|builtin` 标记，前端显式展示；不引入优先级遮蔽（D4 同名拒绝） |
| 用户专家"文件 + DB 实例化"两处状态可能漂移 | 编辑/删除一律"先动文件再刷记录"，失败即整体失败（照 `rename_project`）；启动/列表时按 `source_dir` 幂等校正 |
| 默认全需安装 → 新用户首次进来技能/专家全空 | 首次进入「我的」给引导；管理员可对特定条目开 `default_enabled=true` 作为开箱可用项（策略层，不改代码） |
| 会话目录随用户分层后，单用户删除=删一棵目录树 | 这是收益（归属清晰、易导出），删除用户不在本期实现 |

## 11. 验收标准

1. 新用户登录 → 「我的」为空且有引导 → 市场安装一个技能 → 该技能出现在「我的 · 已启用」，且对话时可用。
2. 停用该技能 → 不出现在运行期技能索引（`hidden_skill_names` 命中），「我的 · 已停用」可见。
3. 自建技能（`users/<uid>/skills/<name>/SKILL.md`）出现在「我的」，可编辑、可删除；与内置同名时创建被拒（409）。
4. 自建专家落 `users/<uid>/experts/<dir>/expert.json`，出现在「我的」，能在新会话里被选中并生效；编辑后文件与 `assistants` 记录一致。
5. 磁盘上 `data_root/{public, users/<uid>/{workspaces,sessions,skills,experts}}` 与设计一致；新会话的事件落在 `users/<uid>/sessions/<sid>/events.jsonl`。
6. 未安装任何插件的用户，`ctx.extra["plugins"]` 为空；装且启用后工具可用，停用后不可用（代码未卸载）。
