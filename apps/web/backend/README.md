# SynlysAgent Web 后端

FastAPI 实现的 Agent 宿主：模型服务管理、助手/会话、SSE 对话流、用户文件工作区与受限 Python 沙箱（`python.run`），核心运行时复用 `packages/synlys-harness`。

## 环境与启动（开发）

conda 环境 `synlysagent`（Python 3.12+），安装两个本地包（在 `apps/web/backend` 目录下）：

```bash
conda activate synlysagent
pip install -e ../../packages/synlys-harness   # 核心运行时
pip install -e ".[dev]"                        # 后端本体 + 测试依赖
```

启动（两种等价方式，监听地址/端口取 `.env`，默认 `0.0.0.0:8005`）：

```bash
python run_uvicorn.py                 # 方式一：项目入口脚本
uvicorn app.main:app --host 0.0.0.0 --port 8005   # 方式二：uvicorn 直启
```

健康检查：`curl http://127.0.0.1:8005/api/health` → `{"status":"ok"}`

## 配置（.env）

在 `apps/web/backend/.env` 中配置（全部有默认值，`pydantic-settings` 加载）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `STORAGE_BACKEND` | `sqlite` | 存储后端二选一：`sqlite`（开发）或 `mongodb`（生产） |
| `SQLITE_PATH` | `../data/synlys_agent.db` | sqlite 库文件路径 |
| `MONGODB_URI` | 空 | `mongodb` 模式必填，业务数据连接串 |
| `MONGODB_DB` | `synlys_agent` | 业务库名（用户认证固定读 `ai4ms.users`，见下） |
| `AUTH_SECRET` | 空 | 与 AI4MS 门户共享的 HMAC-SHA256 签名 secret；**为空时按门户规则派生：`sha256("<进程cwd>_ai4ms_portal")`**，单机部署可留空，多实例/跨机器必须显式配置同一值 |
| `AUTH_ENABLED` | `true` | `false` 时匿名放行（仅本机调试） |
| `DEV_AUTH_TOKEN` | 空 | sqlite 开发模式的固定 token（免登录调试用） |
| `HOST` / `PORT` | `0.0.0.0` / `8005` | 监听地址与端口 |
| `DATA_DIR` | `../data` | 运行数据根：`workspaces/`（用户文件）、`sessions/`（事件 JSONL） |
| `FERNET_KEY` | 空 | provider `api_key` 落库加密 key（Fernet）。生成：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`；留空则 api_key 明文落库（仅开发）；生产配置后注意：换 key 前已加密的数据不可解（读取报错需重录） |
| `HTTP_ALLOWED_HOSTS` | 空 | `http.request` 工具的域名白名单（逗号分隔，支持子域后缀匹配），空则全部拒绝 |
| `USER_QUOTA_BYTES` | `1073741824` | 每用户工作区配额（字节） |
| `SANDBOX_MODE` | `local` | `python.run` 执行形态：`local`（本机 `-I` 子进程）或 `docker`（临时容器强隔离，多用户/云部署用；需预构建镜像并装 `synlys-harness[docker]`） |
| `SANDBOX_DOCKER_IMAGE` | `synlora-sandbox:latest` | docker 模式镜像名（构建：`docker build -t synlora-sandbox:latest docker/sandbox/`） |
| `SANDBOX_STRICT` | `false` | docker 不可用时：`false` 回退本机执行（事件标 `sandbox=local-weak`）；`true` 拒绝执行（fail-closed） |
| `SANDBOX_MEM_LIMIT` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` | `512m` / `1.0` / `256` | docker 模式单容器资源限额 |
| `SANDBOX_DOCKER_USER` | 空 | 容器内运行用户（空 = 镜像默认非 root 用户） |

## AI⁴MS 子平台接入（插件机制）

**设计原则：一切皆插件。** 新增子平台 = 新增一个插件目录，宿主与 harness 零改动；插件配置由插件自己声明（`plugin.json` 的 `config_schema`），由管理员在管理页填写后**落库加密**（敏感字段 Fernet 加密），**不写 `.env`/`settings.py`**。

插件包结构：

```
apps/web/backend/plugins/<id>/
  plugin.json              # manifest：id/name/version/tools_module/config_schema/skills/expert
  tools.py                 # 用 harness @tool 声明的工具，配置从 ctx.extra["plugins"][id] 取
  skills/<name>/SKILL.md   # 插件自带技能（可选）
```

**管理入口**：管理后台 →「插件」页（左侧导航）→ 安装（填写配置）/ 配置（敏感字段留空 = 保持原值）。

**新增一个子平台的步骤**：

1. 复制 `plugins/spec_agent/`，改 `plugin.json`（`id`/`name`/`config_schema`/`tools_module`/`expert`）
2. 写 `tools.py`（`from synlys_harness import tool, ToolContext, ToolResult`，配置走 `ctx.extra["plugins"]["<id>"]`）
3. 重启服务后在插件页安装

宿主与 harness 一行不用改。

**已接入**：`spec_agent`（Spec_Agent 核磁预测三件套 `spec.nmr.forward/reverse/search`；安装后自动播种「谱图解析专家」；服务端未开鉴权时凭证留空）。

## 能力目录与可见性（市场机制）

**设计原则：内置项随仓库走，可见性由策略控制，用户安装只写记录。** 「专家 / 技能 / 插件」统一纳入能力目录（`app/catalog/`），三层模型如下（参考 jiuwen 的目录分层 + DSH 的配置分层）：

| 层 | 谁能改 | 存哪 |
| --- | --- | --- |
| **内置目录**（随仓库，只读） | 开发者 | 专家=代码种子 `SEED_ASSISTANTS`；技能=随包播种的内置技能；插件=`apps/web/backend/plugins/<id>/` |
| **管理员策略**（可见性 + 默认启用） | 管理员 | `catalog_policy` 集合，`_id = f"{kind}:{item_id}"` |
| **用户安装**（只写记录） | 用户本人 | `user_capabilities` 集合，`_id = f"{uid}:{kind}:{item_id}"` |

两个新集合的形态：

```
catalog_policy      { _id: "plugin:spec_agent", kind, item_id,
                      visibility: "public"|"hidden", default_enabled: bool }
user_capabilities   { _id: "u1:plugin:spec_agent", user_id, kind, item_id, installed_at }
```

**可见性规则**（`CapabilityService`，运行期过滤的唯一入口）：

- `hidden` → 普通用户完全不可见（管理员后台仍可见、可改，且不提供「安装隐藏项」的入口）
- `public + 默认启用`（缺省值）→ 全员开箱可用，无需安装
- `public + 非默认` → 出现在用户侧「能力中心」，用户自行安装后对自己可见（不影响他人）

策略缺省 = `public + 默认启用`，因此**升级后既有行为不变**，无需数据迁移。

**安装 = 只写记录，不复制文件。** 安装只是往 `user_capabilities` 写一条 `(user, kind, item)` 记录；内置包随仓库（或数据目录播种）走，升级内置项即对所有已安装用户生效，**无副本漂移**、也不存在「装的是旧版」的问题。（jiuwen 要复制文件，是因为它从远端 hub 下载包；本项目首期内置即全部来源，无下载需求。）

**运行期落地**：`CapabilityService` 按用户算出可见集，统一作用于插件工具过滤、技能索引过滤、专家列表过滤、`ctx.extra["plugins"]` 只注入可见插件。**内置工具（`python.run`/`file.*`/`web.*` 等）不受影响**，只有插件贡献的工具受策略控制。技能过滤用**黑名单口径**（`hidden_skill_names`）：内置技能按技能策略、插件技能跟随其插件；公共技能目录里管理员自建的技能始终可见。

**插件配置两层**：管理员公共安装（`plugin_configs`，`_id = 插件 id`，部署级共享，如 Spec_Agent 网关地址）与用户个人安装（同集合，`_id = user:<uid>:<plugin_id>`，个人专用）并存；运行期**个人优先、公共打底**（浅合并），两者都无则空配置。用户维度文档只是「个人安装记录」，不参与插件整体安装状态判定。

**API 一览**：

| 侧 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 用户 | `GET` | `/api/v1/catalog` | 当前用户可见的能力目录（含策略与安装状态；`hidden` 项不返回） |
| 用户 | `POST` | `/api/v1/catalog/{kind}/{item_id}/install` | 安装（写记录；插件可带个人配置 body `{"config": {...}}`） |
| 用户 | `DELETE` | `/api/v1/catalog/{kind}/{item_id}/install` | 卸载（删记录） |
| 管理员 | `GET` | `/api/v1/admin/catalog` | 管理员视角目录（含 `hidden` 条目与全部策略） |
| 管理员 | `PUT` | `/api/v1/admin/catalog/{kind}/{item_id}/policy` | 配置 `{visibility, default_enabled}` |

用户侧入口：左栏底部用户菜单 →「能力中心」→ 独立整页 `/capabilities`（专家 / 技能 / 插件三分组，安装 / 卸载，插件可填个人配置）。管理后台为整页 `/admin/*`，由顶部页签改为**左侧导航列表**：常规 / 模型服务 / 助手管理 / 技能管理 / 插件。

**首期明确不做**：用户自建 / 导入技能与插件（用户私有目录 `{data_dir}/users/{uid}/` 仅设计预留，无写入路径）、按角色 / 按用户白名单的细粒度可见性、插件市场远程下载、常规设置的实际内容（外观主题已可经右上角切换，界面语言等后续提供）。

## 与 AI4MS 门户对接

- **免登录跳转**：门户 AppCard 配置跳转 `http://<host>:8005/#token=<token>`，前端从 location.hash 提取 token 后放入 `Authorization: Bearer <token>` 请求头（前端实现见 Plan 3）。token 为门户签发的 `{payload_b64}.{hmac_hex}` 格式，后端用 `AUTH_SECRET` 校验。
- **mongodb 模式账号打通**：`POST /api/v1/auth/login` 直连 `MONGODB_URI` 的 `ai4ms.users` 集合校验用户名/密码（PBKDF2-SHA256，格式与门户兼容），登录后签发同格式 token；sqlite 模式则查本地 `local_users` 集合。

## 测试

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v
```

全量在 sqlite 后端运行；设置环境变量 `TEST_MONGODB_URI` 后 mongodb 后端用例自动加入（否则跳过）。

## PM2 部署（生产）

仓库根目录的 `ecosystem.config.cjs` 已将 interpreter 指向 conda 环境 python，直接启动：

```bash
cd E:/agent_projects/SynlysAgent
pm2 start ecosystem.config.cjs
pm2 logs synlys-agent   # 查看日志；pm2 save 可持久化进程列表
```

实际执行体为 `apps/web/backend/run_uvicorn.py`（读取 `.env` 后 `uvicorn.run` 启动），崩溃自动重启（最多 10 次、间隔 3s）。

**部署注意（内置内容目录）**：`apps/web/backend/catalog/`（内置专家/技能/插件）是**数据目录而非 Python 包**，`pyproject.toml` 的 `include = ["app*"]` 不含它，因此 **editable 安装之外的部署（wheel 式安装、只同步 `app/` 的发布产物等）不会自动带上它**，必须把 `catalog/` 与 `app` 包一起部署到同级位置（源码部署即 `apps/web/backend/catalog/`）。缺失时内置内容为空（能力中心看不到任何专家/技能/插件），启动日志会告警「未发现任何内置内容」。
