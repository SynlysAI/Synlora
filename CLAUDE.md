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
| 问答卡（`AskUserCard.tsx`，勾选+多题翻页） | Jiuwen `InteractionSlot/InteractionPrompt.tsx`（勾选不即发、取消/跳过/下一步、Other 自由输入、答案拼文本回传） |
| 审批卡（ASK_USER 强制审批，复用问答回路） | Jiuwen `permission_interrupt` rail 模式（执行前打断 + 审批 payload 人工确认） |
| 文件交付卡（`FileSendCard.tsx`，图片内联预览） | Jiuwen `ChatPanel/MessageItem.tsx` 的 FileDownloadList isImage 分支（卡内 img、鉴权 blob→objectURL） |
| harness 工具管线/事件会话/loop | DSH `core/tools`（四段管线）、`core/session`（事件→消息投影）、pi `packages/agent/src/agent.ts`（steering/钩子） |

## 项目结构

```
packages/synlys-harness/   # 纯 Python Agent 运行时（零 Web 依赖）：事件会话/工具管线/沙箱/LLM 流式/loop
apps/web/backend/          # FastAPI 宿主（8005）：双后端存储/AI4MS 兼容认证/SSE 对话/文件工作区
apps/web/frontend/         # React 19 + TS + Tailwind 4 + Zustand 三栏工作台
docs/superpowers/          # 设计文档（specs）/ 实施计划（plans）/ 验收报告（acceptance）
```

## 关键架构约定

- 事件流是唯一事实源：所有进入 LLM 上下文的内容都先落 session 事件；`derive_messages()` 投影；瞬态事件（llm/delta、reasoning/delta）只推 SSE 不落盘
- harness 不 import FastAPI；web 只是宿主；接入契约见 `docs/superpowers/plans/2026-09-10-synlysagent-02-web-backend.md` 文首 10 条
- 认证与 AI⁴MS 门户逐字兼容（HMAC token + `ai4ms.users`，`#token=` 跳转）
- python.run 是事故围栏非安全边界（-I 隔离/环境白名单/超时/截断）；多用户公网部署前必须升级 Docker 沙箱（backlog 1）
- 工具强制审批：`Permission.ASK_USER` 在管线 pre-execute 打断，宿主 `approval_handler` 复用 ask/user 事件与 answer 回路，fail-closed（SpecLabOS 类工具声明即生效）
- 插话（steering）双语义：模型还有 step 则下个边界注入本轮；turn 正常结束时残留插话经 `take_queued_turn()` 自动转为下一轮续跑（取消/失败路径丢弃）
- 运行时（ActiveRun/SSE 队列/ask future）为单进程内存态：uvicorn 必须 workers=1 单实例部署，多副本会破坏 steer/answer/cancel

## 环境与测试

- conda 环境 `synlysagent`（Python 3.12）；前端 npm（apps/web/frontend）
- 测试：`cd packages/synlys-harness && conda run -n synlysagent python -m pytest -v`；web 同理（`apps/web/backend`）
- 启动：`cd apps/web/backend && conda run -n synlysagent python -m uvicorn app.main:app --port 8005`（前端 dist 由后端静态托管，单端口）；开发模式登录用 DEV_AUTH_TOKEN（.env 中 devtok）
- UI 改动后 `npm run build` 通过即交付，真机验证由用户自测（不跑 playwright 验证循环）

## 后续路线

对齐 backlog 文档 `docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md`（含各项触发条件）：

沙箱 Docker 化（多用户部署前置）→ AI⁴MS 工具接入（统一 Job 注册表先行：异步状态机 + 完成通知；Spec_Agent → SpecLabOS 经强制审批）→ 跨会话长期记忆 → 工具结果 spill → MCP adapter → 多 Agent/SwarmFlow（参考 jiuwenswarm agents/swarm 声明式装配）→ 动态技能市场 / headless 运行时（用量驱动，暂缓）
