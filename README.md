# Synlora 科研智能体平台

Synlora 是一个面向科研人员的 Web 智能体工作台。它在三栏对话界面中组合专家、技能、插件、MCP、知识库和用户文件工作区，让模型能够读取资料、执行分析代码、生成交付文件，并把耗时任务交给后台 Job 跟踪。平台作为 AI⁴MS 生态的独立子平台部署，可与门户及 Plane 科研上下文打通。

> 仓库内部代号仍为 **SynlysAgent**，部分目录、Python 包和 conda 环境名沿用该代号；对外产品名称统一为 **Synlora**。

- 当前版本：1.7.1（正式版）
- 平台设计：[2026-09-10 平台设计文档](docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md)
- 后端细节：[Web 后端说明](apps/web/backend/README.md)
- 扩展开发：[插件、技能与后台任务指南](docs/development/plugin-and-skill-guide.md)

## 功能概览

### 1. 科研对话工作台

- 左栏管理项目、会话与助手；中栏承载流式消息、思考过程、工具调用、问询与审批卡片；右栏查看文件、任务与运行信息。
- 会话可绑定项目工作区，也可使用独立工作区；上传文件、模型生成的报告、图表和表格统一落在用户可管理的工作区中。
- 模型服务兼容 OpenAI API，管理员可在后台配置多个 provider，用户在会话中选择模型。

### 2. 可审计的 Agent 运行时

- SSE 流式输出支持断连续传；用户可在运行中插话（steering），赶不上本轮边界时自动转为下一轮输入。
- 事件日志是会话事实源：进入模型上下文的内容先落事件，再投影为消息；瞬态 delta 不落盘。
- 支持多题问询（`ask_user`）、工具级强制审批（`Permission.ASK_USER`）、上下文自动压缩和运行取消。
- 会话事件可通过 `after_seq` 游标回放，Plane Agent Dock 也能用非流式接口提交后台 run 并回放终态事件。

### 3. 安全执行与后台任务

- 每个用户拥有独立文件工作区和配额；`python.run` 默认以本机隔离子进程执行，可切换到一次性 Docker 容器。
- Docker 模式下工作区挂载为 `/workspace`，获准技能只读挂载到 `/skills/<name>`；容器默认断网、非 root、资源受限并在调用后销毁。
- 执行输出超过 64 KiB 时自动截断，并把全文写入 `tmp/spill/`，模型可用 `file.read` 分段读回。
- `job.submit` 提交长任务后立即返回 job ID；任务状态由统一注册表轮询，支持查询与取消。
- 任务成功或失败后可自动唤醒所属会话：会话繁忙时作为插话注入，空闲时自动开启一轮汇总；连续无人输入的唤醒轮有上限，避免自激。

### 4. 能力中心

- **专家**：定义人设、工具白名单、技能引用、MCP 引用和推荐问题；管理员可维护内置专家，用户可自建私有专家。
- **技能**：以 `SKILL.md` 为入口，可携带 `scripts/`、`references/` 和 `assets/`；支持手动创建和 ZIP 目录包导入。
- **插件**：面向 AI⁴MS 子平台和外部科研服务，可贡献工具、异步 Job 连接器、技能和专家。
- **MCP**：用户可配置 Streamable HTTP MCP；管理员可部署支持 stdio 或 Streamable HTTP 的公共 MCP，并在会话中按需附加。
- 能力目录提供内置、公共、自建、安装、启用、会话开关等多层授权边界；运行时始终对专家白名单、用户可见集和会话选择取防御性交集。

### 5. 内置科研能力

- 内置专家：科研助手、数据分析助手。
- 内置技能：12 个官方技能，覆盖 PDF / Word / Excel / PPT 文档处理、探索性数据分析、统计检验、Matplotlib / Seaborn / 出版级可视化、RDKit、实验设计与不确定度。
- 内置插件：
  - **Spec_Agent**：NMR 正反向预测与库检索，NMR / IR / GPC / Raman / LC-MS 五类谱图异步解析。
  - **Poly_Agent**：聚酰亚胺 Tg、氟基电解质配方、Raman 解析、共聚竞聚率拟合、硅键谱图集成评分。
  - **Sciverse**：科技文献语义检索、结构化检索、全文分页读取与附件下载。
  - **ARS**：深度研究、论文写作、多视角同行评审与端到端学术研究流水线。

## 总体架构

```text
┌────────────────────────────────────────────────────────────┐
│ React 19 三栏工作台（apps/web/frontend）                     │
│ 会话 / 消息流 / 工具卡片 / 能力中心 / 管理后台              │
└───────────────────────────┬────────────────────────────────┘
                            │ REST + SSE /api/v1
┌───────────────────────────▼────────────────────────────────┐
│ FastAPI 单进程宿主（apps/web/backend）                      │
│ 认证 · 项目与文件 · 能力目录 · 插件 / MCP · Job · 静态托管 │
└───────────────────────────┬────────────────────────────────┘
                            │ 进程内调用
┌───────────────────────────▼────────────────────────────────┐
│ synlys-harness（packages/synlys-harness）                  │
│ Agent loop · 事件会话 · 工具管线 · 模型路由 · 沙箱执行     │
└──────────┬─────────────────────────────┬───────────────────┘
           │                             │
   SQLite / MongoDB                OpenAI 兼容模型与工具
```

关键边界：

- `synlys-harness` 不依赖 FastAPI，可被其他宿主复用；Web 层负责认证、持久化、目录与产品能力装配。
- 机制归 harness，内容归宿主：内置专家、技能、插件和公共 MCP 放在 `catalog/`，不硬编码进运行时。
- 所有进入模型上下文的内容必须先进入事件日志，便于回放、审计与上下文压缩。
- 当前运行中的 run、SSE 队列和问询 future 是进程内状态，应用必须单实例部署。

## 快速开始

### 环境要求

- Python 3.12+（推荐 conda 环境名 `synlysagent`）
- Node.js 20+ 与 npm
- SQLite 用于本地开发；生产建议 MongoDB
- Docker 仅在启用容器沙箱或构建沙箱镜像时需要

### 1. 启动后端

```bash
conda create -n synlysagent python=3.12 -y
conda activate synlysagent

cd apps/web/backend
python -m pip install -e ../../../packages/synlys-harness
python -m pip install -e ".[dev]"
cp .env.example .env

python run_uvicorn.py
```

默认监听 `0.0.0.0:8005`。健康检查：

```bash
curl http://127.0.0.1:8005/api/health
```

### 2. 启动前端开发服务

```bash
cd apps/web/frontend
npm install
npm run dev
```

Vite 默认运行在 `http://127.0.0.1:5173`，`/api` 请求会代理到 8005。

### 3. 单端口构建部署

```bash
cd apps/web/frontend
npm run build

cd ../backend
python run_uvicorn.py
```

后端检测到 `frontend/dist/index.html` 后会托管 SPA，访问 `http://<host>:8005/` 即可同时使用页面与 API。无构建产物时退化为仅 API 模式。

### 4. 启用 Docker 沙箱

```bash
docker build -t synlora-sandbox:latest docker/sandbox/

cd apps/web/backend
python -m pip install -e "../../../packages/synlys-harness[docker]"
```

在 `.env` 中配置：

```dotenv
SANDBOX_MODE=docker
SANDBOX_STRICT=true
```

多用户或公网部署建议保持 `SANDBOX_STRICT=true`。若 Docker 探测失败，严格模式会拒绝执行；非严格模式会回退本机执行，并在事件中标记 `sandbox=local-weak`。

## 关键配置

配置文件位于 `apps/web/backend/.env`，模板见 [`.env.example`](apps/web/backend/.env.example)。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STORAGE_BACKEND` | `sqlite` | `sqlite` 用于开发，`mongodb` 用于生产。 |
| `SQLITE_PATH` | `.runtime/data/synlys_agent.db` | SQLite 数据库路径。 |
| `MONGODB_URI` / `MONGODB_DB` | 空 / `synlys_agent` | MongoDB 模式必填；账号认证读取 `ai4ms.users`。 |
| `AUTH_ENABLED` / `AUTH_SECRET` | `true` / 空 | 与 AI⁴MS 门户共享的 HMAC secret；跨机器部署必须显式配置。 |
| `DEV_AUTH_TOKEN` | 空 | SQLite 开发模式固定 token，仅本地调试使用。 |
| `FERNET_KEY` | 空 | 模型 API key 与 MCP 凭证加密 key；生产必须配置。 |
| `DATA_DIR` | `.runtime/data` | 公共能力、用户工作区、会话事件等运行数据根目录。 |
| `USER_QUOTA_BYTES` | `1073741824` | 单用户工作区配额。 |
| `HTTP_ALLOWED_HOSTS` | 空 | `http.request` 域名白名单；空表示全部拒绝。 |
| `WEKNORA_BASE_URL` / `WEKNORA_API_KEY` | 空 | WeKnora 知识库连接；为空时知识工具报未配置。 |
| `ASSISTANT_WEB_SEARCH_ENDPOINT` | 空 | SearXNG 搜索服务；为空时禁用 `web.search`。 |
| `PLANE_BASE_URL` / `PLANE_API_TOKEN` / `PLANE_SERVICE_TOKEN` | 空 | Plane 科研上下文与 delegated token 集成。 |
| `SANDBOX_MODE` | `local` | `local` 或 `docker`。 |
| `SANDBOX_DOCKER_IMAGE` | `synlora-sandbox:latest` | 容器沙箱镜像。 |
| `SANDBOX_STRICT` | `false` | Docker 不可用时是否 fail-closed。 |
| `SANDBOX_MEM_LIMIT` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` | `512m` / `1.0` / `256` | 单容器资源限额。 |
| `SANDBOX_DOCKER_NETWORK` | `false` | 是否放开容器网络；仅建议可信内网开启。 |
| `SANDBOX_JOB_DEFAULT_TIMEOUT_S` / `SANDBOX_JOB_MAX_TIMEOUT_S` | `1800` / `7200` | 后台任务默认与最大超时。 |
| `SANDBOX_DEPLOYMENT_ID` | 按 `DATA_DIR` 派生 | 部署标识，用于取消与重启清理容器。 |

完整配置说明见 [Web 后端说明](apps/web/backend/README.md)。

## 生产部署检查清单

1. **单实例运行**：`uvicorn` 只能 `workers=1`，PM2 只能启动一个应用实例；多副本会破坏插话、问答回填、取消和任务轮询。
2. **存储**：使用 MongoDB，显式配置 `MONGODB_URI` 与 `AUTH_SECRET`，并规划数据库备份。
3. **密钥**：配置 `FERNET_KEY`，并在更换密钥前确认已加密数据可解密或已重新录入。
4. **沙箱**：多用户部署启用 Docker 沙箱、严格模式和资源限额；公网部署保持容器断网。
5. **前端**：执行 `npm run build`，由后端单端口托管；部署包必须同时包含 `app/`、`catalog/` 和前端 `dist/`。
6. **PM2**：仓库提供 [ecosystem.config.cjs](ecosystem.config.cjs)，但其中的 Python 解释器绝对路径需按目标机器修改。
7. **外部能力**：为 Spec_Agent、Poly_Agent、Sciverse、WeKnora、SearXNG 和 Plane 配置服务地址与凭证；插件敏感配置在管理后台填写并落库加密。

## 扩展与生态集成

### 插件和技能扩展

内置与私有扩展统一放在能力目录：

```text
apps/web/backend/catalog/
├── experts/<id>/expert.json
├── skills/<name>/SKILL.md
├── plugins/<id>/plugin.json
└── mcp/<id>/mcp.json
```

运行期私有扩展放在 `{DATA_DIR}/public/catalog/`，同 ID 条目会覆盖仓库内置版本。插件代码属于管理员可信部署内容；普通用户只能安装、启用和配置被授权的能力，不能上传 Python 插件代码。

开发规范、manifest 字段、Job 连接器契约和技能包约束见[插件、技能与后台任务指南](docs/development/plugin-and-skill-guide.md)。

### AI⁴MS 门户

- 门户 AppCard 可配置 `http://<host>:8005/#token=<token>`，前端从 URL hash 提取 token 并放入 `Authorization: Bearer` 请求头。
- MongoDB 模式下，Synlora 直连 `ai4ms.users` 校验门户账号密码，并签发同格式 token。
- SQLite 模式使用本地用户集合和 `DEV_AUTH_TOKEN`，仅用于开发。

### Plane 科研上下文

- 建会话和发送消息时可携带 `X-Research-Context-Token`；平台接受 `agent-context.v2` 与 `agent-context.v3`。
- v3 支持 Workspace / Project / Research Chain / Node 四级目标、能力硬上界和 bounded manifest。
- 通过校验后，上下文元数据落库，`allowed_tools`、`allowed_plugins` 和 `allowed_mcp_ids` 会与注册表、专家白名单、插件与 MCP 可见性取防御性交集；opaque token 本身不落库。
- Plane BFF 可用服务凭证调用 `/api/v1/research/delegated-token` 换取短效用户 token，并通过 `/api/v1/research/capabilities` 获取只读能力清单。
- `POST /api/v1/sessions/{sid}/runs` 提交后台 run 后立即返回 `run_id`；Plane 可通过 durable events 回放、审批 answer 和显式 cancel 管理生命周期。

## 目录结构

```text
Synlora/
├── apps/web/
│   ├── backend/              # FastAPI 宿主、API、服务层、能力目录与测试
│   └── frontend/             # React 19 + TypeScript + Vite 工作台
├── packages/synlys-harness/  # 纯 Python Agent 运行时
├── docker/                   # 沙箱镜像与 Spec_Agent 相关镜像
├── docs/
│   ├── development/          # 扩展与运维指南
│   └── superpowers/          # 设计文档、实施计划与验收材料
├── ecosystem.config.cjs      # PM2 示例配置
└── .runtime/data/            # 运行数据（不入库）
```

## 测试与质量检查

```bash
# Agent 核心运行时
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v

# Web 后端；设置 TEST_MONGODB_URI 后自动加入 MongoDB 用例
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v

# 前端类型检查、构建与 lint
cd apps/web/frontend
npm run build
npm run lint
```

后台沙箱 Docker 集成用例在无 daemon 或镜像时按条件跳过。提交代码前请至少运行与改动相关的测试集，并保持版本号口径一致。

## 文档索引

- [平台架构设计](docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md)
- [能力中心设计](docs/superpowers/specs/2026-09-16-synlysagent-capability-center-redesign-design.md)
- [MCP 会话附加设计](docs/superpowers/specs/2026-09-27-mcp-session-attach-design.md)
- [插件、技能与后台任务指南](docs/development/plugin-and-skill-guide.md)
- [Web 后端说明](apps/web/backend/README.md)
