# Synlora 科研智能体平台

（内部代号 SynlysAgent：目录/包/conda 环境沿用早期代号，对外品牌统一为 Synlora）

类 Claude/DeepSeek Harness 的科研智能体 Web 平台：三栏对话工作台，通过对话形式使用各助手（谱图解析、高分子研发、数据分析、文件处理等），每个用户拥有独立沙箱（文件工作区 + 受限 Python 执行），管理员可在页面上配置模型与助手。作为 AI⁴MS 生态的独立子平台部署，现有 AI⁴MS 能力后续通过 Tool Registry 接入。

平台已具备完整的 Agent 运行时能力：SSE 流式对话与断连续传、**运行中插话**（steering，赶不上本轮自动转下一轮）、**工具级强制审批**（`Permission.ASK_USER`，管线硬约束）、**多题勾选式问询**（ask_user，逐题作答一次提交）、上下文自动压缩（超阈值摘要）、WeKnora 知识库实接（hybrid 检索 + 助手绑定）、文件交付卡（图片内联预览）。

- 版本：0.7.0-beta.1（内测版）
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
uvicorn app.main:app --host 0.0.0.0 --port 8005   # 方式二：uvicorn 直启
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
| `SANDBOX_MODE` | `local` | `python.run` 执行形态：`local`（本机 `-I` 子进程，事故围栏）或 `docker`（临时容器强隔离，多用户部署用） |
| `SANDBOX_STRICT` | `false` | `docker` 模式不可用时：`false` 回退本机执行（事件标 `sandbox=local-weak`）；`true` 拒绝执行（fail-closed，公网部署建议开启） |
| `SANDBOX_MEM_LIMIT` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` | `512m` / `1.0` / `256` | docker 模式单容器资源限额 |

其余变量（`MONGODB_URI`/`MONGODB_DB`/`SQLITE_PATH`/`HOST`/`PORT`/`DATA_DIR`/`AUTH_ENABLED`/`USER_QUOTA_BYTES`/`SANDBOX_DOCKER_IMAGE`/`SANDBOX_DOCKER_USER`）见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 插件机制（AI⁴MS 子平台接入）

一切皆插件：新增子平台 = 新增 `apps/web/backend/catalog/plugins/<id>/`（`plugin.json` 声明配置 schema/工具模块/技能/专家模板），宿主与 harness 零改动。插件配置由插件自己声明，在管理后台「插件」页填写后**落库加密**（敏感字段），**不进 `.env`/`settings.py`**；运行期按命名空间注入 `ctx.extra["plugins"]`。首个插件 `spec_agent` 提供核磁预测三件套并自动播种「谱图解析专家」，详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 能力目录（市场）

「专家 / 技能 / 插件」统一纳入能力目录，三层可见性模型：**内置目录**（随仓库，只读，`apps/web/backend/catalog/{experts,skills,plugins}/` 按类型分目录）→ **管理员策略**（`catalog_policy` 配可见性 `public`/`hidden` + 是否默认启用，缺省 = public + 默认启用）→ **用户安装**（`user_capabilities`，只写记录、**不复制文件**，升级即生效）。管理员在管理后台左侧导航（常规/模型服务/助手管理/技能管理/插件）逐项配可见性与默认；普通用户在左栏用户菜单「能力中心」（独立整页 `/capabilities`）自行安装/卸载。运行期可见集由 `CapabilityService` 按用户计算，统一过滤插件工具、技能索引、专家列表与 `ctx.extra["plugins"]`（内置工具不受影响）。详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### 内置内容布局（catalog/）

**位置即类型，加一个目录即扩展**：内置专家 / 技能 / 插件统一落在 `apps/web/backend/catalog/`（`experts/<dir>/expert.json`、`skills/<name>/SKILL.md`、`plugins/<id>/plugin.json`），由 `app/catalog/loader.py` 的 `scan_catalog()` 一次性扫入；`catalog/skills/` 作为**只读技能根**直接提供，`{data_dir}/skills/` 退回可写公共层（同名公共层优先）。**harness 内不含任何内置内容（内容归宿主、机制归 harness）**，宿主的 `catalog/` 是数据目录而非 Python 包，非 editable 部署需与 `app/` 同级一起放。详见 [apps/web/backend/README.md](apps/web/backend/README.md)。

### Docker 沙箱（多用户/云部署）

`SANDBOX_MODE=docker` 时，`python.run` 在临时容器中执行：workspace 单目录挂载（容器内 `/workspace`，目录约定不变）、断网、内存/CPU/进程数限额、非 root 用户、跑完即删；镜像需预构建（分析全家桶，与内置技能依赖对齐）：

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
# harness 核心运行时（145 项；另有 docker 沙箱逃逸集成用例，无 daemon/镜像自动 skip）
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v

# web 后端（341 项，默认 sqlite 后端；设置 TEST_MONGODB_URI 后 mongodb 用例自动加入）
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v

# 前端类型检查 + 构建 / lint
cd apps/web/frontend
npm run build
npm run lint
```

## 后续路线

按价值与触发条件推进（详见 [docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md](docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md)）：

- **~~沙箱升级（多用户部署前置）~~**（✅ 0.4.0 完成）：`python.run` 执行器抽象（local/docker 可切换），docker 形态为临时容器 + 资源限额 + 断网，探测失败按 strict 回退或拒绝
- **AI⁴MS 工具接入（项目立身之本）**：首期（Spec_Agent 核磁三件套，插件化接入）✅ 0.5.0；统一 Job 注册表（异步任务状态机 + 完成通知）→ 5 种谱图解析异步任务 / Poly_Agent / SpecLabOS 设备工作流（后者经强制审批）
- **能力市场延续项**：用户自建 / 导入技能与插件（用户私有目录 `{data_dir}/users/{uid}/` 仅设计预留）、按角色 / 按用户白名单的细粒度可见性、插件市场远程下载（首期已上线内置目录 + 管理员策略 + 用户安装，见「能力目录（市场）」）
- **跨会话长期记忆**：工作区级记忆抽取与注入
- **MCP adapter**：Tool Registry 加 MCP 来源，一次投入换第三方工具生态
- **多 Agent / SwarmFlow**：声明式 team 装配（出现并行科研场景需求时启动）
- **动态技能市场 / 双进程运行时**：用量驱动，暂缓
