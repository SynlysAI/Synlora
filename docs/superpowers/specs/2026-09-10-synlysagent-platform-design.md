# SynlysAgent 科研智能体平台设计文档

- 日期：2026-09-10
- 状态：已与用户逐节评审通过
- 仓库：`E:\agent_projects\SynlysAgent\`（GitHub 组织 SynlysAI）

## 1. 背景与目标

AI⁴MS 生态已有多个子平台（Spec_Agent 谱图解析、Poly_Agent 高分子研发、RAGPortal/WeKnora 知识库、SmartAccess 仪器接入、SpecLabOS 实验管理等），Poly_Agent 内有一版工作台对话 Agent，但前端是 4785 行巨型 Vue 单文件组件、界面体验差、模型配置写死在代码里。

本项目新建一个**独立的科研智能体平台**：类 Claude/DeepSeek Harness 的三栏 Web 工作台，通过对话形式使用各助手（谱图解析、高分子研发、文件分析等），每个用户拥有独立沙箱（文件工作区 + Python 执行），管理员可在页面上配置模型与助手。现有 AI⁴MS 能力后续通过 Tool Registry 接入。

### 架构借鉴来源（对 E:\agent_projects 三个框架的调研结论）

| 借鉴对象 | 借鉴内容 |
|---|---|
| deepseek-harness（TS） | Tool 四段管线（pre-execute/execute/post-execute/result）、event-sourced session（事件投影为消息，"model-visible ⟺ logged" 不变量）、turn/step 双层循环、"Plugins, not loop changes" 原则 |
| pi（TS） | steering/followUp 双消息队列（中途插话）、Extension 生命周期钩子、Session 树思想（V2 演进）、小内核哲学 |
| jiuwenswarm（Python/FastAPI） | 单进程 FastAPI + React 的产品组织方式、管理员页配模型的产品形态（其 ReAct 内核在外部包 openjiuwen 中，本地无源码，循环本体自研） |

## 2. 需求与 V1 范围

### 2.1 核心需求（用户确认）

1. 独立 Web 服务页面（单进程，端口 8005，PM2 部署，与其他子平台一致）
2. 对话式界面，类 DSH 三栏工作台
3. 多 Agent 助手：**管理员在页面创建/编辑助手**（名称、头像、系统提示词、绑定模型/工具/知识库），普通用户选用
4. 模型管理：**管理员在页面配置自定义 OpenAI 兼容 provider**（API URL、Key、model_id），不做外部厂商模板；普通用户对话时选择模型
5. 每用户独立沙箱：文件工作区（上传/浏览/下载）+ `python.run` 受限执行
6. 知识库：V1 预留助手绑定知识库的结构 + `knowledge.search` 示例工具（接口形状按 WeKnora 设计），真实接入后续
7. 认证：沿用 AI⁴MS 共享认证（HMAC-SHA256 token + MongoDB `ai4ms.users`，门户 `#token=xxx` 跳转登录）
8. V1 内置 6 个工具：`python.run`、`file.read`、`file.write`、`file.list`、`knowledge.search`（mock）、`http.request`（受限 GET）
9. 不做多 Agent 编排、外层 Task Loop（留 extension 钩子位，第二阶段）

### 2.2 关键决策记录

| 决策点 | 结论 |
|---|---|
| 项目名 | SynlysAgent（仓库名），内部核心运行时层叫 harness |
| 架构方案 | 方案 C：单体起步 + 包边界预留（monorepo，harness 为纯 Python 包，web 为其宿主） |
| V1 验证方式 | 纯框架 + 内置示例工具，不接 AI⁴MS 真实能力 |
| 前端 | React 18 + TS + Vite（不用 Vue，吸取 Poly_Agent 前端教训） |
| Python 环境 | 新建 conda 环境 `synlysagent`（Python 3.12） |
| 存储 | 双后端：生产 MongoDB / 开发可选 SQLite，配置文件切换 |

## 3. 总体架构

### 3.1 Monorepo 结构（方案 C）

```
E:\agent_projects\SynlysAgent\
├── packages/
│   └── synlys-harness/              # 纯 Python Agent 运行时包（零 FastAPI 依赖）
│       ├── pyproject.toml
│       └── src/synlys_harness/
│           ├── core/                # agent loop、session 事件、scope
│           ├── tools/               # tool registry + 执行管线
│           ├── models/              # LLM provider 路由（OpenAI 兼容）
│           └── extensions/          # 扩展钩子（生命周期事件）
├── apps/web/
│   ├── backend/                     # FastAPI 应用（唯一进程）
│   │   └── app/
│   │       ├── api/v1/              # auth/assistants/models/sessions/files/chat
│   │       ├── services/            # 调用 harness 的胶水层
│   │       ├── core/                # 配置、共享认证（HMAC token）
│   │       └── db/                  # DocumentStore 抽象 + repositories
│   └── frontend/                    # React 18 + TS + Vite 三栏工作台
├── data/                            # 运行时数据（gitignore）：workspaces/、sessions/、sqlite
├── docs/superpowers/                # 设计文档与实施计划
├── ecosystem.config.cjs             # PM2 部署
└── README.md
```

### 3.2 架构原则（DSH 思想落地）

1. **harness 不 import FastAPI**：web 只是它的一个宿主；未来 CLI、Celery worker、双进程 RPC（harness 包外加 RPC server 层）都能直接复用
2. **"model-visible ⟺ logged"**：所有进入 LLM 上下文的内容都先落 session 事件日志
3. **工具 = 声明 + 管线**：注册工具只写 schema 和 execute，权限/审计/超时全走统一管线
4. **AI⁴MS 工具接入零侵入**：后续接入只是在 registry 注册新 tool（HTTP/MCP adapter），loop 与管线不改

### 3.3 技术栈

| 层 | 选型 |
|---|---|
| harness | Python 3.12、pydantic v2、openai SDK、anyio |
| web 后端 | FastAPI、uvicorn、sse-starlette、aiosqlite（开发存储）、motor（生产存储）、cryptography（key 加密） |
| 前端 | React 18、TypeScript、Vite、Tailwind CSS、shadcn/ui、Zustand |
| 认证 | 共享 HMAC token（PBKDF2 密码，复用 `ai4ms.users`） |
| 环境 | conda `synlysagent` |

## 4. harness 核心设计

### 4.1 Agent Loop（turn/step 双层）

```
RunSession.run(user_message)
  └── Turn（一轮用户输入 = N 个 step）
        ├── Step: 组装 system prompt + 上下文 → 调 LLM（流式）
        │     ├── 纯文本 → answer_delta 事件 → turn 结束
        │     └── tool_calls → 逐个过 Tool 管线 → tool_* 事件 → 下一 step
        ├── steering 队列：用户中途插话 → 注入下一 step
        └── max_steps=25 上限；每 step 检查 cancel token（前端"停止"→ asyncio cancel）
```

V1 不做多 Agent 编排与外层 Task Loop。

### 4.2 Event-sourced Session

- 会话 = 事件流；事件 envelope：`{seq, type, payload, ts}`
- 事件类型（V1）：`turn/start`、`user/message`、`llm/delta`、`assistant/message`、`tool/call`、`tool/result`、`turn/end`、`turn/aborted`、`error`
- `derive_messages(events) -> list[Message]`：事件流投影为 LLM 消息（DSH foldSurface 思想）
- 持久化：JSONL 文件（`data/sessions/{session_id}/events.jsonl`，回放源）+ DB 副本（列表页快查，可重建）
- 价值：刷新重放恢复完整对话；审计/回放/evaluate/会话 fork 未来都在事件流上长

### 4.3 Tool Registry + 四段管线（核心扩展点）

```python
@tool(name="python.run", description="...", schema={...}, timeout_s=60, permission="sandbox")
async def run_python(ctx: ToolContext, code: str) -> ToolResult: ...
```

管线四段（extension 可挂载）：`pre_execute`（参数校验/权限：deny|allow|ask_user）→ `execute`（around 包装：超时/异常捕获）→ `post_execute`（结果改写/截断）→ `emit_result`（落事件 + 推 SSE）。

V1 内置 6 工具：

| 工具 | 说明 |
|---|---|
| `python.run` | 用户沙箱受限执行（见 5.2） |
| `file.read` / `file.write` / `file.list` | 限用户工作区内路径 |
| `knowledge.search` | mock 实现，返回固定结构（接口形状按 WeKnora `/knowledge-chat` 设计，便于后续替换） |
| `http.request` | 受限 GET（域名白名单为服务端配置文件静态配置，V1 不做页面管理），为接 AI⁴MS API 预演 |

### 4.4 模型路由

```python
class ModelProvider(BaseModel):   # 存 DB，管理员 CRUD
    name: str                     # 显示名
    base_url: str                 # OpenAI 兼容 endpoint
    api_key: str                  # web 层加密后存储
    model_id: str
    enabled: bool
```

harness 用 openai SDK 按实例化时的 provider 配置切换 `base_url`，要求模型支持 function calling。

### 4.5 Extension 钩子（V1 最小集）

4 个钩子位：`on_session_start`、`before_llm_call`（可改 prompt）、`on_tool_event`、`on_session_end`。Python 函数注册，不做动态加载（第二阶段按需扩展）。

### 4.6 Assistant → 运行时组装

管理员配置的助手（提示词/模型/工具白名单/知识库绑定）+ 用户沙箱上下文 → `AgentConfig` → `RunSession`。助手是配置不是代码。后端首次启动时若 assistants 集合为空，自动 seed 两个示例助手（科研助手：全工具；数据分析助手：仅 python/file 工具）。

## 5. Web 后端设计

### 5.1 API 面（REST + SSE，前缀 `/api/v1`）

| 模块 | 端点 |
|---|---|
| 认证 | `POST /auth/login`、`GET /auth/me`（兼容共享 token + URL hash 登录；admin 角色读 `ai4ms.users`） |
| 助手 | `GET/POST/PATCH/DELETE /assistants`（管理员 CRUD，普通用户只读） |
| 模型 | `GET /models`（全员）；`POST/PATCH/DELETE /models`（管理员）；`POST /models/{id}/test`（真实连通性测试） |
| 会话 | `GET/POST /sessions`、`PATCH/DELETE /sessions/{id}`（改名/归档/删除） |
| 对话 | `POST /sessions/{id}/messages`（SSE 流式，事件即 session 事件）、`POST /runs/{run_id}/cancel` |
| 文件 | `GET/POST /files`、`GET /files/{id}/download`、`DELETE /files/{id}` |
| 回放 | `GET /sessions/{id}/events` |

### 5.2 用户沙箱

```
data/workspaces/{user_id}/
├── files/      # 用户上传 + file.* 工具根目录
├── output/     # agent 产物（图表/报告）
└── tmp/        # python.run 的 cwd
```

`python.run` 安全限制：子进程 `[sys.executable, "-I"]`（隔离模式）；cwd 锁定 tmp/；环境变量白名单；stdout/stderr 合并截断 64KB；超时默认 60s；产物写 output/；每用户磁盘配额 1GB；并发执行信号量 2/用户。

### 5.3 双后端存储

```
STORAGE_BACKEND = mongodb | sqlite   # 默认 sqlite
MONGODB_URI / SQLITE_PATH            # 对应配置
```

`DocumentStore` 协议（get/insert/update/delete/list，字段过滤排序）双实现：

- `MongoStore`（motor，文档直存；认证库固定走 Mongo `ai4ms.users`）
- `SqliteStore`（aiosqlite；每 collection 一张表：`_id TEXT PRIMARY KEY, doc JSON TEXT` + 声明的索引字段提取为真实列如 user_id/session_id/seq）

实体 repository（assistants/providers/sessions/events/files/runs）建立在 DocumentStore 之上，业务代码不感知后端。provider 的 api_key 用 Fernet 加密（SECRET 来自服务端配置），存储与后端无关。

数据集合（业务库 `synlys_agent`）：

```
assistants { name, avatar, description, system_prompt, model_provider_id,
             tool_whitelist[], knowledge_base_ids[], created_by, timestamps }
providers  { name, base_url, api_key_enc, model_id, enabled, timestamps }
sessions   { user_id, assistant_id, title, archived, message_count, timestamps }
events     { session_id, seq, type, payload, ts }        # JSONL 的 DB 副本
files      { user_id, path, size, mime, sha256, timestamps }
runs       { session_id, user_id, status, usage, started_at, ended_at }
```

开发环境 SQLite 时认证的处理：内置一个开发用本地用户表（仅 sqlite 后端启用），避免开发时也依赖 Mongo；生产（mongodb 后端）严格走 `ai4ms.users` 共享认证。

### 5.4 会话运行编排

- 每次用户消息创建一个 `run` 记录；harness `RunSession` 由 web 的 SessionService 持有（进程内字典 + run 注册表，支持 cancel）
- SSE 用 `sse-starlette`；断连后 run 继续执行完落盘，前端重连带 `Last-Event-ID` 从事件流补齐

## 6. 前端设计

### 6.1 三栏工作台

```
┌──────────┬────────────────────────────┬──────────────┐
│ 左栏 260px│        中间 Chat           │ 右栏 320px    │
│ [+新对话] │  消息流（用户/助手气泡、     │ （可折叠）     │
│ 助手切换  │  工具调用卡片、流式光标、    │ 📁 文件       │
│ 搜索     │  停止/重试/复制）           │ 💻 沙箱产物   │
│ 历史会话  │  底部 Composer：           │ ⚙ 运行信息    │
│ (分组)    │  附件+输入+发送             │  （模型/步数/ │
│          │                            │   token 用量）│
├──────────┴────────────────────────────┴──────────────┤
│ 顶栏：Logo/当前助手/模型选择/用户菜单（管理入口）        │
└──────────────────────────────────────────────────────┘
```

管理页（admin 路由）：助手管理、模型管理（表单 CRUD + 连通性测试按钮）。

### 6.2 工程原则

- 单组件 ≤ 300 行；消息类型独立组件（UserMessage/AssistantMessage/ToolCallCard/FileChip）
- SSE 用 fetch + ReadableStream 解析（便于带 token header）
- 样式：shadcn/ui 中性灰 + 单主色、深浅主题、元信息默认折叠 hover 展开——区别于 Element Plus 控制台感
- 状态：Zustand（会话列表/当前会话/SSE 流三个 store）

## 7. 错误处理

| 层 | 策略 |
|---|---|
| LLM 调用 | 超时/限流重试 1 次；失败发 `error` 事件（不静默），会话保留可重试 |
| 工具执行 | 异常捕获为 `tool/result(error)` 事件（LLM 可见并自我修正），不中断 run |
| SSE 断连 | run 后台执行完落盘；重连后按 `Last-Event-ID` 补齐 |
| 认证失败 | 401 统一响应，前端跳登录 |

## 8. 测试策略（只测核心）

- harness pytest 单测：`derive_messages` 投影、管线四段顺序/拦截、max_steps 与 cancel、mock LLM 完整 turn
- web 后端 2 条关键链路：SSE 对话端到端（mock provider）、文件上传→python.run→产物落盘
- 前端不写测试，playwright 人工过验收场景

## 9. V1 验收场景

1. 管理员初始化：登录 → 模型管理页添加 OpenAI 兼容 provider（测试按钮真实调用）→ 助手页见两个种子助手（科研助手/数据分析助手）
2. 完整闭环：选"数据分析助手" → 上传 CSV → "分析分布并画图" → 工具卡片（python.run）→ 统计结论 + output/ 生成 PNG → 右栏可见可下载
3. 会话恢复：刷新 → 会话列表在 → 点开 → 消息与工具卡片从事件流完整重放
4. 中途停止：长任务点"停止" → 立即中断，事件流记录 aborted，状态一致
5. 沙箱边界：`os.system` 类调用被拒；死循环 60s 超时被杀；输出 >64KB 截断有提示

## 10. V1 明确不做

多 Agent/编排、外层 Task Loop、SKILL.md 动态技能、WeKnora 真实接入、AI⁴MS 各子平台工具接入、Docker 部署（PM2 即可）、图表交互渲染（图片展示）、会话 branch/fork（V2）、移动端适配。

## 11. 后续演进路线（非 V1）

1. **AI⁴MS 工具接入**：Spec_Agent（`/api/v1/tasks/{nmr,gpc,...}` 异步任务 → 工具化轮询模式）、WeKnora 检索（替换 knowledge.search mock）、SpecLabOS 设备/工作流
2. **MCP adapter**：Tool Registry 加 MCP 来源
3. **多 Agent / SwarmFlow**：harness 加 subagent provider + 声明式 team 装配（借鉴 jiuwenswarm）
4. **SKILL.md 动态技能**（借鉴 jiuwenswarm skill_manager）
5. **双进程演进**：harness 包加 RPC server 层 → headless 运行时
