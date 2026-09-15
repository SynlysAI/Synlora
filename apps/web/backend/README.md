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
apps/web/backend/catalog/plugins/<id>/
  plugin.json              # manifest：id/name/version/tools_module/config_schema/skills/expert
  tools.py                 # 用 harness @tool 声明的工具，配置从 ctx.extra["plugins"][id] 取
  skills/<name>/SKILL.md   # 插件自带技能（可选）
```

**管理入口**：管理后台 →「插件」页（左侧导航）→ 安装（填写配置）/ 配置（敏感字段留空 = 保持原值，已配置的敏感字段可点「清除」）。

**新增一个子平台的步骤**：

1. 复制 `catalog/plugins/spec_agent/`，改 `plugin.json`（`id`/`name`/`config_schema`/`tools_module`/`expert`）
2. 写 `tools.py`（`from synlys_harness import tool, ToolContext, ToolResult`，配置走 `ctx.extra["plugins"]["<id>"]`；需要以登录用户身份调子平台时，另见下方 `ctx.extra["ai4ms_token"]` 约定）
3. 重启服务后在插件页安装

宿主与 harness 一行不用改。

**已接入**：`spec_agent`（Spec_Agent 核磁预测三件套 `spec.nmr.forward/reverse/search`；安装后自动播种「谱图解析专家」；服务端未开鉴权时凭证留空）。

**访问凭证（调用 AI⁴MS 子平台的身份）**：

- **可留空**：留空时平台按**登录用户**代签短效 token（要求 Synlora 的 `AUTH_SECRET` 与目标子平台一致，生产环境已满足）；填写则为**平台级服务身份**，用于兜底——用户在 AI⁴MS 侧没有账号、或子平台与本方 secret 不一致（如本地开发）时使用。缺失/失效的典型表现是工具返回 401。
- **凭证清除**：插件配置表单里，已配置的敏感字段旁有「清除」入口（保存后生效）——用于轮换凭证或改为「按登录用户代签」（留空即可）时清掉旧值。清除只作用于 schema 声明为 `secret` 的字段（其余名字提交后被忽略），且"同一字段既填新值又点清除"时**新值生效**。

**按用户身份调用（插件开发约定）**：宿主在每轮对话装配时注入 `ctx.extra["ai4ms_token"]`（当前登录用户的 AI⁴MS 代签凭证，1 小时有效；解析不到身份时该键不存在）。插件应**优先用它、回落到自身配置里的服务 token**：

```python
token = str(ctx.extra.get("ai4ms_token") or config.get("token") or "")
if token:
    headers["Authorization"] = f"Bearer {token}"
```

之所以要按用户：异步谱图任务会按提交者归属（否则多用户任务串号）。代签走 `app/services/ai4ms_identity.py`——先从登录 token 的 `ai4ms_user_id` 快路径取（mongodb 登录时写入），否则按用户名回查 `ai4ms.users` 的 `user_id`（带进程内缓存）；失败一律静默降级为「不注入」，绝不打断对话。

**获取平台级服务凭证（需要兜底身份时）**：Spec_Agent 的 token 是 HMAC-SHA256 自签、解析时回查 `ai4ms.users`（要求 `sub` 对应用户存在且 `active`），所以需要一个**已存在的 active 账号**来签长期 token（默认 12h 的登录 token 不适合做服务集成）。用仓库里的脚本一键签发：

```bash
conda run -n synlysagent python docker/spec-agent/mint_token.py --list          # 列出可用账号
conda run -n synlysagent python docker/spec-agent/mint_token.py --username <账号>  # 签 365 天 token
```

脚本自动读 Spec_Agent 的 `AUTH_SECRET` / `AUTH_MONGODB_URI`（默认 `E:/github_project/Spec_Agent/backend/.env`，可用 `--env-file` 指定），Mongo 不可达时可用 `--user-id/--username/--role` 直接指定账号。签出的 token 粘进管理后台「插件」页 → Spec_Agent → 配置 → **访问凭证**（保存即生效，无需重启；轮换时重跑脚本覆盖即可）。

## 能力目录与可见性（市场机制）

**设计原则：内置项随仓库走，可见性由策略控制，用户安装只写记录。** 「专家 / 技能 / 插件」统一纳入能力目录（`app/catalog/`），三层模型如下（参考 jiuwen 的目录分层 + DSH 的配置分层）：

| 层 | 谁能改 | 存哪 |
| --- | --- | --- |
| **内置目录**（随仓库，只读） | 开发者 | 专家=`catalog/experts/<dir>/expert.json`；技能=`catalog/skills/<name>/SKILL.md`；插件=`catalog/plugins/<id>/plugin.json` |
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
- `public + 非默认`（缺省值）→ 出现在用户侧「能力中心」，用户自行安装后对自己可见（不影响他人）
- `public + 默认启用` → 全员开箱可用，无需安装（管理员显式开启的例外）

策略缺省 = `public + 非默认`：内置条目在市场可见，但**需用户安装后才可用**。

**安装 = 只写记录，不复制文件。** 安装只是往 `user_capabilities` 写一条 `(user, kind, item)` 记录；内置内容随仓库在 `catalog/` 里，作为**只读技能根/扫描根**直接提供，升级内置项即对所有已安装用户生效，**无副本漂移**、也不存在「装的是旧版」的问题。（jiuwen 要复制文件，是因为它从远端 hub 下载包；本项目首期内置即全部来源，无下载需求。）

**运行期落地**：`CatalogService` 的专家/技能/插件三类条目**统一来自 `scan_catalog` 的扫描结果**（技能描述取 `SKILL.md` frontmatter）；`CapabilityService` 按用户算出可见集，统一作用于插件工具过滤、技能索引过滤、专家列表过滤、`ctx.extra["plugins"]` 只注入可见插件。**内置工具（`python.run`/`file.*`/`web.*` 等）不受影响**，只有插件贡献的工具受策略控制。技能过滤用**黑名单口径**（`hidden_skill_names`）：内置技能按技能策略、插件技能跟随其插件；公共技能目录里管理员自建的技能始终可见。

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

## 内置内容布局（catalog/）

**设计原则：位置即类型，加一个目录即扩展。** 全部内置内容统一落在宿主侧 `apps/web/backend/catalog/`（此前分散在 Python 常量 `SEED_ASSISTANTS`、harness 包内 `resources/skills/` 与 `backend/plugins/` 三处异构位置），按类型分目录；**harness 退回零内容（内容归宿主、机制归 harness）**。

```
apps/web/backend/catalog/          # 加一个目录 = 加一个内置项（自动扫到）
  experts/<dir>/expert.json        # 专家：id/name/avatar/description/system_prompt/tool_whitelist
  skills/<name>/SKILL.md           # 技能：frontmatter 即元数据，无额外 manifest
  plugins/<id>/plugin.json         # 插件：沿用既有契约（tools_module/config_schema/skills/expert）
```

三类包的最小示例：

```jsonc
// experts/<dir>/expert.json —— 内置独立专家（按 _id 幂等播种成助手文档）
{
  "id": "asst-data",
  "name": "数据分析助手",
  "avatar": "📊",
  "description": "优先用 python.run 做统计分析与可视化",
  "system_prompt": "你是数据分析助手……",
  "tool_whitelist": ["python.run", "file.read", "file.write", "file.list"]
}
```

```markdown
---
name: office-doc
description: 生成 Word / Excel / PPT 办公文档。用户要"整理成报告/做成 PPT"时使用。
version: "1.0"
author: SynlysAgent
tags: [文档, 报告]
---

# Office 文档生成
（正文……）
```

```jsonc
// plugins/<id>/plugin.json —— 插件 manifest（id/name/version/tools_module 必填）
{
  "id": "spec_agent",
  "name": "Spec_Agent 谱图解析",
  "version": "1.0.0",
  "tools_module": "tools.py",
  "config_schema": [{ "key": "base_url", "label": "服务地址", "type": "text", "required": true }],
  "skills": ["spec-nmr"],
  "expert": { "name": "谱图解析专家", "system_prompt": "……", "tool_whitelist": ["spec.nmr.forward"] }
}
```

**扫描器**（`app/catalog/loader.py`）：`catalog_roots(settings)` 给出搜索根（随仓库的 `apps/web/backend/catalog/` + `{data_dir}/catalog/` 运行期安装预留），`scan_catalog(roots) -> CatalogIndex{.experts,.skills,.plugins}` 三类分开返回。三条规则：

1. **位置即类型**：只认 `<root>/{experts,skills,plugins}/` 三个固定子目录，放错位置不收录。
2. **非法包只告警跳过、不阻断启动**：manifest 解析失败 / 缺必填字段（专家 `id/name/system_prompt`，插件 `id/name/version/tools_module`）/ `config_schema` 缺 `key` 的包，记日志后跳过。
3. **同名后者覆盖前者并告警**：同根内重复 id、或跨根重名时，遍历顺序靠后的覆盖靠前的（数据目录根在仓库根之后，故运行期安装预留位优先）。

**只读根 vs 公共层**：`catalog/skills/` 作为**只读技能根**直接提供给 `SkillService`（不再播种拷贝到 `{data_dir}/skills`），其技能标记 `builtin=True`、不可删（删除返回 404）；`{data_dir}/skills/` 退回**可写公共层**（管理员自建 / 导入技能，始终可见），两者同名时**公共层优先**。`builtin` 标记语义 = 「来自只读根」。

**旧副本迁移**：启动时清理 `{data_dir}/skills` 里与 `catalog/skills` 同名且 `SKILL.md` **字节一致**的旧播种副本；**字节不同 → 保留并告警**（可能是管理员改过，也可能是旧播种残留，需人工核对；保留期间它会因「公共层优先」遮蔽内置版本）。

**专家种子**：`app/catalog/seed.py::seed_experts` 按 `_id` 幂等（已存在跳过、不覆盖管理员改动，缺失补种），内置专家由 `catalog/experts` 驱动，原先的 `SEED_ASSISTANTS` 与 `BUILTIN_SKILL_NAMES` 两个硬编码已删除。

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
