# SynlysAgent 科研智能体平台

类 Claude/DeepSeek Harness 的科研智能体 Web 平台：三栏对话工作台，通过对话形式使用各助手（谱图解析、高分子研发、数据分析、文件处理等），每个用户拥有独立沙箱（文件工作区 + 受限 Python 执行），管理员可在页面上配置模型与助手。作为 AI⁴MS 生态的独立子平台部署，现有 AI⁴MS 能力后续通过 Tool Registry 接入。

- 版本：V1（0.1.0）
- 设计文档：[docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md](docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md)
- 验收报告：[docs/superpowers/acceptance/2026-09-10-验收报告.md](docs/superpowers/acceptance/2026-09-10-验收报告.md)

## 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  前端 React 19 三栏工作台（apps/web/frontend）                 │
│  左栏：助手/会话    中栏：消息流+工具卡片+SSE流式   右栏：文件/沙箱产物/运行信息 │
└──────────────────────────┬──────────────────────────────────┘
                     REST + SSE（/api/v1）
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI 单进程宿主（apps/web/backend，端口 8005）             │
│  认证/HMAC token · 助手/模型管理 · 会话编排 · 文件工作区       │
│  + 静态托管前端构建产物（单端口部署）                           │
└──────────────────────────┬──────────────────────────────────┘
                           │ 进程内调用（零 FastAPI 依赖）
┌──────────────────────────▼──────────────────────────────────┐
│  synlys-harness 核心运行时（packages/synlys-harness）         │
│  Agent Loop（turn/step 双层）· Event-sourced Session          │
│  Tool Registry + 四段管线 · OpenAI 兼容模型路由               │
└───────┬───────────────────────┬─────────────────────────────┘
        │                       │
   存储双后端               LLM / 工具
   sqlite（开发）           OpenAI 兼容 provider（管理员页面配置）
   mongodb（生产）          python.run / file.* / knowledge.search / http.request
                           （AI⁴MS 各子平台能力后续以工具形式接入）
```

核心原则（详见设计文档 §3.2）：harness 不 import FastAPI（可被 CLI/worker 等其他宿主复用）；所有进入 LLM 上下文的内容先落 session 事件日志（model-visible ⟺ logged）；工具 = 声明 + 统一管线，接入新能力零侵入。

## 目录结构

```
SynlysAgent/
├── packages/synlys-harness/     # 纯 Python Agent 运行时（零 FastAPI 依赖）
│   └── src/synlys_harness/      #   agent loop / session 事件 / tools 管线 / models 路由
├── apps/web/
│   ├── backend/                 # FastAPI 应用（唯一进程，PM2 部署单元）
│   │   ├── app/                 #   api（REST+SSE）/ services / core（配置+认证）/ db（双后端存储）
│   │   └── run_uvicorn.py       #   启动入口（读 .env）
│   └── frontend/                # React 19 + TS + Vite 三栏工作台（npm run build 产物由后端托管）
├── docs/superpowers/            # 设计文档（specs/）、实施计划（plans/）、验收报告与截图（acceptance/）
├── ecosystem.config.cjs         # PM2 部署配置
└── data/                        # 运行时数据（不入库）：workspaces/、sessions/、sqlite 库
```

## 快速开始

Python 环境为 conda 环境 `synlysagent`（Python 3.12）。

### 1. 后端安装与启动（开发）

```bash
conda activate synlysagent
cd apps/web/backend
pip install -e ../../packages/synlys-harness   # 核心运行时
pip install -e ".[dev]"                        # 后端本体 + 测试依赖

python run_uvicorn.py        # 默认 0.0.0.0:8005，健康检查 curl http://127.0.0.1:8005/api/health
```

### 2. 前端开发模式（热更新，代理 /api 到 8005）

```bash
cd apps/web/frontend
npm install
npm run dev                  # Vite dev server（5173），API 经代理走后端
```

### 3. 单端口构建部署（V1 最终形态）

```bash
cd apps/web/frontend
npm run build                # 产物 dist/（已在 .gitignore）
cd ../backend
python run_uvicorn.py        # 后端启动时检测到 dist/index.html 即托管前端
```

浏览器访问 `http://127.0.0.1:8005/`：同一端口同时提供页面与 API；无 dist 时自动退化为仅 API 模式（日志有提示）。

### 4. PM2 部署（生产）

```bash
pm2 start ecosystem.config.cjs    # interpreter 已指向 conda 环境 synlysagent 的 python
pm2 logs synlys-agent
```

## 配置要点

配置在 `apps/web/backend/.env`（模板见 `.env.example`，全部有默认值）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `STORAGE_BACKEND` | `sqlite` | 存储后端：`sqlite`（开发零依赖）或 `mongodb`（生产） |
| `AUTH_SECRET` | 空 | 与 AI⁴MS 门户共享的 HMAC-SHA256 secret；为空时按门户规则派生，多实例部署必须显式配置同一值 |
| `DEV_AUTH_TOKEN` | 空 | sqlite 开发模式的固定 token，登录页"开发模式进入"直连（如 `devtok`） |
| `FERNET_KEY` | 空 | provider api_key 落库加密 key（Fernet）；生成方式见 `.env.example` |
| `HTTP_ALLOWED_HOSTS` | 空 | `http.request` 工具域名白名单（逗号分隔），空则全部拒绝 |

其余变量（`MONGODB_URI`/`MONGODB_DB`/`SQLITE_PATH`/`HOST`/`PORT`/`DATA_DIR`/`AUTH_ENABLED`/`USER_QUOTA_BYTES`）见 [apps/web/backend/README.md](apps/web/backend/README.md)。

## 与 AI⁴MS 门户对接

- **免登录跳转**：门户 AppCard 配置跳转 `http://<host>:8005/#token=<token>`，前端从 URL hash 提取 token 放入 `Authorization: Bearer` 请求头，后端用 `AUTH_SECRET` 校验。
- **账号打通**：`STORAGE_BACKEND=mongodb` 时，登录直连 MongoDB 的 `ai4ms.users` 集合校验用户名/密码（PBKDF2-SHA256，格式与门户兼容）并签发同格式 token；sqlite 模式使用本地 `local_users`（开发用）。

## 测试

```bash
# harness 核心运行时（75 项）
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v

# web 后端（87 项，默认 sqlite 后端；设置 TEST_MONGODB_URI 后 mongodb 用例自动加入）
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v

# 前端类型检查 + 构建 / lint
cd apps/web/frontend
npm run build
npm run lint
```

## 后续路线（V1 之后）

详见设计文档 §11：AI⁴MS 工具接入（Spec_Agent 异步任务、WeKnora 检索替换 knowledge.search mock、SpecLabOS 设备/工作流）、MCP adapter、多 Agent/声明式 team 装配、SKILL.md 动态技能、双进程演进（harness RPC server 层）。
