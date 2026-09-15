# Synlora 项目说明（内部代号 SynlysAgent：目录/包/conda 环境沿用早期代号）

科研智能体平台：类 ChatGPT/Claude 的三栏 Web 工作台（对话式使用科研助手 + 用户沙箱 + 知识库），WeKnora 知识检索已实接，后续接入 AI⁴MS 生态能力（Spec_Agent/Poly_Agent/SpecLabOS 等）。

## 核心设计纪律（最重要）

**所有功能与 UI 设计必须参考以下三个本地参考项目，禁止自行发挥。**

动手写任何代码（尤其前端）之前，**必须先读参考项目的对应实现**，逐样式/逐结构照抄后再映射到本项目的技术栈与 token：

- **UI 视觉/布局/交互 → 参考 DeepSeek Harness**：`E:\agent_projects\deepseek-harness\packages\client\`
- **对话内容展示模式（思考/工具/用时）→ 参考 JiuwenSwarm**：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\`
- **Agent 运行时架构 → 参考 DSH 架构思想 + Pi 小内核**：`E:\agent_projects\deepseek-harness\packages\core\`、`E:\agent_projects\pi\packages\`

历史教训：本项目曾因"参考 token 但没参考组件实现"被用户批评界面丑、思考/工具展示不符预期，返工多轮。结论：**照抄 > 发挥**。

## 参考映射表（改哪块就先读哪块）

| 本项目模块 | 参考源（照抄对象） |
|---|---|
| 主题 token（`src/theme.css`，前缀 `--sa-*`） | DSH `ui-theme/src/styles/design-platform.css`（三层 static/alias/specific + 明暗映射）、`scrollbar.css`、`base.css`（字体栈/动效三档） |
| 三栏布局/拖拽调宽/右栏三态（`AppShell.tsx`） | DSH `ui-layout/src/client/AppFrame.tsx`（pointer capture + rAF 节流 + clamp） |
| 消息流排版（文档流、turn 头部行、头像在回答上方） | Jiuwen `ChatPanel/MessageList.tsx`（chat-avatar 头部行 + turn-elapsed 用时行） |
| 思考折叠面板（`ReasoningPanel.tsx`） | Jiuwen `ChatPanel/MessageList.tsx` 的 reasoning-panel + `index.css` 1784-1852 行（无背景、左竖线、暗字、扫光 shimmer、grid-rows 平滑折叠、完成自动收起） |
| 工具调用行（`ToolCallCard.tsx`） | Jiuwen `ChatPanel/ToolGroupDisplay.tsx` 的 tool-tree-item + `index.css` 1944-2037 行（单行分类图标+动作描述、hover 箭头、行内下拉详情卡） |
| 左栏会话列表/新会话按钮、底部输入框（模型选择内嵌/蓝色圆形发送） | DSH 左栏与 Composer 实际界面（描边新会话按钮、单行会话项+右对齐时间、"展开其余 N 个会话"折叠） |
| 回复尾部操作（复制/任务用时/token 用量） | Jiuwen 消息尾部元信息行（`N in / N out / total` 格式） |
| 问答卡（`AskUserCard.tsx`，勾选+多题翻页） | Jiuwen `InteractionSlot/InteractionPrompt.tsx`（勾选不即发、取消/跳过/下一步、Other 自由输入、答案拼文本回传）；**位置**另照 `InteractionSlot/index.tsx`：待作答由 `InteractionSlot.tsx` 吸附在输入框正上方（不占消息流），作答后仍留过程区随「任务用时」chip 折叠 |
| 审批卡（ASK_USER 强制审批，复用问答回路） | Jiuwen `permission_interrupt` rail 模式（执行前打断 + 审批 payload 人工确认） |
| 文件交付卡（`FileSendCard.tsx`，图片内联预览） | Jiuwen `ChatPanel/MessageItem.tsx` 的 FileDownloadList isImage 分支（卡内 img、鉴权 blob→objectURL） |
| harness 工具管线/事件会话/loop | DSH `core/tools`（四段管线）、`core/session`（事件→消息投影）、pi `packages/agent/src/agent.ts`（steering/钩子） |

## 项目结构

```
packages/synlys-harness/   # 纯 Python Agent 运行时（零 Web 依赖，零内置内容）：事件会话/工具管线/沙箱/LLM 流式/loop
apps/web/backend/          # FastAPI 宿主（8005）：双后端存储/AI4MS 兼容认证/SSE 对话/文件工作区 + catalog/（内置内容）
apps/web/frontend/         # React 19 + TS + Tailwind 4 + Zustand 三栏工作台
docs/superpowers/          # 设计文档（specs）/ 实施计划（plans）/ 验收报告（acceptance）
```

## 关键架构约定

- 事件流是唯一事实源：所有进入 LLM 上下文的内容都先落 session 事件；`derive_messages()` 投影；瞬态事件（llm/delta、reasoning/delta）只推 SSE 不落盘
- harness 不 import FastAPI；web 只是宿主；接入契约见 `docs/superpowers/plans/2026-09-10-synlysagent-02-web-backend.md` 文首 10 条
- 认证与 AI⁴MS 门户逐字兼容（HMAC token + `ai4ms.users`，`#token=` 跳转）
- python.run 执行器抽象（`tools/sandbox.py`）：local（-I 隔离/环境白名单/超时/截断，事故围栏）与 docker（临时容器：workspace 单目录挂载 /workspace、断网、资源限额、非 root、跑完即删）两实现；宿主经 `ctx.extra.code_executor` 注入、`resolve_executor()` 探测解析（不可用时 strict 拒绝或回退 local-weak 标记）；镜像构建见 `docker/sandbox/`
- 一切皆插件：子平台接入 = `apps/web/backend/catalog/plugins/<id>/`（`plugin.json` 声明配置 schema/工具模块/技能/专家模板）；宿主通用框架 `app/plugins/`（loader 扫描 + config_store 加密落库 + PluginService 编排 + api 管理端点）；插件配置经管理页填写落库（不进 settings.py/.env），运行期按命名空间注入 `ctx.extra["plugins"]`；插件技能经 SkillService 额外技能根提供；新增插件不改主框架与 harness
- 内置内容统一在宿主 `apps/web/backend/catalog/`，按类型分目录（`experts/<dir>/expert.json`、`skills/<name>/SKILL.md`、`plugins/<id>/plugin.json`），**位置即类型、加目录即扩展**，由 `app/catalog/loader.py::scan_catalog` 一次扫入；**harness 零内容（内容归宿主、机制归 harness）**；`catalog/skills` 是只读技能根，`{data_dir}/public/skills` 是可写公共层（同名公共层优先）；`catalog/` 是数据目录非 Python 包，非 editable 部署须与 `app/` 同级同放
- 运行数据按用户分层：`{data_dir}/public/{skills,catalog}`（公共层）+ `{data_dir}/users/<uid>/{workspaces,sessions,skills,experts}`（用户层）；内置内容始终单一来源（repo 的 `catalog/`），用户"安装"只写记录不复制文件；会话工作根：绑定项目 = 项目目录，未绑定（不选工作区，project_id=null）= `sessions/{sid}/workspace`（files/output/tmp 同构，python.run cwd/沙箱挂载/上传落点都在会话工作区，`events.jsonl` 在会话根、与模型和文件树视野隔离，删除会话整目录移除；**发消息不回落活跃项目**、失效绑定清为 null）
- 能力目录（市场）：内置项（专家/技能/插件）由 `apps/web/backend/catalog/{experts,skills,plugins}/` 扫描枚举（`app/catalog/loader.py::scan_catalog`，三类条目统一来自它）；管理员用 `catalog_policy` 配「可见性（public/hidden）+ 内置（`default_enabled`）」：hidden = 全局隐藏（装过也踢出），内置 = **全员自动可用、用户侧只读**（不可安装/启停/卸载，`PUT /me/capabilities` 409、历史安装记录被判定忽略），非内置 = 市场可见需用户安装；缺省 = public + 非内置；用户安装只写 `user_capabilities` 记录（**不复制文件**）；运行期可见集由 `CapabilityService` 按用户计算（插件工具/技能索引/专家列表/`ctx.extra["plugins"]` 统一走它，**内置工具不受影响**，判定口径 = 非 hidden 且（内置 或 已装且启用））；技能过滤用黑名单口径（`hidden_skill_names`），公共技能目录里管理员自建的技能始终可见；插件配置分公共（`plugin_configs`，部署级）与个人（`user:<uid>:<plugin_id>`）两层，个人优先；管理后台为整页左导航（常规/模型服务/助手管理/技能管理/插件），用户侧「能力中心」为独立整页 `/capabilities`
- 用户安装后可启用/停用（`user_capabilities.enabled`，停用优先于默认启用）；用户可自建技能与专家（落各自 `users/<uid>/`，技能同名全局唯一），内置条目不可编辑（想定制请自建换名）；用户侧能力中心为「市场 / 我的」两栏（`/capabilities`）：市场装/卸/启停，我的子页签过滤 + 自建技能/专家增删改（改造计划 `docs/superpowers/plans/2026-09-15-synlysagent-11-capability-center-ui.md` 已全部完成）
- 工具强制审批：`Permission.ASK_USER` 在管线 pre-execute 打断，宿主 `approval_handler` 复用 ask/user 事件与 answer 回路，fail-closed（SpecLabOS 类工具声明即生效）
- 插话（steering）双语义：模型还有 step 则下个边界注入本轮；turn 正常结束时残留插话经 `take_queued_turn()` 自动转为下一轮续跑（取消/失败路径丢弃）
- 插件会话级开关（**默认全关**）：会话文档 `enabled_plugins`（null/[] = 本会话不启用任何插件；列表 = 只启用这些，切换即 PATCH 落库刷新不丢），`+ 号 → 插件` 面板切换（草稿态暂存 sessions store、随建会话写入）；chat 装配按它收窄插件**工具、配置注入、技能索引**，未启用插件的播种专家按未选处理（persona 不注入），与用户级可见集取**交集**（勾选不能放大可见性，内置工具不受影响；「内置」插件在会话级同样默认关——它保证的是用户级可用与不可卸载，非会话自动加载）；技能字典带 `source/plugin` 标记供过滤（技能管理页滤 plugin 来源，统一在插件页查看附属技能/专家/工具）
- 运行时（ActiveRun/SSE 队列/ask future）为单进程内存态：uvicorn 必须 workers=1 单实例部署，多副本会破坏 steer/answer/cancel

## 环境与测试

- conda 环境 `synlysagent`（Python 3.12）；前端 npm（apps/web/frontend）
- 测试：`cd packages/synlys-harness && conda run -n synlysagent python -m pytest -v`；web 同理（`apps/web/backend`）
- 启动：`cd apps/web/backend && conda run -n synlysagent python -m uvicorn app.main:app --port 8005`（前端 dist 由后端静态托管，单端口）；开发模式登录用 DEV_AUTH_TOKEN（.env 中 devtok）
- UI 改动后 `npm run build` 通过即交付，真机验证由用户自测（不跑 playwright 验证循环）

## 后续路线

对齐 backlog 文档 `docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md`（含各项触发条件）：

沙箱 Docker 化 ✅（0.4.0，`SANDBOX_MODE=docker`）→ AI⁴MS 工具接入 ✅ 首期（Spec_Agent 核磁三件套，插件化）→ 统一 Job 注册表 + 5 种谱图异步任务 → SpecLabOS 经强制审批 → 跨会话长期记忆 → 工具结果 spill → MCP adapter → 多 Agent/SwarmFlow（参考 jiuwenswarm agents/swarm 声明式装配）→ 动态技能市场 / headless 运行时（用量驱动，暂缓）
