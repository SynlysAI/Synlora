# SynlysAgent Plan 3：React 前端与集成验收

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SynlysAgent 的 React 三栏工作台前端（DSH 视觉体系），并连真实后端完成 Plan 1 设计文档 §9 的验收场景。

**Architecture:** `apps/web/frontend` Vite + React 18 + TS SPA。三层：`api/`（fetch 封装 + SSE 流解析）、`stores/`（Zustand：auth/session/chat 三个 store）、`components/`（布局壳 + 消息流 + 右栏 + 管理页）。theme.css 移植 DSH 三层 token。

**Tech Stack:** React 18、TypeScript、Vite、Tailwind CSS 4、Zustand、react-markdown（markdown 渲染）、无 UI 组件库（自建组件 + DSH token）。

**设计文档:** §6（三栏布局、6.2 工程原则、6.3 视觉规格）；**后端契约:** 本文「附录 A」（来自 Plan 2 最终审查）

**环境:** node/npm 已装则用；dev server 5173 端口代理 8005。conda 环境跑后端。

---

## 文件结构

```
apps/web/frontend/
├── package.json / vite.config.ts / tsconfig.json / tailwind.config / index.html
├── src/
│   ├── main.tsx / App.tsx            # 路由（/ 登录→工作台；/admin/* 管理页）
│   ├── theme.css                     # DSH 三层 token（Task 2）
│   ├── api/
│   │   ├── client.ts                 # fetch 封装（Bearer、错误码→异常映射）
│   │   └── sse.ts                    # POST + ReadableStream SSE 解析
│   ├── stores/
│   │   ├── auth.ts                   # token/用户态/#token 提取
│   │   ├── sessions.ts               # 会话列表/当前会话
│   │   └── chat.ts                   # SSE 事件流→消息视图模型
│   ├── components/
│   │   ├── layout/AppShell.tsx       # 三栏 + 拖拽调宽 + 折叠（≤300 行）
│   │   ├── sidebar/                  # SessionList、AssistantPicker、搜索
│   │   ├── chat/                     # MessageList、UserMessage、AssistantMessage、
│   │   │                             # ToolCallCard、StreamCursor、Composer
│   │   ├── rightbar/                 # FilesPanel、OutputPanel、RunInfoPanel
│   │   └── admin/                    # AssistantsAdmin、ModelsAdmin、表单组件
│   └── types.ts                      # 后端契约的 TS 类型
```

---

### Task 1: 脚手架

- [ ] `npm create vite@latest frontend -- --template react-ts`（在 apps/web/ 下）；装 `zustand react-markdown`；Tailwind 4 按官方 vite 集成
- [ ] vite.config.ts：`server.proxy = { "/api": "http://127.0.0.1:8005" }`（dev 免跨域）
- [ ] 验证：`npm run dev` 出 Vite 页；`npm run build` 通过
- [ ] Commit `feat(frontend): Vite 脚手架与代理配置`

### Task 2: theme.css（DSH token 移植）与 AppShell 三栏

- [ ] `theme.css`：按设计 §6.3 写三层 token——`--sa-static-*`（neutral-bluish 蓝调灰阶 50-1000 + blue/amber/green/red；色值照抄 `E:\agent_projects\deepseek-harness\packages\client\ui-theme\src\styles\design-platform.css`）、`--sa-alias-*`（明暗两份：bg/border 四级/label 四级/hover；`body[data-sa-dark-theme]` 切换）、`--sa-specific-*`（bubble/sidebar/code 块）；动效三档 + 缓动；字体栈（CJK 回退 + 代码字体不裸写 monospace）；滚动条 8px 跨引擎双路径；`@supports (corner-shape)` 平滑圆角
- [ ] `AppShell.tsx`：grid 三栏（260px/1fr/320px），左右栏 DragHandle（pointer capture + rAF），右栏三态（隐藏/常态/全屏），<900px 折叠左栏；顶栏（Logo/当前助手/模型选择/用户菜单/主题切换/管理入口）
- [ ] 视觉验证：playwright 截图三栏 + 拖拽 + 暗色主题切换
- [ ] Commit `feat(frontend): DSH 主题 token 与三栏布局壳`

### Task 3: API 层与认证流

- [ ] `api/client.ts`：`api(path, {method, body, form})`——Bearer 注入、401→authStore.logout()、JSON/错误 detail 解析；`types.ts` 按「附录 A」全量类型
- [ ] `api/sse.ts`：`streamChat(sid, text, onEvent)`——fetch POST + ReadableStream 手动解析 SSE 帧（`event:`/`id:`/`data:` 三行块），AbortController 支持停止
- [ ] `stores/auth.ts`：`#token=` 提取→localStorage→replaceState 清 hash；login 页（用户名密码→POST /auth/login）；`GET /auth/me` 启动校验；401 拦截跳登录
- [ ] Commit `feat(frontend): API 客户端、SSE 解析与认证流`

### Task 4: 会话与聊天核心

- [ ] `stores/sessions.ts`：列表（GET）、创建（POST，选 assistant）、改名/归档/删除；`stores/chat.ts`：**视图模型 reducer**——SSE 事件→消息列表（user/message→UserMessage；llm/delta 累积流式气泡；tool/call+tool/result→ToolCallCard（配对 by tool_call_id）；assistant/message 定稿；turn 终止事件置流结束态）；`lastSeq` 持久化（断连恢复用 after_seq）
- [ ] 消息组件：MessageList（自动滚动+置顶锚）、UserMessage、AssistantMessage（react-markdown + 代码块样式）、ToolCallCard（状态图标/参数折叠/结果折叠/truncated 标记）、StreamCursor
- [ ] Composer：多行输入、附件选择（POST /files 后作为上下文提示文本附加）、发送/停止（cancel run）、Enter 发送 Shift+Enter 换行、进行中禁发
- [ ] 断连恢复：进入会话时 GET events?after_seq=-1 全量重建视图模型
- [ ] **连真实后端验证**（mock 不再够用：后端起 dev token 模式 + 手配一个真实 provider，或临时 monkeypatch——验收时用真实 OpenAI 兼容服务）
- [ ] Commit `feat(frontend): 会话与聊天核心（消息流/工具卡片/Composer）`

### Task 5: 左栏与右栏

- [ ] 左栏：新对话按钮、助手切换（下拉/列表，显示 avatar+description）、会话列表（按更新时间倒序、搜索过滤、重命名/删除右键菜单、归档分组）
- [ ] 右栏：FilesPanel（上传拖拽区+列表+下载+删除）、OutputPanel（列 output/ 产物——经 files 接口无 output 视图，V1 展示 chat 中 tool 产物的下载链接即可：ToolCallCard 的 cwd 提示 + file.list 结果展示）、RunInfoPanel（当前/最近 run 的模型/步数/token——从 turn 事件聚合）
- [ ] Commit `feat(frontend): 左栏会话管理与右栏工作区面板`

### Task 6: 管理页

- [ ] `/admin/models`：列表（has_key 徽标）+ 新建/编辑表单（name/base_url/api_key/model_id/enabled）+ 连通性测试按钮（loading→ok/error/latency）
- [ ] `/admin/assistants`：列表 + 表单（name/avatar/description/system_prompt 多行/模型选择/工具白名单多选（六项 checkbox）/knowledge_base_ids 预留隐藏）+ builtin 徽标与删除保护
- [ ] admin 路由守卫（role!=='admin' 隐藏入口 + 403 提示）
- [ ] Commit `feat(frontend): 管理页（模型与助手管理）`

### Task 7: 集成验收（设计 §9 五场景）

- [ ] 后端配真实 OpenAI 兼容 provider（用户提供或 dev 模式测试）→ playwright 有头模式逐场景走查：
  1. 管理员初始化（模型+助手种子可见）
  2. 完整闭环（数据分析助手+CSV上传+python.run+图表 PNG+右栏可见）
  3. 会话恢复（刷新重放）
  4. 中途停止
  5. 沙箱边界（os.system 拒/死循环超时/输出截断）
- [ ] 每场景截图留档 `docs/superpowers/acceptance/`
- [ ] 修验收发现的问题（逐个修复提交）
- [ ] **Commit** `feat(frontend): 集成验收通过`

### Task 8: 收尾

- [ ] 根 README.md（项目总览：架构图、三包说明、启动指南、部署、AI4MS 对接）
- [ ] 后端静态托管前端（main.py StaticFiles 挂 dist——单端口部署）；`npm run build` 产物验证
- [ ] 版本号 0.1.0 同步（harness/web/frontend 三处）
- [ ] **Commit** `docs: 项目 README 与单端口部署`

---

## 完成标准（Plan 3 DoD / 项目 V1 DoD）

- [ ] `npm run build` 通过；单端口 8005 托管前端可访问
- [ ] 设计 §9 五个验收场景 playwright 走查通过（截图留档）
- [ ] 全部测试绿（harness 75 + web 87）
- [ ] 三栏布局/暗色主题/拖拽调宽可用；DSH 视觉体系落地

## 附录 A：后端契约摘要（Plan 2 最终审查产出，详版见该审查记录）

**认证**：Bearer header；`#token=` 提取→localStorage；`POST /auth/login {username,password}→{token,username,role}`；`GET /auth/me`
**SSE**：`POST /api/v1/sessions/{sid}/messages {text}` → 帧三行 `id:`/`event:`/`data:`；终止=`turn/end` 或 `turn/aborted`；事件 payload 见 harness EventType；`run_id` 从 `turn/start` 取；停止 `POST /api/v1/runs/{run_id}/cancel`；断连恢复 `GET /api/v1/sessions/{sid}/events?after_seq=N`（前端持久化 lastSeq）
**错误码**：401 跳登录｜403 守卫｜404 不存在/非本人｜422 校验（detail）｜409 冲突｜413 文件/配额｜429 会话互斥或并发上限
**文件**：`POST /api/v1/files`（multipart 字段 `files`）→ 201 全成功 / 200 部分 `{status, results:[{ok,file?|error,code?}]}`；`GET /files`、`GET /files/{id}/download`、`DELETE /files/{id}`
**其他**：models 永无 key 字段（`{_id,name,base_url,model_id,enabled,has_key}`）；助手 `model_name` 可能是 `(已停用)`/`(已删除)`/null；首条消息自动生成标题
