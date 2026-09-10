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
