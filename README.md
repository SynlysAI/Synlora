# Synlora 科研智能体平台

（内部代号 SynlysAgent：目录/包/conda 环境沿用早期代号，对外品牌统一为 Synlora）

类 Claude/DeepSeek Harness 的科研智能体 Web 平台：三栏对话工作台，通过对话形式使用各助手（谱图解析、高分子研发、数据分析、文件处理等），每个用户拥有独立沙箱（文件工作区 + 受限 Python 执行），管理员可在页面上配置模型与助手。作为 AI⁴MS 生态的独立子平台部署，现有 AI⁴MS 能力后续通过 Tool Registry 接入。

平台已具备完整的 Agent 运行时能力：SSE 流式对话与断连续传、**运行中插话**（steering，赶不上本轮自动转下一轮）、**工具级强制审批**（`Permission.ASK_USER`，管线硬约束）、**多题勾选式问询**（ask_user，逐题作答一次提交）、上下文自动压缩（超阈值摘要）、WeKnora 知识库实接（hybrid 检索 + 助手绑定）、文件交付卡（图片内联预览）。后台长任务走统一 Job 注册表（提交即返回，终态在右侧运行信息展示，不自动创建聊天回复）。AI⁴MS 子平台异步任务（Spec_Agent 五种谱图解析）经插件连接器接入，提交后自动跟踪并持久化结果。

- 版本：0.14.0-beta.1（内测版）
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
Synlora/
├── packages/synlys-harness/     # 纯 Python Agent 运行时（零 FastAPI 依赖）
│   └── src/synlys_harness/      #   agent loop / session 事件 / tools 管线 / models 路由
├── apps/web/
│   ├── backend/                 # FastAPI 应用（唯一进程，PM2 部署单元）
│   │   ├── app/                 #   api（REST+SSE）/ services / core（配置+认证）/ db（双后端存储）
│   │   ├── catalog/             #   内置内容（experts/ skills/ plugins/，按类型分目录，非 Python 包）
│   │   └── run_uvicorn.py       #   启动入口（读 .env）
│   └── frontend/                # React 19 + TS + Vite 三栏工作台（npm run build 产物由后端托管）
├── docs/superpowers/            # 设计文档（specs/）、实施计划（plans/）、验收报告与截图（acceptance/）
├── ecosystem.config.cjs         # PM2 部署配置
└── data/                        # 运行时数据（不入库）：public/{skills,catalog}（公共层）、users/<uid>/{workspaces,sessions,skills,experts}（用户层；不选工作区的会话以 sessions/{sid}/workspace 为工作区，文件与产物跟会话走）、sqlite 库
```

## 快速开始

Python 环境为 conda 环境 `synlysagent`（Python 3.12）。

### 1. 后端安装与启动（开发）

```bash
conda activate synlysagent
cd apps/web/backend
pip install -e ../../../packages/synlys-harness   # 核心运行时
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
uvicorn app.main:app --host 0.0.0.0 --port 8005   # 方式二：uvicorn 直启
```

浏览器访问 `http://127.0.0.1:8005/`：同一端口同时提供页面与 API；无 dist 时自动退化为仅 API 模式（日志有提示）。

### 4. PM2 部署（生产）

```bash
pm2 start ecosystem.config.cjs    # interpreter 已指向 conda 环境 synlysagent 的 python
pm2 logs synlys-agent
```

**部署硬约束：必须单实例。** 运行时（进行中的 run、SSE 消费队列、待作答 future）是单进程内存态，**`uvicorn` 只能用 `workers=1`、PM2 只能起一个实例**；多副本会让插话（steering）、问答回填与取消失效。当前明确不做 run 状态外置，横向扩展需先改造运行时。另注意 `ecosystem.config.cjs` 中的 `interpreter` 是 conda 环境 python 的绝对路径，换机器部署需同步修改。

## 配置要点

配置在 `apps/web/backend/.env`（模板见 `.env.example`，全部有默认值）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `STORAGE_BACKEND` | `sqlite` | 存储后端：`sqlite`（开发零依赖）或 `mongodb`（生产） |
| `AUTH_SECRET` | 空 | 与 AI⁴MS 门户共享的 HMAC-SHA256 secret；为空时按门户规则派生，多实例部署必须显式配置同一值 |
| `DEV_AUTH_TOKEN` | 空 | sqlite 开发模式的固定 token，登录页"开发模式进入"直连（如 `devtok`） |
| `FERNET_KEY` | 空 | provider api_key 落库加密 key（Fernet）；生成方式见 `.env.example` |
| `HTTP_ALLOWED_HOSTS` | 空 | `http.request` 工具域名白名单（逗号分隔），空则全部拒绝 |
| `SANDBOX_MODE` | `local` | `python.run` 执行形态：`local`（本机 `-I` 子进程，事故围栏）或 `docker`（临时容器强隔离，多用户部署用） |
| `SANDBOX_STRICT` | `false` | `docker` 模式不可用时：`false` 回退本机执行（事件标 `sandbox=local-weak`）；`true` 拒绝执行（fail-closed，公网部署建议开启） |
| `SANDBOX_MEM_LIMIT` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` | `512m` / `1.0` / `256` | docker 模式单容器资源限额 |
| `SANDBOX_JOB_DEFAULT_TIMEOUT_S` / `SANDBOX_JOB_MAX_TIMEOUT_S` | `1800` / `7200` | 后台沙箱任务独立默认/最大超时；不复用前台工具预算 |
| `SANDBOX_DEPLOYMENT_ID` | 空（按 `DATA_DIR` 派生） | 精确标记本部署容器，供取消和重启清理 |

其余变量（`MONGODB_URI`/`MONGODB_DB`/`SQLITE_PATH`/`HOST`/`PORT`/`DATA_DIR`/`AUTH_ENABLED`/`USER_QUOTA_BYTES`/`SANDBOX_DOCKER_IMAGE`/`SANDBOX_DOCKER_USER`）见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 插件机制（AI⁴MS 子平台接入）

一切皆插件：新增子平台 = 新增 `apps/web/backend/catalog/plugins/<id>/`（`plugin.json` 声明配置 schema/工具模块/技能/专家模板），宿主与 harness 零改动。插件配置由插件自己声明，在管理后台「插件」页填写后**落库加密**（敏感字段），**不进 `.env`/`settings.py`**；运行期按命名空间注入 `ctx.extra["plugins"]`。首个插件 `spec_agent` 提供核磁预测三件套并自动播种「谱图解析专家」，详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 能力目录与统一扩展中心（市场）

「专家 / 技能 / 扩展」统一纳入能力中心。平台插件仍使用能力目录与用户安装权限模型；用户 MCP 是独立的 Streamable HTTP 扩展配置，不复制插件文件、不绕过用户权限。

专家支持人设提示词、技能引用、MCP 引用、可用工具和推荐问题；专家只保存引用，不复制技能文件或 MCP 凭证。运行时会将专家引用与当前用户可用能力合并，并继续受插件会话开关、用户安装状态和工具白名单约束。

技能支持上传包含 `SKILL.md` 的 ZIP 目录包，保留 `scripts/`、`references/`、`assets/` 等附属文件；详情页提供左侧文件树与右侧文本/图片预览，压缩包大小、文件数量、解压大小和路径逃逸均有安全限制。

扩展中心统一展示插件市场、MCP 接入和我的扩展。普通用户首期仅开放 Streamable HTTP MCP，可配置 URL、Headers、Bearer Token、启用状态，并支持测试连接和发现远程工具；凭证加密存储，接口只返回字段名和是否已配置。

三层可见性模型：**内置目录**（随仓库，只读，`apps/web/backend/catalog/{experts,skills,plugins}/` 按类型分目录）→ **管理员策略**（`catalog_policy` 配可见性 `public`/`hidden` + 是否默认启用，缺省 = public + 非默认启用，即条目在市场可见但需用户安装后才可用）→ **用户安装**（`user_capabilities`，只写记录、**不复制文件**，升级即生效）。

能力中心按「**市场 / 我的**」页签组织：市场是「可安装的内置条目」，我的 = 用户自建能力 + 已安装能力。用户在市场安装后可**启用 / 停用**（`user_capabilities.enabled`，停用优先于默认启用）；用户还可**自建技能与专家**（落各自 `{data_dir}/users/<uid>/{skills,experts}/`，技能同名全局唯一，内置条目不可编辑、想定制请自建换名）。管理员在管理后台左侧导航（常规/模型服务/助手管理/技能管理/插件）逐项配可见性与默认。运行期可见集由 `CapabilityService` 按用户计算，统一过滤插件工具、技能索引、专家列表与 `ctx.extra["plugins"]`（内置工具不受影响）。

**当前状态**：已上线（0.14.0-beta.1）——用户侧「能力中心」（`/capabilities`）按**专家 / 技能 / 扩展**分类；专家与技能保留市场 / 我的卡片视图，扩展进入统一的插件市场、MCP 接入和我的扩展页面。技能可手动创建或上传 ZIP，专家可在完整编排表单中选择技能、MCP、工具和推荐问题。

点卡片进入**详情页**（路由 `/capabilities/<类型>/<条目 id>`），按条目类型展示技能说明与目录文件预览、专家人设与能力编排、插件配置字段与附属清单；**安装 / 卸载 / 启用 / 停用 / 编辑 / 删除**按类型在详情或扩展中心完成。**内置条目对普通用户只读**（标「内置 · 全员可用」，无启停与编辑入口），管理员在详情页有「在管理后台编辑」跳转直达对应管理页。

后端接口：`/api/v1/market/{kind}`、`GET /api/v1/me/capabilities/{kind}/{item_id}`、`PUT /api/v1/me/capabilities/{kind}/{id}`、`/api/v1/me/{skills,experts}`、`POST /api/v1/me/skills/import`、`GET /api/v1/me/skills/{name}/files`、`GET /api/v1/me/skills/{name}/file`、`GET/POST/PATCH/DELETE /api/v1/me/mcps` 和 `POST /api/v1/me/mcps/{id}/test`。详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 内置内容布局（catalog/）

**位置即类型，加一个目录即扩展**：内置专家 / 技能 / 插件统一落在 `apps/web/backend/catalog/`（`experts/<dir>/expert.json`、`skills/<name>/SKILL.md`、`plugins/<id>/plugin.json`），由 `app/catalog/loader.py` 的 `scan_catalog()` 一次性扫入；`catalog/skills/` 作为**只读技能根**直接提供，`{data_dir}/public/skills/` 为可写公共层（同名公共层优先），用户自建技能另落 `{data_dir}/users/<uid>/skills/`。**harness 内不含任何内置内容（内容归宿主、机制归 harness）**，宿主的 `catalog/` 是数据目录而非 Python 包，非 editable 部署需与 `app/` 同级一起放。详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### Docker 沙箱（多用户/云部署）

`SANDBOX_MODE=docker` 时，`python.run` 与 `shell.run` 在临时容器中执行：workspace 以 `/workspace` 读写挂载，本轮获准技能分别以 `/skills/<name>` 只读挂载；容器断网、非 root、资源受限、每次调用后删除。公共技能保持单份存储，不复制进用户目录。镜像需预构建：

```bash
docker build -t synlora-sandbox:latest docker/sandbox/
```

宿主需安装 docker SDK：`pip install "synlys-harness[docker]"`。探测失败（daemon 不可达/镜像缺失）按 `SANDBOX_STRICT` 决定回退或拒绝；每次执行结果带 `sandbox` 标记（`docker`/`local`/`local-weak`）随事件可观测。

其余变量（`MONGODB_URI`/`MONGODB_DB`/`SQLITE_PATH`/`HOST`/`PORT`/`DATA_DIR`/`AUTH_ENABLED`/`USER_QUOTA_BYTES`）见 [apps/web/backend/README.md](apps/web/backend/README.md)。

## 与 AI⁴MS 门户对接

- **免登录跳转**：门户 AppCard 配置跳转 `http://<host>:8005/#token=<token>`，前端从 URL hash 提取 token 放入 `Authorization: Bearer` 请求头，后端用 `AUTH_SECRET` 校验。
- **账号打通**：`STORAGE_BACKEND=mongodb` 时，登录直连 MongoDB 的 `ai4ms.users` 集合校验用户名/密码（PBKDF2-SHA256，格式与门户兼容）并签发同格式 token；sqlite 模式使用本地 `local_users`（开发用）。

## 测试

```bash
# harness 核心运行时（含 Docker 沙箱集成用例；无 daemon/镜像时按条件跳过）
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v

# web 后端（默认 sqlite 后端；设置 TEST_MONGODB_URI 后 mongodb 用例自动加入）
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v

# 前端类型检查 + 构建 / lint
cd apps/web/frontend
npm run build
npm run lint
```

## 后续路线

当前判断：**Agent 能力面已足够完整**（事件内核 / 工具管线 / steering / 强制审批 / 上下文压缩 / docker 沙箱 / 插件机制 / 能力市场），**薄弱的是产品化运维面**（用量看不清、成本无闸门、用户管不了、数据没备份、部署迁移脆弱）。因此路线按「先补齐多人使用不出事，再纵深能力」排序；其中统一 Job 注册表因近期要推 Spec_Agent 异步任务而单独提前。

**近期起点：统一 Job 注册表**（Spec_Agent 异步任务的地基，提交即返回 job_id，终态写入任务面板并支持主动查询）。

| 阶段 | 内容 | 说明 |
|---|---|---|
| **A 内测期打磨** | 工具结果 spill · CI 流水线 · 可观测性（结构化日志 / 工具调用审计 / 用量聚合）· 会话导出 Markdown | 每项 0.5~2 天，不依赖外部系统，内测期即可做 |
| **B 多人用之前必须** | 管理员用户管理页 · LLM 用量配额 · run 崩溃恢复 · 应用容器化 · 备份与数据迁移 | 不做则开放多人有实质风险 |
| **C 能力纵深** | 统一 Job 注册表（最高优先）→ Spec_Agent 异步任务 → SpecLabOS 设备工作流 → 跨会话长期记忆 → MCP adapter → 多 Agent / SwarmFlow | AI⁴MS 生态接入主线 |

**进展**：统一 Job 注册表（C1）✅ 已实现——后台任务提交即返回、轮询器同步状态、终态更新右侧运行信息并支持主动查询；Spec_Agent 异步谱图任务（C2）✅ 已实现——插件连接器接入 5 类谱图解析（NMR/IR/GPC/Raman/LC-MS），走通「上传 → 提交 → 轮询 → 结果回填」全链路；下一步 SpecLabOS 设备工作流（C3）建在其上。

完整计划（含每项的做法与验收标准）见 [docs/superpowers/plans/2026-09-16-synlysagent-13-next-roadmap.md](docs/superpowers/plans/2026-09-16-synlysagent-13-next-roadmap.md)；06 号 backlog 保留为历史记录。

**已完成的主要演进**：沙箱 Docker 化（0.4.0）· AI⁴MS 首期接入（Spec_Agent 同步三件套 + 按登录用户代签凭证，0.5.0）· 能力市场三层可见性模型（0.6.0）· 内置内容统一 `catalog/`（0.7.0）· 用户自建技能与专家（0.8.0）· 能力中心「市场 / 我的」两栏（0.9.0）· 能力中心左导航 + 卡片 + 详情页改版（0.10.0）· 统一 Job 注册表（0.11.0）· Spec_Agent 五类谱图异步任务（0.12.0）。

**当前明确不做**：按角色 / 按用户白名单的细粒度可见性、插件市场远程下载、用户自建插件、双进程 headless 运行时、移动端适配。
