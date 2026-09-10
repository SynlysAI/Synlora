# SynlysAgent Plan 2：FastAPI Web 后端

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SynlysAgent 的 FastAPI web 后端（单进程 8005 端口）：双后端存储、共享认证、助手/模型管理、SSE 对话端点（包装 harness RunSession）、文件工作区、事件回放。

**Architecture:** `apps/web/backend` 是 harness 的宿主。DocumentStore 抽象（SQLite 开发/MongoDB 生产）承载六个 repository；SessionService 把 harness 的 EventLog sinks 接到 JSONL 文件 + DB 副本 + SSE 内存队列；RunRegistry 管理 cancel。所有配置走 Settings。

**Tech Stack:** FastAPI、uvicorn、pydantic-settings、motor（可选）、aiosqlite、cryptography（Fernet）、sse-starlette、websockets/httpx（测试）。

**设计文档:** `docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md` §5
**harness 版本:** e0bc4b6（71 测试绿），API 见本文「附录 A」

**环境（Windows/git-bash）:** Python 命令统一 `conda run -n synlysagent --no-capture-output`。后端目录 `apps/web/backend`，依赖装入 synlysagent 环境。

**Plan 2 必须遵守的 harness 接入契约**（Plan 1 最终审查产出）：
1. `RunSession(config, registry, pipeline, backend, event_log, user_id, run_id, hooks=None, workspace_root=None, context_extra=None)`
2. **sink 不得抛异常**——EventLog.append 直接 await sink，宿主 sink 必须 try/except 全包（持久化失败不杀对话）
3. **SSE 推送用内存队列 + 独立发送循环**——sink 串行 await，慢客户端不得拖住 LLM 流（sink 里只 put 队列）
4. **消费 run() 用 `contextlib.aclosing` 包裹**
5. JSONL 路径 `data/sessions/{session_id}/events.jsonl`（回放源）；DB events 是副本
6. `workspace_root` 传每用户真实目录（`data/workspaces/{user_id}`）；None 时文件/沙箱工具返回 no_workspace
7. `python.run` 是事故围栏非安全边界；产物落 `output/`（沙箱 cwd 是 `tmp/`）
8. asyncio task cancel 后收尾事件从 EventLog 读（generator yield 不可达）
9. `Permission.ASK_USER` 当前按 ALLOW 执行——不使用
10. `http_allowed_hosts` 经 `context_extra` 注入（服务端 Settings 静态配置）

---

## 文件结构（Plan 2 产出）

```
apps/web/backend/
├── pyproject.toml                  # 独立包 synlys-web（依赖 synlys-harness）
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app 工厂 + 路由挂载 + 启动种子
│   ├── core/
│   │   ├── __init__.py
│   │   ├── settings.py             # Settings（pydantic-settings）
│   │   └── auth.py                 # HMAC token 解析（AI4MS 兼容）+ 本地用户模式
│   ├── db/
│   │   ├── __init__.py
│   │   ├── store.py                # DocumentStore 协议 + SqliteStore + MongoStore
│   │   └── repos.py                # 六个 repository + 种子助手
│   ├── services/
│   │   ├── __init__.py
│   │   ├── agent_service.py        # SessionService：harness 组装 + sinks + SSE 队列
│   │   └── workspace.py            # 用户工作区布局 + 配额
│   └── api/
│       ├── __init__.py
│       ├── deps.py                 # get_current_user / require_admin / get_store
│       ├── auth_api.py             # POST /auth/login、GET /auth/me
│       ├── models_api.py           # /models CRUD + /test
│       ├── assistants_api.py       # /assistants CRUD
│       ├── sessions_api.py         # /sessions CRUD + /messages(SSE) + /events + cancel
│       └── files_api.py            # /files 上传/列表/下载/删除
└── tests/
    ├── conftest.py                 # 临时 Settings + sqlite + httpx AsyncClient
    ├── test_store.py
    ├── test_repos.py
    ├── test_auth.py
    ├── test_mgmt_api.py            # models + assistants API
    ├── test_chat_api.py            # SSE 端到端（mock provider）
    └── test_files_api.py           # 上传→python.run→产物（经 chat）
ecosystem.config.cjs                # PM2（根目录）
```

---

### Task 1: 后端脚手架与 Settings

**Files:** Create `apps/web/backend/pyproject.toml`、`.env.example`、`app/__init__.py`、`app/core/__init__.py`、`app/core/settings.py`、`app/main.py`、`tests/__init__.py`

- [ ] **Step 1: pyproject.toml**

```toml
[project]
name = "synlys-web"
version = "0.1.0"
description = "SynlysAgent Web 后端（FastAPI，harness 宿主）"
requires-python = ">=3.12"
dependencies = [
    "synlys-harness",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "pydantic-settings>=2.2",
    "aiosqlite>=0.20",
    "cryptography>=42",
    "sse-starlette>=2.1",
    "python-multipart>=0.0.9",
]
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24", "httpx>=0.27"]
prod = ["motor>=3.4"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

安装：`conda run -n synlysagent pip install -e "../../packages/synlys-harness" -e ".[dev]"`（workspace 内相对路径）。

- [ ] **Step 2: `.env.example`**（根目录 data/ 由 Settings 定）

```env
# 存储：mongodb | sqlite（默认 sqlite，开发零依赖）
STORAGE_BACKEND=sqlite
MONGODB_URI=
MONGODB_DB=synlys_agent
SQLITE_PATH=../data/synlys_agent.db

# 认证（与 AI4MS 共享）：AUTH_SECRET 与门户一致；DEV_AUTH_TOKEN 为 sqlite 开发模式的本地 token
AUTH_SECRET=
AUTH_ENABLED=true
DEV_AUTH_TOKEN=

# 服务
HOST=0.0.0.0
PORT=8005
DATA_DIR=../data

# http.request 工具域名白名单（逗号分隔；空 = 全拒）
HTTP_ALLOWED_HOSTS=

# 加密 key（Fernet key，用于 provider api_key 加密；生成：python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"）
FERNET_KEY=

# 用户工作区配额（字节）
USER_QUOTA_BYTES=1073741824
```

- [ ] **Step 3: `app/core/settings.py`**

```python
"""应用配置（pydantic-settings，env 与 .env 加载）。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。

    Attributes:
        storage_backend: 存储后端（mongodb|sqlite）。
        mongodb_uri: Mongo 连接串（backend=mongodb 时必填）。
        mongodb_db: 业务库名。
        sqlite_path: SQLite 文件路径。
        auth_secret: 与 AI4MS 共享的 HMAC secret（为空时按门户规则派生）。
        auth_enabled: 关闭后匿名放行（仅开发）。
        dev_auth_token: sqlite 开发模式的固定 token。
        host/port: 监听地址。
        data_dir: 运行数据根（workspaces/sessions）。
        http_allowed_hosts: http.request 工具白名单（逗号分隔）。
        fernet_key: provider api_key 加密 key。
        user_quota_bytes: 每用户工作区配额。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    storage_backend: str = "sqlite"
    mongodb_uri: str = ""
    mongodb_db: str = "synlys_agent"
    sqlite_path: str = "../data/synlys_agent.db"
    auth_secret: str = ""
    auth_enabled: bool = True
    dev_auth_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8005
    data_dir: str = "../data"
    http_allowed_hosts: str = ""
    fernet_key: str = ""
    user_quota_bytes: int = 1_073_741_824

    @property
    def data_root(self) -> Path:
        """数据根目录（自动创建）。"""
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allowed_hosts(self) -> list[str]:
        """http 白名单列表。"""
        return [h.strip() for h in self.http_allowed_hosts.split(",") if h.strip()]
```

- [ ] **Step 4: `app/main.py`**（最小骨架，后续任务扩展）

```python
"""SynlysAgent Web 后端入口。"""
from fastapi import FastAPI


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="SynlysAgent", version="0.1.0")

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 5: 验证启动**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -c "from app.main import app; print(app.title)"
conda run -n synlysagent --no-capture-output python -m uvicorn app.main:app --port 8005 &
sleep 3 && curl -s http://127.0.0.1:8005/api/health   # {"status":"ok"} 后 kill uvicorn
```

- [ ] **Step 6: Commit** `feat(web): 后端脚手架与 Settings 配置`

---

### Task 2: DocumentStore 双后端

**Files:** Create `app/db/__init__.py`、`app/db/store.py`、`tests/test_store.py`

- [ ] **Step 1: 失败测试**（要点：insert/get/update/delete/list_filter/排序/索引字段提取；Mongo 参数化用 `skipif`）

```python
"""DocumentStore 单测（sqlite 全跑；mongo 仅在有测试 URI 时跑）。"""
import os

import pytest

from app.db.store import create_store

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["sqlite"])
async def store(request, tmp_path):
    """sqlite 后端 store。"""
    s = create_store("sqlite", sqlite_path=str(tmp_path / "t.db"))
    await s.init()
    yield s
    await s.close()

MONGO_URI = os.environ.get("TEST_MONGODB_URI", "")
```

（测试体：`insert("users", {"_id": "u1", "user_id": "u1", "name": "a"})` → `get`、`update` 部分字段合并、`delete`、`list("users", filters={"user_id": "u1"}, sort=[("seq", 1)])`；`test_index_columns_extracted`：schema 声明索引字段后 sqlite 表中该列可查。）

- [ ] **Step 2: 实现 `app/db/store.py`**

```python
"""文档存储抽象：协议 + SQLite/MongoDB 双实现。

collection schema 形如 {"users": ["user_id", "seq"]}（第二项为提取为真实列的索引字段）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

COLLECTION_INDEXES: dict[str, list[str]] = {
    "assistants": ["name"],
    "providers": ["name"],
    "sessions": ["user_id", "assistant_id", "updated_at"],
    "events": ["session_id", "seq"],
    "files": ["user_id"],
    "runs": ["session_id", "user_id", "status"],
    "local_users": ["username"],
}


class DocumentStore(Protocol):
    """文档存储协议。"""

    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def insert(self, collection: str, doc: dict) -> dict: ...
    async def get(self, collection: str, doc_id: str) -> dict | None: ...
    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None: ...
    async def delete(self, collection: str, doc_id: str) -> bool: ...
    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]: ...


class SqliteStore:
    """SQLite 文档存储：_id 主键 + doc JSON 列 + 索引字段提取列。"""

    def __init__(self, path: str) -> None:
        """初始化（路径为空时内存库）。"""
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """建库建表（幂等）。"""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path or ":memory:")
        self._db.row_factory = aiosqlite.Row
        for name, indexes in COLLECTION_INDEXES.items():
            cols = ", ".join(f'"{c}" TEXT' for c in indexes)
            await self._db.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" (_id TEXT PRIMARY KEY, doc TEXT NOT NULL, {cols})'
            )
            for c in indexes:
                await self._db.execute(f'CREATE INDEX IF NOT EXISTS "ix_{name}_{c}" ON "{name}"("{c}")')
        await self._db.commit()

    async def close(self) -> None:
        """关闭连接。"""
        if self._db:
            await self._db.close()

    @staticmethod
    def _row_to_doc(row: aiosqlite.Row) -> dict:
        """行 → 文档。"""
        return json.loads(row["doc"])

    async def insert(self, collection: str, doc: dict) -> dict:
        """插入文档（_id 必填，重复抛 ValueError）。"""
        doc_id = doc["_id"]
        indexes = COLLECTION_INDEXES.get(collection, [])
        cols = ", ".join(["_id", "doc"] + indexes)
        marks = ", ".join("?" * (2 + len(indexes)))
        vals: list[Any] = [doc_id, json.dumps(doc, ensure_ascii=False)] + [str(doc.get(c, "")) for c in indexes]
        try:
            await self._db.execute(f'INSERT INTO "{collection}" ({cols}) VALUES ({marks})', vals)
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"_id 已存在: {doc_id}") from exc
        await self._db.commit()
        return doc

    async def get(self, collection: str, doc_id: str) -> dict | None:
        """按 _id 取文档。"""
        cur = await self._db.execute(f'SELECT doc FROM "{collection}" WHERE _id = ?', (doc_id,))
        row = await cur.fetchone()
        return self._row_to_doc(row) if row else None

    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None:
        """合并更新（读-改-写）。"""
        cur = await self._db.execute(f'SELECT doc FROM "{collection}" WHERE _id = ?', (doc_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        doc = self._row_to_doc(row)
        doc.update(fields)
        indexes = COLLECTION_INDEXES.get(collection, [])
        sets = ", ".join(['doc = ?'] + [f'"{c}" = ?' for c in indexes])
        vals: list[Any] = [json.dumps(doc, ensure_ascii=False)] + [str(doc.get(c, "")) for c in indexes] + [doc_id]
        await self._db.execute(f'UPDATE "{collection}" SET {sets} WHERE _id = ?', vals)
        await self._db.commit()
        return doc

    async def delete(self, collection: str, doc_id: str) -> bool:
        """按 _id 删除。"""
        cur = await self._db.execute(f'DELETE FROM "{collection}" WHERE _id = ?', (doc_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]:
        """按索引字段过滤 + 排序（排序字段须在索引列中）。"""
        where, vals = "", []
        if filters:
            conds = []
            for k, v in filters.items():
                conds.append(f'"{k}" = ?')
                vals.append(str(v))
            where = "WHERE " + " AND ".join(conds)
        order = ""
        if sort:
            order = "ORDER BY " + ", ".join(f'"{c}" {"ASC" if d >= 0 else "DESC"}' for c, d in sort)
        lim = f"LIMIT {limit}" if limit else ""
        cur = await self._db.execute(
            f'SELECT doc FROM "{collection}" {where} {order} {lim}', vals
        )
        rows = await cur.fetchall()
        return [self._row_to_doc(r) for r in rows]


class MongoStore:
    """MongoDB 文档存储（motor）。filters 直接透传 Mongo 查询。"""

    def __init__(self, uri: str, db: str) -> None:
        """初始化连接参数。"""
        from motor.motor_asyncio import AsyncIOMotorClient  # 延迟导入：非 prod 无需装 motor
        self._client = AsyncIOMotorClient(uri)
        self._db = self._client[db]

    async def init(self) -> None:
        """连通性检查。"""
        await self._db.command("ping")

    async def close(self) -> None:
        """关闭连接。"""
        self._client.close()

    async def insert(self, collection: str, doc: dict) -> dict:
        """插入（重复 _id 抛 ValueError）。"""
        try:
            await self._db[collection].insert_one(dict(doc))
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise ValueError(f"_id 已存在: {doc['_id']}") from exc
            raise
        return doc

    async def get(self, collection: str, doc_id: str) -> dict | None:
        """按 _id 取。"""
        return await self._db[collection].find_one({"_id": doc_id})

    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None:
        """$set 合并更新。"""
        await self._db[collection].update_one({"_id": doc_id}, {"$set": fields})
        return await self.get(collection, doc_id)

    async def delete(self, collection: str, doc_id: str) -> bool:
        """删除。"""
        r = await self._db[collection].delete_one({"_id": doc_id})
        return r.deleted_count > 0

    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]:
        """过滤 + 排序 + limit。"""
        cursor = self._db[collection].find(filters or {})
        if sort:
            cursor = cursor.sort([(c, d) for c, d in sort])
        if limit:
            cursor = cursor.limit(limit)
        return [d async for d in cursor]


def create_store(backend: str, *, sqlite_path: str = "", mongodb_uri: str = "",
                 mongodb_db: str = "synlys_agent") -> DocumentStore:
    """按配置构造 store。

    Raises:
        ValueError: backend 未知。
    """
    if backend == "sqlite":
        return SqliteStore(sqlite_path)
    if backend == "mongodb":
        return MongoStore(mongodb_uri, mongodb_db)
    raise ValueError(f"未知 STORAGE_BACKEND: {backend}")
```

- [ ] **Step 3: 跑测试绿 → Commit** `feat(web): DocumentStore 双后端（sqlite/mongo）`

注：anyio marker 需要 `tests/conftest.py` 提供 anyio_backend fixture（`@pytest.fixture def anyio_backend(): return "asyncio"`），或统一用 asyncio_mode=auto（本包 pytest.ini 已配 auto，测试直接 async def）。

---

### Task 3: repositories 与种子助手

**Files:** Create `app/db/repos.py`、`tests/test_repos.py`

六个 repo 全部基于 DocumentStore，方法：`create/get/list/update/delete`（薄封装 + `_id` 生成 + 时间戳）。关键接口签名：

```python
class ProviderRepo:      # api_key 写前 Fernet 加密（app/core/crypto.py: encrypt_key/decrypt_key；
    async def create(self, doc: dict) -> dict            # key 缺失时明文并存 api_key_plain=True）
    async def get_public(self, doc_id: str) -> dict | None   # 无明文 key，含 has_key: bool
    async def get_decrypted(self, doc_id: str) -> dict | None  # 解密副本（组装 ModelProviderConfig 用）

class AssistantRepo:     # 字段 name/avatar/description/system_prompt/model_provider_id/
    async def delete(self, doc_id: str) -> bool           # tool_whitelist/knowledge_base_ids/builtin
    # builtin=True 的删除抛 ValueError（种子助手不可删）

class EventRepo:
    async def append(self, session_id: str, event: SessionEvent) -> dict   # _id=f"{sid}:{seq}"
    async def list_events(self, session_id: str) -> list[SessionEvent]     # seq 升序

class RunRepo:           # status: running|completed|aborted|failed
class SessionRepo:       # user_id/assistant_id/title/archived/message_count
class FileRepo:          # user_id/path/size/mime/sha256

async def seed_assistants(store) -> None   # assistants 空时插 2 个：asst-research（全六工具）、
                                           # asst-data（仅 python.run/file.*），builtin=True；幂等
```

种子助手的 system_prompt：

- `asst-research`（科研助手）：「你是 SynlysAgent 科研助手，帮助科研人员完成文献检索、数据分析、文件处理等任务。回答保持准确、简洁，需要时主动使用工具。」
- `asst-data`（数据分析助手）：「你是数据分析助手。优先使用 python.run 工具对用户上传的数据做统计分析与可视化，结果图表保存到 output/ 目录并在回复中说明结论。」

测试要点（test_repos.py）：provider 加解密往返（带 Fernet key）、assistant builtin 删除被拒（ValueError）、event append 后 list 按 seq 升序、seed 幂等（跑两次仍 2 个）。

**Commit:** `feat(web): 六个 repository 与种子助手`

---

### Task 4: 共享认证

**Files:** Create `app/core/auth.py`、`app/api/__init__.py`、`app/api/deps.py`、`tests/test_auth.py`

**认证格式（与 AI4MS 逐字兼容，已从门户源码核实）**：

```
token = f"{base64url(payload_json, 无padding)}.{hmac_sha256_hex(secret, payload_b64)}"
payload = {"sub": user_id, "username": ..., "role": "admin"|"user", "iat": ..., "exp": ...}
```

`app/core/auth.py`：

```python
"""AI4MS 共享 HMAC token 解析 + sqlite 本地用户模式。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from app.core.settings import Settings


def _secret(settings: Settings) -> bytes:
    """HMAC secret：优先 AUTH_SECRET，否则按门户规则派生。"""
    if settings.auth_secret:
        return settings.auth_secret.encode()
    return hashlib.sha256(f"{Path.cwd()}_ai4ms_portal".encode()).hexdigest().encode()


def parse_token(token: str, settings: Settings) -> dict[str, Any] | None:
    """校验签名与过期，返回 payload（失败返回 None）。

    Args:
        token: `{payload_b64}.{hmac_hex}` 格式令牌。
        settings: 应用配置。
    """
    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("role") not in ("admin", "user"):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def issue_token(payload: dict[str, Any], settings: Settings) -> str:
    """签发 token（本地登录用；payload 自动补 iat/exp，exp=7天）。"""
    body = {**payload, "iat": int(time.time()), "exp": int(time.time()) + 7 * 86400}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(body, ensure_ascii=False).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"
```

**两种模式**：
- `STORAGE_BACKEND=mongodb`（生产）：`POST /auth/login` 校验 `ai4ms` 库 users（PBKDF2：`pbkdf2_sha256$260000$salt$hash`，`hashlib.pbkdf2_hmac("sha256", password, salt_hex, 260000).hex()`）→ `issue_token`；`/auth/me` 解析 token 后回查 users 确认 status=active
- `sqlite`（开发）：`DEV_AUTH_TOKEN` 设置时直接接受该 token（payload 固定 `{sub:"dev", username:"dev", role:"admin"}`）；否则 login 用 `local_users` 集合（username + PBKDF2 密码）

**deps.py**：`get_current_user(request) -> dict`（Authorization Bearer 或 URL `#token=` 无法用于 header——前端从 hash 提取后放 header；本端只认 Bearer）、`require_admin`、`auth_enabled=false` 时匿名放行 `{sub:"anon", role:"admin"}`（开发）。

测试：签发→解析往返、篡改 sig 拒绝、过期拒绝、PBKDF2 密码校验、require_admin 403。

**Commit:** `feat(web): AI4MS 兼容认证与本地开发模式`

---

### Task 5: 模型与助手管理 API

**Files:** Create `app/api/models_api.py`、`app/api/assistants_api.py`、Modify `app/main.py`（挂路由）、`tests/test_mgmt_api.py`

- `GET /api/v1/models`（全员，**不返回明文 key**，只带 `has_key: true`/name/base_url/model_id/enabled）；`POST/PATCH/DELETE /api/v1/models`（require_admin）
- `POST /api/v1/models/{id}/test`（require_admin）：临时构造 `OpenAICompatibleBackend`（key 解密）发一次最小请求（messages=[{"role":"user","content":"ping"}]、max_tokens=8），返回 `{ok, latency_ms, error}`——用 httpx 流式真实调用
- `GET /api/v1/assistants`（全员，含 model 名称联查）；`POST/PATCH/DELETE`（require_admin，builtin 不可删）
- 输入校验：base_url 必须 http(s)、tool_whitelist 校验存在于 registry.names、model_provider_id 校验存在且 enabled

测试（sqlite fixture + admin token）：CRUD 权限（普通用户 403）、models 列表无 key、test 端点 mock 上游（httpx MockTransport 注入到 backend 不可行——端点测试用 monkeypatch OpenAICompatibleBackend.stream 返回固定事件）。

**Commit:** `feat(web): 模型与助手管理 API（含连通性测试）`

---

### Task 6: 会话 API 与 SSE 对话端点（核心任务）

**Files:** Create `app/services/__init__.py`、`app/services/agent_service.py`、`app/api/sessions_api.py`、Modify `app/main.py`、`tests/test_chat_api.py`

**REST 部分**：`GET/POST /api/v1/sessions`（按 user_id 隔离，POST body `{assistant_id, title?}`，首条消息后自动生成标题=前 24 字）、`PATCH /sessions/{id}`（改名/归档）、`DELETE /sessions/{id}`（删 DB + JSONL 目录）、`GET /sessions/{id}/events`（读 JSONL 重放，返回事件数组）。

**agent_service.py 核心**（遵守契约 1-10）：

```python
"""SessionService：harness 组装、事件持久化与 SSE 流。"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from synlys_harness import (
    AgentConfig, EventLog, EventType, ModelProviderConfig, OpenAICompatibleBackend,
    RunSession, ToolPipeline, ToolRegistry, register_builtin_tools,
)

_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)
_PIPELINE = ToolPipeline(registry=_REGISTRY)


class TooManyRuns(Exception):
    """用户并发运行数超限。"""



class ActiveRun:
    """一次进行中的对话运行。"""

    def __init__(self) -> None:
        """初始化队列与事件。"""
        self.queue: asyncio.Queue = asyncio.Queue()
        self.done = asyncio.Event()
        self.session: RunSession | None = None


class AgentService:
    """对话运行编排（单例，挂 app.state）。"""

    def __init__(self, store, settings) -> None:
        """保存依赖。

        Args:
            store: DocumentStore。
            settings: 应用配置。
        """
        self._store = store
        self._settings = settings
        self._runs: dict[str, ActiveRun] = {}

    def _jsonl_path(self, session_id: str) -> Path:
        """会话事件文件路径。"""
        p = self._settings.data_root / "sessions" / session_id / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def chat(self, session_id: str, user: dict, assistant: dict,
                   provider_cfg: ModelProviderConfig, text: str) -> str:
        """启动一轮对话运行，返回 run_id（事件经 run_registry 取）。"""
        # 每用户并发限制：同用户 running 状态 run ≥ 2 时抛 TooManyRuns（API 层转 429）
        running = await self._store.runs.list("runs", filters={
            "user_id": user["sub"], "status": "running"})
        if len(running) >= 2:
            raise TooManyRuns("该用户已有 2 个运行中的对话")
        run_id = uuid.uuid4().hex[:12]
        active = ActiveRun()
        self._runs[run_id] = active

        async def jsonl_sink(event) -> None:
            """事件追加 JSONL（契约：不得抛异常）。"""
            try:
                line = event.model_dump_json() + "\n"
                self._jsonl_path(session_id).open("a", encoding="utf-8").write(line)
            except OSError:
                pass

        async def db_sink(event) -> None:
            """事件写 DB 副本（EventRepo.append，契约：不得抛异常）+ SSE 队列只 put。"""
            try:
                await self._event_repo.append(session_id, event)
            except Exception:
                pass
            await active.queue.put(event)

        log = EventLog(sinks=[jsonl_sink, db_sink])
        backend = OpenAICompatibleBackend(provider_cfg)
        session = RunSession(
            config=AgentConfig(
                system_prompt=assistant["system_prompt"],
                tool_names=assistant.get("tool_whitelist") or [],
            ),
            registry=_REGISTRY, pipeline=_PIPELINE, backend=backend,
            event_log=log, user_id=user["sub"], run_id=run_id,
            workspace_root=self._settings.data_root / "workspaces" / user["sub"],
            context_extra={"http_allowed_hosts": self._settings.allowed_hosts},
        )
        active.session = session
        asyncio.create_task(self._drive(run_id, session, text, user["sub"], session_id))
        return run_id

    async def _drive(self, run_id, session, text, user_id, session_id) -> None:
        """后台驱动 run 至完成（契约：aclosing 消费）。"""
        import contextlib

        run_doc = {"_id": run_id, "session_id": session_id, "user_id": user_id,
                   "status": "running", "started_at": time.time()}
        await self._store.runs.insert(run_doc)
        final_status = "completed"
        try:
            async with contextlib.aclosing(session.run(text)):
                async for _event in session.run(text):
                    pass  # 事件已由 sinks 处理
        except Exception:
            final_status = "failed"
        if session._cancel.is_set():
            final_status = "aborted"
        await self._store.runs.update(run_id, {"status": final_status, "ended_at": time.time()})
        active = self._runs.get(run_id)
        if active:
            await active.queue.put(None)  # 结束哨兵
            active.done.set()

    async def cancel(self, run_id: str) -> bool:
        """取消运行。"""
        active = self._runs.get(run_id)
        if active and active.session:
            active.session.cancel()
            return True
        return False
```

**SSE 端点**（sessions_api.py）：

```python
@router.post("/sessions/{sid}/messages")
async def send_message(sid: str, body: MessageIn, request: Request, user=Depends(get_current_user)):
    """启动 run 并以 SSE 流式返回事件（event: 事件类型，data: JSON，id: seq）。"""
    # 校验 session 归属/assistant/provider；chat() 拿 run_id
    async def gen():
        sent = 0
        while True:
            ev = await active.queue.get()
            if ev is None:
                break
            yield {"event": ev.type.value, "data": ev.model_dump_json(), "id": str(ev.seq)}
            sent += 1
    return EventResponse(gen(), media_type="text/event-stream")
```

实现细节：SSE 生成器 `async for` ActiveRun.queue，None 哨兵结束；**断连不 cancel**（设计 §7：run 由 `_drive` 独立 task 在后台执行完落盘，前端重连后经 `GET /sessions/{id}/events?after_seq=N` 补齐）；`POST /api/v1/runs/{run_id}/cancel` 仅响应用户显式停止。

**设计偏离声明**：设计文档 §7 写"LLM 超时/限流重试 1 次"，流式场景下自动重试会导致重复 delta 输出，V1 改为错误事件（code=llm_error）+ 前端"重试"按钮（重发该消息），语义等价且实现干净。

测试（test_chat_api.py，**mock provider**）：monkeypatch `OpenAICompatibleBackend.stream` 为脚本化 async gen（先 ToolCallChunk python.run `print(6*7)` 再 TextDelta）；走完整 HTTP：POST session → POST messages（SSE 收到 turn/start…tool/result 含 "42"…assistant/message/turn/end）；JSONL 文件存在且行数=事件数；cancel 端点生效（长脚本 provider + cancel → turn/aborted 事件）。

**Commit:** `feat(web): 会话 API 与 SSE 对话端点（harness 编排）`

---

### Task 7: 文件 API 与工作区

**Files:** Create `app/services/workspace.py`、`app/api/files_api.py`、Modify `app/main.py`、`tests/test_files_api.py`

- `workspace.py`：`workspace_root(settings, user_id)`（data/workspaces/{uid}，files/output/tmp 自动建）、`usage_bytes(root)`（rglob stat 求和）、`check_quota(root, incoming, quota)`（ValueError→413）
- `POST /api/v1/files`（multipart 多文件，存 `files/` 原名冲突加序号，sha256 入库）；`GET /api/v1/files`（用户列表）；`GET /files/{id}/download`（FileResponse）；`DELETE /files/{id}`（删记录+磁盘文件）
- 限制：单文件 50MB、扩展名黑名单（.exe/.bat/.cmd/.msi/.ps1）
- 测试：上传→列表→下载字节一致→删除；超配额 413；黑名单扩展名 422

**Commit:** `feat(web): 文件 API 与用户工作区配额`

---

### Task 8: 端到端链路测试与收尾

**Files:** Modify tests；Create 根目录 `ecosystem.config.cjs`、`apps/web/backend/README.md`

- [ ] E2E-1（spec §9 场景 2 的后端侧）：上传 CSV → 选数据分析助手 → SSE 对话"分析这份数据的分布并画图"（mock provider 返回固定 python.run 调用，代码读 `../files/*.csv` 算均值写 `output/result.txt`）→ 断言：tool_result ok、output 文件真实存在、事件 JSONL 完整
- [ ] E2E-2：SSE 断连恢复——中途断开后 `GET /sessions/{id}/events` 返回全部事件
- [ ] `ecosystem.config.cjs`：pm2 app `synlys-agent`（cwd apps/web/backend，interpreter conda synlysagent 的 python，script uvicorn）
- [ ] `README.md`：启动/配置/部署说明（.env 各项、两种存储、与 AI4MS 门户对接 #token、PM2）
- [ ] 全量测试 + 手动启动冒烟（health 端点）
- [ ] **Commit** `feat(web): 端到端测试与部署配置`

---

## 完成标准（Plan 2 DoD）

- [ ] `conda run -n synlysagent python -m pytest apps/web/backend/tests -v` 全绿
- [ ] 手动 `uvicorn app.main:app --port 8005` 启动 + `/api/health` OK
- [ ] SSE 对话端到端（mock provider）含工具调用全链路
- [ ] 10 条 harness 接入契约逐条落实（实现中自查）

## 附录 A：harness API（实测签名，e0bc4b6）

```python
RunSession(config: AgentConfig, registry: ToolRegistry, pipeline: ToolPipeline,
           backend: LLMBackend, event_log: EventLog, user_id: str, run_id: str,
           hooks: ExtensionHooks | None = None, workspace_root: Path | None = None,
           context_extra: dict | None = None)
RunSession.run(user_text) -> AsyncIterator[SessionEvent]   # async generator；用 aclosing 包裹
RunSession.cancel() / .steer(text) / .last_usage
EventLog(sinks: list[Callable[[SessionEvent], Awaitable]] | None)
EventLog.append(type_: EventType, payload: dict) -> SessionEvent   # 串行 await sinks
SessionEvent(seq, type: EventType, payload, ts)  # frozen；model_dump_json 可直接 JSONL
ModelProviderConfig(name, base_url, api_key, model_id)
ToolRegistry().names / .llm_schemas(allowed) / .register(fn)
AgentConfig(system_prompt, tool_names=[], max_steps=25, model_id="")
```
