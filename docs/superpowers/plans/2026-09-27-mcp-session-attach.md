# MCP 会话级附加与公共 MCP 管理 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 聊天窗 `+` 面板可按会话勾选附加 MCP；管理员可在后台维护公共 MCP（catalog 第 4 类，支持 stdio/streamable-http）；运行时复用既有 per-run 工具装配。

**Architecture:** 三块延伸，零新架构：catalog loader 增第 4 类 `mcp`（manifest + policy + 市场安装模型照抄技能插件）；`McpService` 增 transport 抽象（HTTP 走既有 httpx 无状态路径，stdio 新增子进程 JSON-RPC，仅公共 MCP）；`agent_service` 的 MCP 装配来源从「专家 mcp_refs」扩展为「会话 enabled_mcp ∪ 专家 mcp_refs」。

**Tech Stack:** FastAPI + 自研 httpx/asyncio JSON-RPC（**不引入 mcp python SDK**）、React 19 + TS + Zustand。

**Spec:** `docs/superpowers/specs/2026-09-27-mcp-session-attach-design.md`

## Global Constraints

- harness（`packages/synlys-harness/`）零改动——MCP 全部在宿主侧（spec §1）
- 不引入 mcp python SDK；stdio 客户端手写（spawn + 按行 JSON-RPC + initialize 握手）
- 用户自建 MCP 仅 streamable-http；stdio 仅公共 catalog 条目（spec §2）
- 工具命名 `mcp.<mcp_id>.<safe_name>`，与已注册工具撞名时跳过（现状 `agent_service.py:438`）
- 会话 `enabled_mcp` 语义与 `enabled_plugins` 同构：缺省/null/[] = 不附加；PATCH 切换即落库
- 勾选不能放大可见性：PATCH 校验 + 运行期二次判定（管理员事后 hidden 的静默跳过）
- 同名冲突用户自建优先于公共条目；用户自建条目存在但停用时整体跳过（不回落公共）
- 注释规范：函数 docstring 中文 + Args/Returns；PEP8；前端照既有组件样式 token（`--sa-*`）
- 测试：`cd apps/web/backend && conda run -n synlysagent python -m pytest -v`；前端 `npm run build` 通过即交付（用户自测，不跑 playwright）
- 提交消息：简洁标题 + 要点列表，不加 AI 共同编辑字样；**不更新版本号**（推送 main 时统一升 1.5.0）

## Review Focus

1. **用户停用/删除了会话已勾选的自建 MCP** → 装配静默跳过不报错（Task 6 测）
2. **管理员事后 hidden 公共 MCP**（会话已勾选）→ 运行期静默跳过（Task 6 测）；PATCH 再勾选被 404 拒绝（Task 5 测）
3. **stdio 子进程运行中死亡** → 该次调用返回 `ok=False` 给模型，下次调用懒重建（Task 3 测）
4. **同名自建与公共 MCP** → 自建优先，公共跳过；自建存在但 `enabled=False` → 整体跳过（Task 4 测）
5. **enabled_mcp 里塞入不存在/他人的 id** → PATCH 404 拒绝（Task 5 测）
6. **manifest 非法（transport 未知/缺连接字段/id 非 kebab-case）** → 扫描告警跳过 + 管理端写入 422（Task 1/8 测）

---

### Task 1: catalog 第 4 类 — McpPackage 与 _scan_mcps

**Files:**
- Modify: `apps/web/backend/app/catalog/loader.py`
- Modify: `apps/web/backend/app/catalog/items.py`
- Test: `apps/web/backend/tests/test_catalog_mcp_scan.py`

**Interfaces:**
- Consumes: 现有 `_merge` / `_warn_dup` / `catalog_roots`（loader.py）
- Produces:
  - `McpPackage`（frozen dataclass，字段见步骤 3）
  - `parse_mcp_manifest(data: dict, directory: Path) -> McpPackage`（`ValueError` = 非法；Task 8 管理写入复用它做校验）
  - `CatalogIndex.mcps: dict[str, McpPackage]`（Task 2/7/8 消费）
  - `KINDS = ("expert", "skill", "plugin", "mcp")`（me_api/catalog api 的 market/policy/安装端点自动泛化）
  - `CatalogService.mcps` property 与 `list_items("mcp")`（Task 7/8 消费）

- [ ] **Step 1: 写失败测试**

```python
"""catalog MCP 条目扫描测试。"""
import json

from app.catalog.loader import CatalogIndex, scan_catalog


def _write(root, mcp_id, data):
    d = root / "mcp" / mcp_id
    d.mkdir(parents=True)
    (d / "mcp.json").write_text(json.dumps(data), encoding="utf-8")


HTTP_OK = {
    "id": "remote-api", "name": "远程 API", "description": "d",
    "transport": "streamable-http", "url": "https://example.com/mcp",
}
STDIO_OK = {
    "id": "fs", "name": "文件系统", "description": "d",
    "transport": "stdio", "command": "npx", "args": ["-y", "x"],
}


def test_scan_mcps_ok(tmp_path):
    _write(tmp_path, "remote-api", HTTP_OK)
    _write(tmp_path, "fs", STDIO_OK)
    index = scan_catalog([tmp_path])
    assert set(index.mcps) == {"remote-api", "fs"}
    assert index.mcps["fs"].command == "npx"
    assert index.mcps["remote-api"].headers == {}


def test_scan_mcps_invalid_entries_skipped(tmp_path):
    # transport 未知 / stdio 缺 command / http url 非 http(s) / id 非 kebab
    _write(tmp_path, "bad-transport", {**HTTP_OK, "id": "bad-transport", "transport": "sse"})
    _write(tmp_path, "no-command", {**STDIO_OK, "id": "no-command", "command": ""})
    _write(tmp_path, "bad-url", {**HTTP_OK, "id": "bad-url", "url": "ftp://x"})
    _write(tmp_path, "Bad_Id", {**HTTP_OK, "id": "Bad_Id"})
    index = scan_catalog([tmp_path])
    assert index.mcps == {}


def test_scan_mcps_data_root_overrides_repo_root(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    _write(repo, "dup", {**HTTP_OK, "id": "dup", "name": "仓库版"})
    _write(data, "dup", {**HTTP_OK, "id": "dup", "name": "数据目录版"})
    index = scan_catalog([repo, data])
    assert index.mcps["dup"].name == "数据目录版"


def test_items_kinds_and_list_items(tmp_path):
    from app.catalog.items import KINDS, CatalogService
    _write(tmp_path, "remote-api", HTTP_OK)
    service = CatalogService(scan_catalog([tmp_path]))
    assert "mcp" in KINDS
    items = service.list_items("mcp")
    assert [i.id for i in items] == ["remote-api"]
    assert items[0].kind == "mcp"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_catalog_mcp_scan.py -v`
Expected: FAIL（`AttributeError: 'CatalogIndex' object has no attribute 'mcps'` / ImportError）

- [ ] **Step 3: 实现 loader 扩展**

`loader.py` 顶部常量区（PLUGINS_DIR 之后）加：

```python
MCP_DIR = "mcp"
MCP_MANIFEST = "mcp.json"
MCP_REQUIRED = ("id", "name", "description", "transport")
MCP_TRANSPORTS = ("stdio", "streamable-http")
MCP_DUP_MSG = "MCP id 重复，后者覆盖前者"
```

`PluginPackage` 之后加 dataclass（frozen 但 list/dict 字段用 `field(default_factory)`，同 PluginPackage 模式）：

```python
@dataclass(frozen=True)
class McpPackage:
    """公共 MCP 包（catalog/mcp/<id>/mcp.json）。

    Attributes:
        id: MCP id（kebab-case，= 目录名；工具命名空间 mcp.<id>.<tool>）。
        name: 显示名。
        description: 描述。
        transport: stdio | streamable-http（用户自建恒为后者，此处为公共条目）。
        command: stdio 启动命令。
        args: stdio 命令参数。
        cwd: stdio 工作目录（空 = 继承）。
        env: stdio 子进程环境变量。
        url: streamable-http 服务地址。
        headers: HTTP 请求头（可含凭证；管理员信任级，一期明文 manifest）。
        bearer_token: Bearer 凭证（空 = 无）。
        timeout_s: 单次调用超时秒数。
        directory: 包目录绝对路径。
    """

    id: str
    name: str
    description: str
    transport: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    bearer_token: str = ""
    timeout_s: float = 60.0
    directory: Path = field(default_factory=Path)
```

加解析函数（**单入口**：扫描与管理端写入共用，保证校验口径一致）：

```python
def parse_mcp_manifest(data: dict, directory: Path) -> McpPackage:
    """解析并校验一份 MCP manifest。

    Args:
        data: manifest 字典（mcp.json 内容）。
        directory: 包目录（写入返回值的 directory 字段）。

    Returns:
        McpPackage。

    Raises:
        ValueError: 任一字段非法（id 非 kebab-case、缺必填、transport 未知、
            stdio 缺 command、http url 非 http(s) 等）。
    """
    from app.services.mcp_service import MCP_ID_OK

    mcp_id = str(data.get("id") or "").strip()
    if not MCP_ID_OK.fullmatch(mcp_id):
        raise ValueError(f"MCP id 必须是 kebab-case: {mcp_id!r}")
    missing = [k for k in MCP_REQUIRED if not str(data.get(k) or "").strip()]
    if missing:
        raise ValueError(f"MCP manifest 缺必填字段: {missing}")
    transport = str(data["transport"]).strip()
    if transport not in MCP_TRANSPORTS:
        raise ValueError(f"MCP transport 仅支持 {MCP_TRANSPORTS}: {transport!r}")
    if transport == "stdio" and not str(data.get("command") or "").strip():
        raise ValueError("stdio 类型必须提供 command")
    url = str(data.get("url") or "").strip()
    if transport == "streamable-http" and not url.startswith(("http://", "https://")):
        raise ValueError("streamable-http 类型的 url 必须以 http:// 或 https:// 开头")
    return McpPackage(
        id=mcp_id,
        name=str(data["name"]).strip(),
        description=str(data["description"]).strip(),
        transport=transport,
        command=str(data.get("command") or "").strip(),
        args=[str(a) for a in data.get("args") or []],
        cwd=str(data.get("cwd") or "").strip(),
        env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
        url=url,
        headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        bearer_token=str(data.get("bearer_token") or ""),
        timeout_s=float(data.get("timeout_s") or 60.0),
        directory=directory,
    )
```

`_scan_mcps`（照 `_scan_plugins` 的告警跳过模式）：

```python
def _scan_mcps(root: Path) -> dict[str, McpPackage]:
    """扫描 `<root>/mcp` 下的公共 MCP 包，解析 manifest。

    Args:
        root: catalog 根目录。

    Returns:
        {MCP id: McpPackage}；非法 manifest 只告警跳过，不抛异常。
    """
    packages: dict[str, McpPackage] = {}
    mcps_dir = root / MCP_DIR
    if not mcps_dir.is_dir():
        return packages
    for entry in sorted(mcps_dir.iterdir()):
        manifest_path = entry / MCP_MANIFEST
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("MCP manifest 解析失败，已跳过 %s: %s", entry.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("MCP manifest 非对象，已跳过 %s", entry.name)
            continue
        try:
            pkg = parse_mcp_manifest(data, entry.resolve())
        except ValueError as exc:
            logger.warning("MCP manifest 非法，已跳过 %s: %s", entry.name, exc)
            continue
        packages[pkg.id] = pkg
    return packages
```

`scan_catalog` 改两处：`CatalogIndex(experts={}, skills={}, plugins={}, mcps={})`，循环体加 `_merge(index.mcps, _scan_mcps(root), MCP_DUP_MSG)`。`CatalogIndex` dataclass 加字段 `mcps: dict[str, McpPackage]`（docstring 同步）。模块头注释的目录清单加一行 `<root>/mcp/<id>/mcp.json → 公共 MCP`。

- [ ] **Step 4: 实现 items 扩展**

`items.py`：`KINDS = ("expert", "skill", "plugin", "mcp")`；模块 docstring 补一行 MCP 条目说明；`CatalogService` 加 `mcps` property（返回 `self._index.mcps`，照 `plugins` property 写法）；`list_items` 加分支 `if kind == "mcp": return self._mcps()`；加 `_mcps()`（照 `_plugins()` 排序返回 `CatalogItem(kind="mcp", ...)`）。

- [ ] **Step 5: 跑测试通过**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_catalog_mcp_scan.py -v`
Expected: 4 PASS

- [ ] **Step 6: 跑全量回归（catalog 相关既有测试不得破）**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest -v -k "catalog or capability or market"`
Expected: 全 PASS（`all_items()` 现在会多枚举 mcp 类，当前无 mcp 条目故行为不变）

- [ ] **Step 7: Commit**

```bash
git add apps/web/backend/app/catalog/loader.py apps/web/backend/app/catalog/items.py apps/web/backend/tests/test_catalog_mcp_scan.py
git commit -m "catalog 新增第 4 类条目 mcp

- loader 加 McpPackage 与 parse_mcp_manifest/_scan_mcps（stdio/streamable-http）
- KINDS 扩展为 expert/skill/plugin/mcp，CatalogService 枚举打通"
```

---

### Task 2: McpService 公共条目接入 — catalog 注入、HTTP 发现与状态缓存

**Files:**
- Modify: `apps/web/backend/app/services/mcp_service.py`
- Modify: `apps/web/backend/app/main.py:100`
- Test: `apps/web/backend/tests/test_mcp_public_http.py`

**Interfaces:**
- Consumes: Task 1 的 `McpPackage`
- Produces:
  - `McpService(store, fernet_key, client_factory=None, catalog_mcps=None)` — 第 4 参：`Callable[[], dict[str, McpPackage]]`（main.py 注入 `lambda: capability_service.catalog.mcps`，实时求值以配合 Task 8 热重载）
  - `McpService.public_status(mcp_id: str) -> dict`（`{status, last_error, tool_count, checked_at}`；Task 7 面板端点消费）
  - `McpService.discover_public_tools(mcp_id: str) -> list[dict]`（Task 4 消费）
  - `McpService.test_public_connection(mcp_id: str) -> dict`（Task 8 管理端点消费）

- [ ] **Step 1: 写失败测试**

```python
"""公共 MCP（catalog 条目）HTTP 发现与状态缓存测试。"""
from pathlib import Path

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService

PKG_HTTP = McpPackage(
    id="pub-http", name="公共 HTTP", description="d",
    transport="streamable-http", url="https://example.com/mcp",
    directory=Path("."),
)


class _FakeStore:
    async def get(self, *_a, **_k):
        return None


def _service(tools_payload):
    captured = {}

    class _Client:
        def __init__(self, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            body = {"result": {"tools": tools_payload}} if json.get("method") == "tools/list" \
                else {"result": {}}
            class _Resp:
                status_code = 200
                headers = {"content-type": "application/json"}
                content = b"x"
                def raise_for_status(self):
                    return None
                def json(self):
                    return body
            return _Resp()

    return McpService(
        _FakeStore(), "",
        client_factory=lambda: _Client(),
        catalog_mcps=lambda: {"pub-http": PKG_HTTP},
    ), captured


@pytest.mark.asyncio
async def test_discover_public_tools_http():
    service, captured = _service([{"name": "echo", "description": "回声",
                                   "inputSchema": {"type": "object"}}])
    tools = await service.discover_public_tools("pub-http")
    assert captured["url"] == "https://example.com/mcp"
    assert tools[0]["name"] == "echo"
    assert tools[0]["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_test_public_connection_updates_status():
    service, _ = _service([])
    await service.test_public_connection("pub-http")
    status = service.public_status("pub-http")
    assert status["status"] == "connected"
    assert status["tool_count"] == 0


@pytest.mark.asyncio
async def test_unknown_public_mcp_raises():
    service, _ = _service([])
    with pytest.raises(KeyError):
        await service.discover_public_tools("nope")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_public_http.py -v`
Expected: FAIL（TypeError: 意外的关键字参数 `catalog_mcps`）

- [ ] **Step 3: 实现**

`mcp_service.py`：

`__init__` 增加第 4 参并保存（docstring 同步）：

```python
    def __init__(self, store: Any, fernet_key: str,
                 client_factory: Callable[[], httpx.AsyncClient] | None = None,
                 catalog_mcps: Callable[[], dict[str, Any]] | None = None) -> None:
        """保存存储、密钥、HTTP 客户端工厂与公共 MCP 目录。

        Args:
            store: DocumentStore 实例。
            fernet_key: Fernet key；空表示开发环境明文存储。
            client_factory: 测试或定制 HTTP 客户端工厂。
            catalog_mcps: 公共 MCP 目录提供者（{id: McpPackage}，实时求值，
                配合管理端写入后的 catalog 热重载）；None 表示无公共条目。
        """
        # ...原三行不动...
        self._catalog_mcps = catalog_mcps or (lambda: {})
        self._public_status: dict[str, dict] = {}
```

新增方法（放在 `runtime_connections` 之后、`_request` 之前；类方法顺序约定 public 在前）：

```python
    def _public_pkg(self, mcp_id: str) -> Any:
        """取公共 MCP 包。

        Args:
            mcp_id: MCP id。

        Returns:
            McpPackage。

        Raises:
            KeyError: 公共条目不存在。
        """
        pkg = self._catalog_mcps().get(mcp_id)
        if pkg is None:
            raise KeyError(f"公共 MCP 不存在: {mcp_id}")
        return pkg

    @staticmethod
    def _public_config(pkg: Any) -> dict:
        """把公共包转成 HTTP 调用配置（与用户自建 resolved() 同构）。"""
        return {
            "id": pkg.id, "name": pkg.name, "url": pkg.url,
            "headers": dict(pkg.headers), "bearer_token": pkg.bearer_token,
        }

    def public_status(self, mcp_id: str) -> dict:
        """公共 MCP 的探测状态（无记录视为未检测）。"""
        return dict(self._public_status.get(mcp_id) or {
            "status": "unchecked", "last_error": "", "tool_count": 0, "checked_at": None,
        })

    async def discover_public_tools(self, mcp_id: str) -> list[dict]:
        """连接公共 MCP 并读取工具列表（http 走无状态短连接；stdio 由 Task 3 接管）。

        Raises:
            KeyError: 公共条目不存在。
            RuntimeError: 连接或协议失败。
        """
        pkg = self._public_pkg(mcp_id)
        if pkg.transport == "stdio":
            raise RuntimeError("stdio transport 由专用客户端处理（Task 3 接入）")
        result = await self._request(self._public_config(pkg), "tools/list", {})
        return [self._normalize_tool(item)
                for item in result.get("tools") or [] if isinstance(item, dict)]

    async def test_public_connection(self, mcp_id: str) -> dict:
        """测试公共 MCP 连接并更新状态缓存（stdio 由 Task 3 接管）。"""
        try:
            tools = await self.discover_public_tools(mcp_id)
        except Exception as exc:
            self._public_status[mcp_id] = {
                "status": "error", "last_error": str(exc),
                "tool_count": 0, "checked_at": time.time(),
            }
            raise
        self._public_status[mcp_id] = {
            "status": "connected", "last_error": "",
            "tool_count": len(tools), "checked_at": time.time(),
        }
        return {"ok": True, "tools": tools}
```

`main.py:100`（`app.state.mcp_service = McpService(store, settings.fernet_key)`）改为注入目录提供者——注意此时 `capability_service` 尚未创建（`main.py:137`），改为**在其创建之后**回填：

```python
    app.state.mcp_service = McpService(store, settings.fernet_key)
    # ...capability_service 创建之后（main.py:137 附近）补：
    app.state.mcp_service.set_catalog_provider(
        lambda: app.state.capability_service.catalog.mcps)
```

`McpService` 加 setter（构造参数保留供测试直传）：

```python
    def set_catalog_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        """注入公共 MCP 目录提供者（main 装配期回调，晚于构造）。"""
        self._catalog_mcps = provider
```

- [ ] **Step 4: 跑测试通过**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_public_http.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/backend/app/services/mcp_service.py apps/web/backend/app/main.py apps/web/backend/tests/test_mcp_public_http.py
git commit -m "McpService 接入公共 catalog 条目

- catalog_mcps 目录提供者（实时求值，支持热重载）与 set_catalog_provider
- 公共条目 HTTP 工具发现 discover_public_tools + 状态缓存 test_public_connection"
```

---

### Task 3: stdio 子进程 JSON-RPC 客户端

**Files:**
- Create: `apps/web/backend/app/services/mcp_stdio.py`
- Modify: `apps/web/backend/app/services/mcp_service.py`
- Modify: `apps/web/backend/app/main.py`（lifespan 关闭钩子）
- Test: `apps/web/backend/tests/test_mcp_stdio.py`

**Interfaces:**
- Consumes: Task 1 `McpPackage`（`command/args/cwd/env/timeout_s`）、Task 2 `discover_public_tools/test_public_connection` 的 stdio 分支占位
- Produces:
  - `mcp_stdio.connect_stdio(pkg) -> StdioMcpClient`（async 工厂，版本回退：`2025-11-25` 失败重试 `2024-11-05`）
  - `StdioMcpClient.request(method, params) -> dict`、`.call_tool(name, arguments) -> dict`、`.close()`
  - `McpService._stdio_request(mcp_id, method, params) -> dict`（Task 4 的 stdio 调用入口；懒 spawn + 失败销毁）
  - `McpService.aclose()`（应用退出清理全部子进程）
  - `discover_public_tools` / `test_public_connection` 对 stdio 生效（改掉 Task 2 的 RuntimeError 占位）

- [ ] **Step 1: 写失败测试**

测试用临时 Python 脚本做假 stdio MCP server（读行 JSON-RPC，回 initialize/tools/list/tools/call）：

```python
"""stdio MCP 子进程客户端测试（用临时假 server 脚本）。"""
import json
import textwrap

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService
from app.services.mcp_stdio import connect_stdio

FAKE_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        method, mid = msg["method"], msg["id"]
        if method == "initialize":
            result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {},
                      "serverInfo": {"name": "fake", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "回声",
                                 "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text",
                                   "text": json.dumps(msg["params"]["arguments"])}],
                      "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
        sys.stdout.flush()
""")


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "fake_mcp_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def _pkg(server_script):
    import sys
    return McpPackage(
        id="fake", name="假服务", description="d", transport="stdio",
        command=sys.executable, args=[str(server_script)], timeout_s=10.0,
        directory=server_script.parent,
    )


@pytest.mark.asyncio
async def test_stdio_handshake_and_tools(server_script):
    client = await connect_stdio(_pkg(server_script))
    try:
        tools = await client.request("tools/list", {})
        assert tools["tools"][0]["name"] == "echo"
        result = await client.call_tool("echo", {"a": 1})
        assert json.loads(result["content"][0]["text"]) == {"a": 1}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_dead_process_lazy_restart(server_script):
    """进程死亡后 request 报错，McpService 下次调用懒重建。"""
    class _Store:
        async def get(self, *_a, **_k):
            return None

    service = McpService(_Store(), "",
                         catalog_mcps=lambda: {"fake": _pkg(server_script)})
    tools = await service.discover_public_tools("fake")
    assert tools[0]["name"] == "echo"
    # 杀掉子进程模拟死亡
    proc = service._stdio_clients["fake"]
    proc._process.kill()
    await proc._process.wait()
    with pytest.raises(Exception):
        await service._stdio_request("fake", "tools/list", {})
    # 懒重建：再次调用成功
    tools = await service.discover_public_tools("fake")
    assert tools[0]["name"] == "echo"
    await service.aclose()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_stdio.py -v`
Expected: FAIL（ModuleNotFoundError: app.services.mcp_stdio）

- [ ] **Step 3: 实现 mcp_stdio.py**

```python
"""stdio MCP 子进程客户端（按行 JSON-RPC，不依赖 mcp SDK）。

协议：spawn 子进程，stdin/stdout 按行读写 JSON-RPC；启动即 initialize 握手 +
notifications/initialized。进程生命周期由调用方（McpService）管理。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 握手版本回退序：新版本失败（服务端拒绝）时换旧版本重建进程重试一次
PROTOCOL_VERSIONS = ("2025-11-25", "2024-11-05")
CLIENT_INFO = {"name": "Synlora", "version": "0.14"}


class StdioMcpClient:
    """一个 stdio MCP 子进程连接。"""

    def __init__(self, command: str, args: list[str], cwd: str = "",
                 env: dict[str, str] | None = None, timeout_s: float = 60.0) -> None:
        """保存连接参数（未启动）。

        Args:
            command: 启动命令。
            args: 命令参数。
            cwd: 工作目录（空 = 继承当前进程）。
            env: 附加环境变量（叠加在 os.environ 之上）。
            timeout_s: 单次请求超时秒数。
        """
        self._command = command
        self._args = list(args)
        self._cwd = cwd or None
        self._env = {**env} if env else {}
        self._timeout_s = timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        """进程是否存活。"""
        return self._process is not None and self._process.returncode is None

    async def start(self, protocol_version: str) -> None:
        """spawn 子进程并完成 initialize 握手。

        Args:
            protocol_version: MCP 协议版本（握手失败由调用方换版本重试）。

        Raises:
            RuntimeError: spawn 失败、握手超时或服务端返回错误。
        """
        import os
        self._process = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            cwd=self._cwd,
            env={**os.environ, **self._env},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # stderr 只记录不阻断：假 server 打日志到 stderr 不该撑爆管道
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        init_id = uuid.uuid4().hex
        self._send({
            "jsonrpc": "2.0", "id": init_id, "method": "initialize",
            "params": {"protocolVersion": protocol_version, "capabilities": {},
                       "clientInfo": CLIENT_INFO},
        })
        await asyncio.wait_for(self._read_until(init_id), timeout=self._timeout_s)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def request(self, method: str, params: dict) -> dict:
        """发一个 JSON-RPC 请求并等待对应 id 的响应。

        Raises:
            RuntimeError: 进程已死、超时或服务端返回 error。
        """
        if not self.alive:
            raise RuntimeError("MCP 子进程已退出")
        request_id = uuid.uuid4().hex
        self._send({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params})
        return await asyncio.wait_for(
            self._read_until(request_id), timeout=self._timeout_s)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用远程工具（tools/call）。"""
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        """终止子进程并停止 stderr 泵。"""
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None

    def _send(self, payload: dict) -> None:
        """写一行 JSON 到 stdin。"""
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        assert self._process.stdin is not None

    async def _read_until(self, request_id: str) -> dict:
        """逐行读 stdout 直到匹配 id 的响应（忽略通知行）。"""
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                raise RuntimeError("MCP 子进程 stdout 已关闭")
            try:
                payload = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or str(payload.get("id")) != request_id:
                continue
            if payload.get("error"):
                error = payload["error"]
                raise RuntimeError(str(
                    error.get("message") if isinstance(error, dict) else error))
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("MCP 响应缺少 result")
            return result

    async def _drain_stderr(self) -> None:
        """持续读 stderr 并按行记日志（防止子进程因管道写满而阻塞）。"""
        assert self._process is not None and self._process.stderr is not None
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            logger.info("MCP stderr: %s", line.decode("utf-8", "replace").rstrip())


async def connect_stdio(pkg: Any) -> StdioMcpClient:
    """按版本回退序连接 stdio MCP 服务。

    Args:
        pkg: McpPackage（command/args/cwd/env/timeout_s）。

    Returns:
        已完成握手的 StdioMcpClient。

    Raises:
        RuntimeError: 全部版本握手失败。
    """
    last_error: Exception | None = None
    for version in PROTOCOL_VERSIONS:
        client = StdioMcpClient(
            pkg.command, list(pkg.args or []), pkg.cwd,
            dict(pkg.env or {}), float(pkg.timeout_s or 60.0))
        try:
            await client.start(version)
            return client
        except Exception as exc:  # 该版本握手失败：销毁进程换下一版本
            await client.close()
            last_error = exc
    raise RuntimeError(f"stdio MCP 握手失败（{pkg.id}）: {last_error}")
```

- [ ] **Step 4: McpService 集成 stdio**

`mcp_service.py`：`__init__` 增 `self._stdio_clients: dict[str, Any] = {}` 与 `self._stdio_locks: dict[str, asyncio.Lock] = {}`（文件头补 `import asyncio`）；新增方法：

```python
    async def _stdio_request(self, mcp_id: str, method: str, params: dict) -> dict:
        """对公共 stdio MCP 发请求：懒 spawn + 单飞锁 + 失败销毁（下次重建）。

        Raises:
            KeyError: 公共条目不存在。
            RuntimeError: 连接或协议失败。
        """
        pkg = self._public_pkg(mcp_id)
        if pkg.transport != "stdio":
            raise RuntimeError(f"MCP {mcp_id} 不是 stdio transport")
        lock = self._stdio_locks.setdefault(mcp_id, asyncio.Lock())
        async with lock:
            client = self._stdio_clients.get(mcp_id)
            if client is None or not client.alive:
                if client is not None:
                    await client.close()
                client = await connect_stdio(pkg)
                self._stdio_clients[mcp_id] = client
            try:
                return await client.request(method, params)
            except Exception:
                # 死连接就地销毁：下次调用走重建，不再等一轮超时
                await client.close()
                self._stdio_clients.pop(mcp_id, None)
                raise

    async def aclose(self) -> None:
        """应用退出清理：关闭全部 stdio 子进程。"""
        for client in list(self._stdio_clients.values()):
            try:
                await client.close()
            except Exception:
                pass
        self._stdio_clients.clear()
```

`discover_public_tools` 的 stdio 分支替换 RuntimeError 占位：

```python
        if pkg.transport == "stdio":
            result = await self._stdio_request(mcp_id, "tools/list", {})
        else:
            result = await self._request(self._public_config(pkg), "tools/list", {})
        return [self._normalize_tool(item)
                for item in result.get("tools") or [] if isinstance(item, dict)]
```

`main.py` lifespan 的 shutdown 段（找 `finally` / `yield` 之后的清理代码，与现有清理同处）加：

```python
        await app.state.mcp_service.aclose()
```

- [ ] **Step 5: 跑测试通过**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_stdio.py tests/test_mcp_public_http.py -v`
Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/backend/app/services/mcp_stdio.py apps/web/backend/app/services/mcp_service.py apps/web/backend/app/main.py apps/web/backend/tests/test_mcp_stdio.py
git commit -m "新增 stdio MCP 子进程客户端

- mcp_stdio.StdioMcpClient：按行 JSON-RPC、握手版本回退、stderr 泵
- McpService._stdio_request 懒 spawn + 单飞锁 + 死连接销毁重建
- lifespan 退出清理子进程（aclose）"
```

---

### Task 4: 统一解析 — resolve_runtime_mcps 与 call_runtime_tool

**Files:**
- Modify: `apps/web/backend/app/services/mcp_service.py`
- Test: `apps/web/backend/tests/test_mcp_resolve.py`

**Interfaces:**
- Consumes: Task 1/2/3 全部产出
- Produces:
  - `McpService.resolve_runtime_mcps(user_id, mcp_ids, visible_catalog_ids) -> list[dict]` — 返回项 `{"id", "name", "source": "user"|"catalog", "transport", "tools": [{name, description, input_schema}]}`
  - `McpService.call_runtime_tool(user_id, source, mcp_id, tool_name, arguments) -> dict`（返回原始 MCP tools/call result；Task 6 的执行闭包消费）

- [ ] **Step 1: 写失败测试**

```python
"""resolve_runtime_mcps 来源合并与优先级测试。"""
from pathlib import Path

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService

PKG = McpPackage(id="dup", name="公共版", description="d",
                 transport="streamable-http", url="https://pub.example.com/mcp",
                 directory=Path("."))


class _Store:
    def __init__(self, docs):
        self._docs = docs

    async def get(self, _col, doc_id):
        return self._docs.get(doc_id)


def _service(user_docs, tools_payload):
    class _Client:
        def __init__(self, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, headers=None, json=None):
            method = json.get("method")
            body = ({"result": {"tools": tools_payload}} if method == "tools/list"
                    else {"result": {"content": [{"type": "text", "text": "ok"}]}})
            class _Resp:
                status_code = 200
                headers = {"content-type": "application/json"}
                content = b"x"
                def raise_for_status(self): return None
                def json(self): return body
            return _Resp()

    return McpService(_Store(user_docs), "",
                      client_factory=lambda: _Client(),
                      catalog_mcps=lambda: {"dup": PKG})


def _user_doc(enabled=True, tools=None):
    return {"user_id": "u1", "mcp_id": "dup", "name": "自建版",
            "url": "https://mine.example.com/mcp", "enabled": enabled,
            "headers_secret": None, "bearer_token_secret": None,
            "tools": tools or []}


@pytest.mark.asyncio
async def test_user_owned_shadows_catalog_same_id():
    service = _service({"u1:dup": _user_doc()}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert len(rows) == 1
    assert rows[0]["source"] == "user"
    assert rows[0]["name"] == "自建版"


@pytest.mark.asyncio
async def test_user_owned_disabled_skips_entirely():
    """自建存在但停用 → 整体跳过，不回落公共同名条目。"""
    service = _service({"u1:dup": _user_doc(enabled=False)}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert rows == []


@pytest.mark.asyncio
async def test_catalog_used_when_no_user_doc_and_visible():
    service = _service({}, [{"name": "pub-tool", "description": "d",
                             "inputSchema": {"type": "object"}}])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids={"dup"})
    assert rows[0]["source"] == "catalog"
    assert rows[0]["tools"][0]["name"] == "pub-tool"


@pytest.mark.asyncio
async def test_catalog_invisible_skipped():
    service = _service({}, [])
    rows = await service.resolve_runtime_mcps("u1", ["dup"], visible_catalog_ids=set())
    assert rows == []


@pytest.mark.asyncio
async def test_call_runtime_tool_dispatches_by_source():
    service = _service({}, [])
    result = await service.call_runtime_tool(
        "u1", "catalog", "dup", "pub-tool", {"x": 1})
    assert result["content"][0]["text"] == "ok"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_resolve.py -v`
Expected: FAIL（AttributeError: resolve_runtime_mcps 不存在）

- [ ] **Step 3: 实现**

`mcp_service.py` 新增（放在 `runtime_connections` 之后）：

```python
    async def resolve_runtime_mcps(self, user_id: str, mcp_ids: list[str],
                                   visible_catalog_ids: "set[str]") -> list[dict]:
        """解析本轮要附加的 MCP 集合（自建优先、公共按可见性、失败跳过）。

        对每个 id（去重）：用户自建文档存在即遮蔽公共同名条目（停用则整体
        跳过，不回落）；否则公共条目须在 visible_catalog_ids 内才使用。
        连接失败/无工具的条目静默跳过（专家 mcp_refs 与会话勾选共用此口径）。

        Args:
            user_id: 用户 sub。
            mcp_ids: 本轮引用的 MCP id（会话勾选 ∪ 专家 mcp_refs）。
            visible_catalog_ids: 该用户可见的公共 MCP id 集合
                （CapabilityService.visible_ids(user, "mcp")）。

        Returns:
            连接列表，每项 {id, name, source, transport, tools}。
        """
        out: list[dict] = []
        for mcp_id in dict.fromkeys(str(i) for i in mcp_ids if i):
            doc = await self._store.get(MCP_COLLECTION, mcp_doc_id(user_id, mcp_id))
            if doc is not None and doc.get("user_id") == user_id:
                # 自建存在即遮蔽公共同名条目；停用整体跳过
                if doc.get("enabled") is not True:
                    continue
                try:
                    config = await self.resolved(user_id, mcp_id)
                    tools = list(config.get("tools") or [])
                    if not tools:
                        tools = await self.discover_tools(user_id, mcp_id, persist=True)
                except Exception:
                    continue
                out.append({"id": mcp_id, "name": config["name"],
                            "source": "user", "transport": "streamable-http",
                            "tools": tools})
                continue
            pkg = self._catalog_mcps().get(mcp_id)
            if pkg is None or mcp_id not in visible_catalog_ids:
                continue
            try:
                tools = await self.discover_public_tools(mcp_id)
            except Exception:
                continue
            out.append({"id": mcp_id, "name": pkg.name, "source": "catalog",
                        "transport": pkg.transport, "tools": tools})
        return out

    async def call_runtime_tool(self, user_id: str, source: str, mcp_id: str,
                                tool_name: str, arguments: dict) -> dict:
        """按来源分发一次远程工具调用。

        Args:
            user_id: 用户 sub（自建来源用于取配置）。
            source: "user" | "catalog"（resolve_runtime_mcps 返回值）。
            mcp_id: MCP id。
            tool_name: 远程工具名。
            arguments: 工具参数。

        Returns:
            MCP tools/call 的原始 result（content/isError）。

        Raises:
            KeyError: 条目不存在。
            RuntimeError: 连接或协议失败。
        """
        if source == "user":
            return await self.call_tool(user_id, mcp_id, tool_name, arguments)
        pkg = self._public_pkg(mcp_id)
        if pkg.transport == "stdio":
            return await self._stdio_request(
                mcp_id, "tools/call",
                {"name": tool_name, "arguments": arguments})
        return await self._request(
            self._public_config(pkg), "tools/call",
            {"name": tool_name, "arguments": arguments})
```

注意：`resolve_runtime_mcps` 用户分支直接查 `_store.get` 而非先 `get()`——`get()` 返回 `_public` 视图丢失 `enabled` 字段细节，这里需要原始文档判 `enabled` 与归属。

- [ ] **Step 4: 跑测试通过**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_resolve.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/backend/app/services/mcp_service.py apps/web/backend/tests/test_mcp_resolve.py
git commit -m "McpService 统一运行期解析

- resolve_runtime_mcps：自建遮蔽公共同名、可见性交集、失败静默跳过
- call_runtime_tool 按 source 分发（自建 http / 公共 http / 公共 stdio）"
```

---

### Task 5: 会话 enabled_mcp 字段与 API 流转

**Files:**
- Modify: `apps/web/backend/app/api/sessions_api.py`
- Modify: `apps/web/backend/app/services/agent_service.py`（chat 签名）
- Modify: `apps/web/backend/app/services/wakeup.py:55`
- Test: `apps/web/backend/tests/test_sessions_enabled_mcp.py`

**Interfaces:**
- Consumes: Task 1 `KINDS`（`capability_service.is_visible/is_enabled` 自动支持 mcp）、既有 `mcp_service.get`
- Produces:
  - 会话文档字段 `enabled_mcp`（`POST /sessions` 可带、`PATCH /sessions/{sid}` 可改）
  - `AgentService.chat(..., enabled_mcp: list[str] | None = None)`（Task 6 消费）
  - wakeup resolver 返回字典新增 `"enabled_mcp"` 键（Task 6 消费）

- [ ] **Step 1: 写失败测试**

测试沿用既有 sessions API 测试的 app/夹具风格（参考 `tests/` 下已有 `test_sessions_*` 的 fixture；若无现成 fixture，则构造 FastAPI 测试 app：挂 sessions 路由 + 假 store + 假 capability_service + 假 mcp_service）。核心用例：

```python
"""会话 enabled_mcp 字段校验与流转测试。"""


async def test_create_session_with_enabled_mcp_ok(client, mcp_owned):
    # mcp_owned：夹具预置用户自建 MCP "mine"
    resp = await client.post("/api/v1/sessions", json={
        "title": "t", "enabled_mcp": ["mine"]})
    assert resp.status_code == 201
    assert resp.json()["enabled_mcp"] == ["mine"]


async def test_patch_enabled_mcp_unknown_id_404(client):
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["ghost"]})
    assert resp.status_code == 404


async def test_patch_enabled_mcp_catalog_visible_ok(client, catalog_mcp_builtin):
    # catalog_mcp_builtin：夹具预置公共 MCP "pub"（default_enabled=True）
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["pub"]})
    assert resp.status_code == 200
    assert resp.json()["enabled_mcp"] == ["pub"]


async def test_patch_enabled_mcp_catalog_invisible_404(client, catalog_mcp_hidden):
    # 公共条目存在但 hidden（不可见）：勾选不能放大可见性
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["hidden-one"]})
    assert resp.status_code == 404
```

（`client/sid/mcp_owned/catalog_mcp_builtin/catalog_mcp_hidden` 夹具按本仓库测试惯例实现；能力可见性用真实 `CapabilityService` + 内存 store 组装，保证与生产同路径。）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_sessions_enabled_mcp.py -v`
Expected: FAIL（`enabled_mcp` 被 pydantic 忽略，响应里没有该字段 / 404 分支行为不符）

- [ ] **Step 3: 实现 sessions_api**

- `SessionCreateBody` 加字段 `enabled_mcp: list[str] | None = None`（docstring 补一段：会话级 MCP 附加，语义同 enabled_plugins）
- `SessionUpdateBody` 同样加 `enabled_mcp: list[str] | None = None`
- 新增校验函数（照 `_validate_plugin_ids`）：

```python
async def _validate_mcp_ids(request: Request, user_id: str, mcp_ids: list[str]) -> None:
    """校验会话级 MCP 附加里的 id 可用（勾选不能放大可见性）。

    每个 id 必须是：本人自建 MCP（存在即校验通过，启用与否运行期再判），
    或对该用户可见的公共 MCP（内置或已装且启用）。

    Args:
        request: FastAPI 请求（取 MCP 与能力服务）。
        user_id: 用户 sub。
        mcp_ids: MCP id 列表。

    Raises:
        HTTPException: 任一 id 不可用（404，不泄露存在性）。
    """
    mcp_service = getattr(request.app.state, "mcp_service", None)
    capability = getattr(request.app.state, "capability_service", None)
    for mcp_id in mcp_ids:
        if mcp_service is not None and await mcp_service.get(user_id, mcp_id):
            continue
        if capability is not None and await capability.is_visible(user_id, "mcp", mcp_id):
            continue
        raise HTTPException(404, f"MCP 不存在或不可用: {mcp_id}")
```

- `create_session`：在 `enabled_plugins` 落库处（约 :292 `"enabled_plugins": body.enabled_plugins` 旁）同步加 `"enabled_mcp": body.enabled_mcp`，创建路径先 `await _validate_mcp_ids(request, user["sub"], body.enabled_mcp)`（非 None 时）
- `update_session`（PATCH，约 :347-357）照 enabled_plugins 的 `model_fields_set` 模式加：

```python
    if "enabled_mcp" in body.model_fields_set:
        if body.enabled_mcp is not None:
            await _validate_mcp_ids(request, user["sub"], body.enabled_mcp)
        fields["enabled_mcp"] = body.enabled_mcp
```

- `send_message` 装配处（约 :496 `enabled_plugins=doc.get("enabled_plugins")` 旁）加 `enabled_mcp=doc.get("enabled_mcp")`

- [ ] **Step 4: agent_service.chat 签名与 wakeup 流转**

- `chat()`（约 :272）签名加 `enabled_mcp: list[str] | None = None`（docstring 补：会话级 MCP 附加，与专家 mcp_refs 取并集）
- `chat` 内部把 `enabled_mcp` 透传到装配段（找到 `enabled_plugins` 在 `_drive`/装配闭包中的传递路径，同样透传；本任务只打通参数链路，装配逻辑 Task 6 接）
- `wakeup.py:55` 返回字典加 `"enabled_mcp": doc.get("enabled_mcp")`，`notify_job_finished` 里调 `chat(...)` 处（约 :785）同步加 `enabled_mcp=ctx.get("enabled_mcp")`

- [ ] **Step 5: 跑测试通过 + 回归**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_sessions_enabled_mcp.py -v`
Expected: 新增用例 PASS（既有 sessions 测试不带 enabled_mcp 行为不变，Task 11 全量回归兜底）

- [ ] **Step 6: Commit**

```bash
git add apps/web/backend/app/api/sessions_api.py apps/web/backend/app/services/agent_service.py apps/web/backend/app/services/wakeup.py apps/web/backend/tests/test_sessions_enabled_mcp.py
git commit -m "会话文档新增 enabled_mcp 字段

- POST/PATCH 校验：自建存在或公共可见（勾选不放大可见性，404 不泄露存在性）
- chat 签名与 wakeup 装配透传 enabled_mcp"
```

---

### Task 6: agent_service 装配接线 — 会话勾选 ∪ 专家 mcp_refs

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py:426-467`
- Test: `apps/web/backend/tests/test_agent_mcp_assembly.py`

**Interfaces:**
- Consumes: Task 4 `resolve_runtime_mcps/call_runtime_tool`、Task 5 `chat(enabled_mcp=...)`
- Produces: 运行期 MCP 工具注册（`mcp.<id>.<tool>`，既有命名与白名单口径不变）

- [ ] **Step 1: 写失败测试**

用假 `mcp_service`（记录 resolve 入参，返回固定 RuntimeMcp 列表）+ 假 `capability_service`，直测 `AgentService` 装配函数产出（沿用 `tests/` 下已有 agent_service 测试的构造方式；若装配逻辑内联在 `chat()` 中难以单测，则把 MCP 装配段抽成模块级函数 `assemble_mcp_tools(mcp_service, capability_service, user_sub, mcp_ids, run_registry) -> list[str]` 再测——**本任务实现即按抽出函数写**）：

```python
"""MCP 装配（会话勾选 ∪ 专家引用）测试。"""


@pytest.mark.asyncio
async def test_assemble_merges_session_and_expert_refs():
    # 会话勾选 ["a"] + 专家 mcp_refs ["b"] → resolve 收到 ["a", "b"]（去重保序）
    registered = assemble(["a"], ["b"])
    assert registered == ["mcp.a.tool-a", "mcp.b.tool-b"]


@pytest.mark.asyncio
async def test_assemble_dedup_same_id():
    registered = assemble(["a", "a"], ["a"])
    assert registered == ["mcp.a.tool-a"]


@pytest.mark.asyncio
async def test_assemble_skips_invisible_and_failed():
    """resolve 阶段已剔除不可见/连接失败条目；装配只注册返回项且跳过撞名。"""
    fake_registry = _fake_registry(existing=["mcp.a.tool-a"])
    names = await assemble_mcp_tools(
        _fake_mcp_service(rows=[  # "a" 连接失败被 resolve 剔除，只剩 "b"
            {"id": "b", "name": "B", "source": "catalog",
             "transport": "streamable-http",
             "tools": [{"name": "tool-b", "description": "d",
                        "input_schema": {"type": "object"}}]}]),
        _fake_capability(visible=set()), "u1", ["a", "b"], [], fake_registry)
    assert names == ["mcp.b.tool-b"]


@pytest.mark.asyncio
async def test_assemble_without_assistant_still_uses_session_refs():
    # 专家为 None（未选专家）时会话勾选依然生效
    registered = assemble(["a"], None)
    assert registered == ["mcp.a.tool-a"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_agent_mcp_assembly.py -v`
Expected: FAIL（assemble_mcp_tools 不存在）

- [ ] **Step 3: 实现**

`agent_service.py` 模块级新增函数（`chat()` 外，供单测与 chat 共用）：

```python
async def assemble_mcp_tools(
    mcp_service: Any, capability_service: Any, user_sub: str,
    session_mcp_ids: list[str], expert_mcp_ids: list[str],
    run_registry: ToolRegistry,
) -> list[str]:
    """把会话勾选 ∪ 专家引用的 MCP 解析为工具并注册进 run_registry。

    解析口径见 McpService.resolve_runtime_mcps（自建优先、可见性交集、
    失败静默跳过）；工具命名 mcp.<id>.<safe_name>，撞名跳过（既有口径）。

    Args:
        mcp_service: MCP 服务。
        capability_service: 能力服务（算公共 MCP 可见集；None 时公共条目全部不可见）。
        user_sub: 用户 sub。
        session_mcp_ids: 会话勾选的 MCP id（enabled_mcp）。
        expert_mcp_ids: 专家绑定的 MCP id（mcp_refs）。
        run_registry: 本轮运行的工具注册表。

    Returns:
        注册成功的 MCP 工具名列表（并入白名单）。
    """
    mcp_ids = [str(i) for i in (session_mcp_ids or []) if i] \
        + [str(i) for i in (expert_mcp_ids or []) if i]
    if not mcp_ids:
        return []
    if capability_service is not None:
        visible = await capability_service.visible_ids(user_sub, "mcp")
    else:
        visible = set()
    connections = await mcp_service.resolve_runtime_mcps(user_sub, mcp_ids, visible)
    tool_names: list[str] = []
    for connection in connections:
        mcp_id = str(connection["id"])
        source = str(connection["source"])
        for remote_tool in connection.get("tools") or []:
            remote_name = str(remote_tool.get("name") or "")
            safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", remote_name)
            local_name = f"mcp.{mcp_id}.{safe_name}"
            if not remote_name or run_registry.find(local_name) is not None:
                continue

            async def execute_mcp(_ctx, args, *, mcp_source=source,
                                  connection_id=mcp_id, tool_name=remote_name):
                result = await mcp_service.call_runtime_tool(
                    user_sub, mcp_source, connection_id, tool_name, args)
                parts = result.get("content") or []
                text = "\n".join(
                    str(part.get("text") or "")
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
                return ToolResult(
                    ok=result.get("isError") is not True,
                    content=text or json.dumps(result, ensure_ascii=False),
                    data={"mcp_id": connection_id, "result": result},
                    error=("mcp_tool_error"
                           if result.get("isError") is True else None),
                )

            run_registry.register(ToolDefinition(
                name=local_name,
                description=str(remote_tool.get("description") or remote_name),
                parameters=(remote_tool.get("input_schema")
                            or {"type": "object", "properties": {}}),
                execute=execute_mcp,
                timeout_s=60.0,
            ))
            tool_names.append(local_name)
    return tool_names
```

`chat()` 内原 `agent_service.py:426-467` 段替换为：

```python
            mcp_tool_names = await assemble_mcp_tools(
                self._mcp_service, self._capability_service, user_sub,
                list(enabled_mcp or []),
                [str(i) for i in ((assistant or {}).get("mcp_refs") or []) if i],
                run_registry,
            ) if self._mcp_service is not None else []
```

（注意原条件 `and assistant` 删除——未选专家时会话勾选也要生效。）

- [ ] **Step 4: 跑测试通过 + 回归**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_agent_mcp_assembly.py -v && conda run -n synlysagent python -m pytest -v -k "agent or session or mcp"`
Expected: 全 PASS（专家 mcp_refs 既有行为不变——同一路径）

- [ ] **Step 5: Commit**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_agent_mcp_assembly.py
git commit -m "MCP 装配来源扩展为会话勾选 ∪ 专家引用

- assemble_mcp_tools 模块级抽出（可单测）：解析/注册/白名单一体
- 未选专家时会话勾选 MCP 依然生效"
```

---

### Task 7: 市场行与 + 面板合并视图端点

**Files:**
- Modify: `apps/web/backend/app/catalog/service.py`（market_items 的 mcp 特有字段）
- Modify: `apps/web/backend/app/api/mcp_api.py`
- Test: `apps/web/backend/tests/test_mcp_panel_api.py`

**Interfaces:**
- Consumes: Task 2 `public_status`、Task 1 `list_items("mcp")` / `is_visible`
- Produces:
  - 市场 mcp 行额外带 `transport` 字段（`GET /api/v1/market/mcp`，Task 10 前端消费）
  - `GET /api/v1/me/mcp/panel` — `+` 面板合并视图（Task 9 前端消费），行结构 `{"id", "name", "description", "source": "user"|"catalog", "transport", "status", "last_error", "tool_count"}`

- [ ] **Step 1: 写失败测试**

```python
"""+ 面板 MCP 合并视图端点测试。"""


async def test_panel_merges_user_and_catalog(client, user_mcp_enabled, catalog_mcp_builtin):
    rows = await (await client.get("/api/v1/me/mcp/panel")).json()
    ids = {r["id"]: r for r in rows}
    assert ids["mine"]["source"] == "user"
    assert ids["mine"]["status"] in {"connected", "unchecked", "error"}
    assert ids["pub"]["source"] == "catalog"
    assert ids["pub"]["transport"] == "streamable-http"


async def test_panel_hides_invisible_catalog(client, catalog_mcp_hidden):
    rows = await (await client.get("/api/v1/me/mcp/panel")).json()
    assert all(r["id"] != "hidden-one" for r in rows)


async def test_panel_hides_disabled_user_mcp(client, user_mcp_disabled):
    rows = await (await client.get("/api/v1/me/mcp/panel")).json()
    assert all(r["id"] != "off" for r in rows)
```

（夹具同 Task 5 风格。）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_panel_api.py -v`
Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现**

`catalog/service.py::market_items`：循环内照插件的 `config_schema` 分支加 mcp 特有字段：

```python
            if kind == "mcp":
                pkg = self.catalog.mcps.get(item.id)
                if pkg is not None:
                    row["transport"] = pkg.transport
```

`mcp_api.py` 新增端点（面板数据自建走 `list_for_user`、公共走能力服务可见集 + 状态缓存）：

```python
@router.get("/panel")
async def mcp_panel(request: Request,
                    user=Depends(get_current_user)) -> list[dict]:
    """「+」面板 MCP 合并视图：自建(enabled) ∪ 可见公共，含探测状态。

    公共条目状态来自后端进程级缓存（McpService.test_public_connection 写入），
    前端无法拼接，故由后端统一出。
    """
    service = _service(request)
    capability = getattr(request.app.state, "capability_service", None)
    rows: list[dict] = []
    for item in await service.list_for_user(user["sub"]):
        if not item["enabled"]:
            continue
        rows.append({
            "id": item["id"], "name": item["name"],
            "description": item["description"], "source": "user",
            "transport": "streamable-http", "status": item["status"],
            "last_error": item["last_error"],
            "tool_count": len(item.get("tools") or []),
        })
    if capability is not None:
        for mcp_id in sorted(await capability.visible_ids(user["sub"], "mcp")):
            pkg = capability.catalog.mcps.get(mcp_id)
            if pkg is None:
                continue
            status = service.public_status(mcp_id)
            rows.append({
                "id": mcp_id, "name": pkg.name, "description": pkg.description,
                "source": "catalog", "transport": pkg.transport,
                "status": status["status"], "last_error": status["last_error"],
                "tool_count": status["tool_count"],
            })
    return rows
```

**路由顺序注意**：`/panel` 必须声明在 `GET /{mcp_id}` 之前（FastAPI 按声明序匹配，否则 `panel` 会被当作 mcp_id）。

- [ ] **Step 4: 跑测试通过 + market 回归**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_mcp_panel_api.py -v -k "not skip" && conda run -n synlysagent python -m pytest -v -k "market or capability"`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/backend/app/catalog/service.py apps/web/backend/app/api/mcp_api.py apps/web/backend/tests/test_mcp_panel_api.py
git commit -m "MCP 市场行与 + 面板合并视图

- market_items 对 mcp 行附 transport
- GET /me/mcp/panel：自建(enabled) ∪ 可见公共 + 探测状态缓存"
```

---

### Task 8: 公共 MCP 管理端点（写入覆盖层 + 热重载 + 测试连接）

**Files:**
- Create: `apps/web/backend/app/catalog/mcp_admin.py`
- Create: `apps/web/backend/app/api/admin_mcp_api.py`
- Modify: `apps/web/backend/app/main.py`（注册路由）
- Test: `apps/web/backend/tests/test_admin_mcp_api.py`

**Interfaces:**
- Consumes: Task 1 `parse_mcp_manifest`、Task 2/3 `test_public_connection`、既有 `require_admin` / `PUT /me/admin/catalog/{kind}/{id}/policy`（policy 端点已按 KINDS 泛化，无需新写）
- Produces:
  - `mcp_admin.public_root(settings) / write_public(settings, data) / delete_public(settings, mcp_id) / is_overlaid(settings, mcp_id)`
  - 端点组（`/api/v1/admin/mcp`，全部 `require_admin`）：`GET ""`（列表：manifest + policy + overlaid + 状态）、`GET /{id}`、`POST ""`、`PUT /{id}`、`DELETE /{id}`、`POST /{id}/reset`、`POST /{id}/test`

- [ ] **Step 1: 写失败测试**

```python
"""公共 MCP 管理端点测试（写入覆盖层 / 热重载 / 删除边界）。"""


async def test_create_public_mcp_writes_overlay_and_hot_reloads(client, admin_user, tmp_data_root):
    resp = await client.post("/api/v1/admin/mcp", json={
        "id": "new-one", "name": "新服务", "description": "d",
        "transport": "streamable-http", "url": "https://x.example.com/mcp",
    })
    assert resp.status_code == 201
    # 覆盖文件落数据目录公共层
    assert (tmp_data_root / "public" / "catalog" / "mcp" / "new-one" / "mcp.json").is_file()
    # 热重载：市场/面板立即可见（无需重启）
    rows = await (await client.get("/api/v1/me/mcp/panel")).json()
    assert any(r["id"] == "new-one" for r in rows)


async def test_create_invalid_manifest_422(client, admin_user):
    resp = await client.post("/api/v1/admin/mcp", json={
        "id": "bad", "name": "x", "description": "d",
        "transport": "stdio",  # 缺 command
    })
    assert resp.status_code == 422


async def test_edit_builtin_writes_overlay(client, admin_user, builtin_mcp):
    resp = await client.put("/api/v1/admin/mcp/builtin-one", json={
        "id": "builtin-one", "name": "改过的内置", "description": "d",
        "transport": "streamable-http", "url": "https://y.example.com/mcp",
    })
    assert resp.status_code == 200
    rows = await (await client.get("/api/v1/admin/mcp")).json()
    row = next(r for r in rows if r["id"] == "builtin-one")
    assert row["overlaid"] is True
    assert row["name"] == "改过的内置"


async def test_delete_builtin_without_overlay_409(client, admin_user, builtin_mcp):
    resp = await client.delete("/api/v1/admin/mcp/builtin-one")
    assert resp.status_code == 409  # 仓库内置不可删，提示用 hidden 下线


async def test_reset_overlay_restores_default(client, admin_user, builtin_mcp_overlaid):
    resp = await client.post("/api/v1/admin/mcp/builtin-one/reset")
    assert resp.status_code == 200
    rows = await (await client.get("/api/v1/admin/mcp")).json()
    row = next(r for r in rows if r["id"] == "builtin-one")
    assert row["overlaid"] is False


async def test_non_admin_forbidden(client, normal_user):
    resp = await client.get("/api/v1/admin/mcp")
    assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_admin_mcp_api.py -v`
Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现 mcp_admin.py**

```python
"""公共 MCP 的管理写入层（数据目录覆盖，仓库 catalog 不动）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.catalog.loader import MCP_MANIFEST, parse_mcp_manifest

if TYPE_CHECKING:
    from app.core.settings import Settings


def public_root(settings: "Settings") -> Path:
    """公共 MCP 可写层根目录（{data_root}/public/catalog/mcp）。"""
    return settings.data_root / "public" / "catalog" / "mcp"


def is_overlaid(settings: "Settings", mcp_id: str) -> bool:
    """某 id 是否存在数据目录覆盖文件。"""
    return (public_root(settings) / mcp_id / MCP_MANIFEST).is_file()


def write_public(settings: "Settings", data: dict[str, Any]) -> Path:
    """校验并写入一份公共 MCP manifest（原子替换）。

    Args:
        settings: 应用配置。
        data: manifest 字典（id 必填且 kebab-case）。

    Returns:
        写入的 manifest 路径。

    Raises:
        ValueError: manifest 非法（parse_mcp_manifest 同口径）。
    """
    directory = public_root(settings) / str(data["id"])
    parse_mcp_manifest(data, directory)  # 仅校验，异常向上抛给端点转 422
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MCP_MANIFEST
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    # 原子写：临时文件 + os.replace，读侧（热重载扫描）不会读到半截 JSON
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def delete_public(settings: "Settings", mcp_id: str) -> bool:
    """删除数据目录覆盖（含包目录；不存在返回 False）。"""
    directory = public_root(settings) / mcp_id
    if not directory.is_dir():
        return False
    for child in sorted(directory.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    directory.rmdir()
    return True
```

- [ ] **Step 4: 实现 admin_mcp_api.py**

```python
"""公共 MCP 管理端点（require_admin；写入数据目录覆盖层 + catalog 热重载）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_admin
from app.catalog.items import CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.mcp_admin import delete_public, is_overlaid, write_public

router = APIRouter(prefix="/api/v1/admin/mcp", tags=["admin-mcp"])


class McpAdminBody(BaseModel):
    """公共 MCP 新建/编辑请求体（与 manifest 字段一一对应）。"""

    id: str
    name: str
    description: str = ""
    transport: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token: str = ""
    timeout_s: float = 60.0


async def _reload_catalog(request: Request) -> None:
    """重扫 catalog 并替换能力服务的目录视图（公共 MCP 写入即生效）。

    只重建 CatalogService（mcp 条目消费方均经它取数）；插件/专家包对象
    不受 mcp 写入影响，维持原引用。
    """
    settings = request.app.state.settings
    index = scan_catalog(catalog_roots(settings))
    request.app.state.capability_service.catalog = CatalogService(index=index)


def _row(request: Request, pkg: Any) -> dict:
    """管理员视角的一行（manifest 公开字段 + policy + overlaid + 状态）。"""
    capability = request.app.state.capability_service
    import asyncio  # 端点为 async，直接 await 在端点内做；此处只组装非异步部分
    return {
        "id": pkg.id, "name": pkg.name, "description": pkg.description,
        "transport": pkg.transport,
        "command": pkg.command, "args": list(pkg.args), "cwd": pkg.cwd,
        "env": dict(pkg.env), "url": pkg.url,
        "header_names": sorted(pkg.headers),
        "bearer_token_set": bool(pkg.bearer_token),
        "timeout_s": pkg.timeout_s,
        "overlaid": is_overlaid(request.app.state.settings, pkg.id),
    }


@router.get("")
async def list_public_mcps(request: Request,
                           user=Depends(require_admin)) -> list[dict]:
    """全部公共 MCP（含 hidden；附 policy 与探测状态）。"""
    capability = request.app.state.capability_service
    rows = []
    for pkg in sorted(capability.catalog.mcps.values(), key=lambda p: p.id):
        pol = await capability.policy.get("mcp", pkg.id)
        status = request.app.state.mcp_service.public_status(pkg.id)
        rows.append({**_row(request, pkg),
                     "visibility": pol["visibility"],
                     "default_enabled": pol["default_enabled"],
                     "status": status["status"], "last_error": status["last_error"],
                     "tool_count": status["tool_count"]})
    return rows


@router.post("", status_code=201)
async def create_public_mcp(request: Request, body: McpAdminBody,
                            user=Depends(require_admin)) -> dict:
    """新增公共 MCP（写数据目录公共层）。"""
    if body.id in request.app.state.capability_service.catalog.mcps:
        raise HTTPException(409, f"MCP 已存在: {body.id}")
    try:
        write_public(request.app.state.settings, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _reload_catalog(request)
    return {"ok": True, "id": body.id}


@router.put("/{mcp_id}")
async def update_public_mcp(request: Request, mcp_id: str, body: McpAdminBody,
                            user=Depends(require_admin)) -> dict:
    """编辑公共 MCP（内置条目 = 写同名覆盖；id 不可改）。"""
    if mcp_id not in request.app.state.capability_service.catalog.mcps:
        raise HTTPException(404, f"MCP 不存在: {mcp_id}")
    if body.id != mcp_id:
        raise HTTPException(422, "id 不可修改")
    try:
        write_public(request.app.state.settings, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _reload_catalog(request)
    return {"ok": True, "id": mcp_id}


@router.delete("/{mcp_id}")
async def delete_public_mcp(request: Request, mcp_id: str,
                            user=Depends(require_admin)) -> dict:
    """删除公共 MCP（仅数据目录层；仓库内置请用 hidden 下线）。"""
    if mcp_id not in request.app.state.capability_service.catalog.mcps:
        raise HTTPException(404, f"MCP 不存在: {mcp_id}")
    if not is_overlaid(request.app.state.settings, mcp_id):
        raise HTTPException(409, "仓库内置条目不可删除，请用「隐藏」下线")
    delete_public(request.app.state.settings, mcp_id)
    await _reload_catalog(request)
    return {"ok": True}


@router.post("/{mcp_id}/reset")
async def reset_public_mcp(request: Request, mcp_id: str,
                           user=Depends(require_admin)) -> dict:
    """恢复默认：删除数据目录覆盖，回退仓库内置版。"""
    if not is_overlaid(request.app.state.settings, mcp_id):
        raise HTTPException(409, "该条目没有覆盖副本")
    delete_public(request.app.state.settings, mcp_id)
    await _reload_catalog(request)
    return {"ok": True}


@router.post("/{mcp_id}/test")
async def test_public_mcp(request: Request, mcp_id: str,
                          user=Depends(require_admin)) -> dict:
    """测试公共 MCP 连接（stdio 真握手 / http initialize）。"""
    try:
        return await request.app.state.mcp_service.test_public_connection(mcp_id)
    except KeyError as exc:
        raise HTTPException(404, f"MCP 不存在: {mcp_id}") from exc
    except Exception as exc:
        raise HTTPException(502, f"MCP 连接失败: {exc}") from exc
```

清理：`_row` 里那行多余的 `import asyncio` 不要写进实现（草稿痕迹）；`_row` 不含 policy/status（list 端点单独补），实现时按上面 list 端点的写法组装。`main.py` 路由注册处加 `from app.api.admin_mcp_api import router as admin_mcp_router` 并挂载（照既有 `app.include_router(...)` 模式）。

- [ ] **Step 5: 跑测试通过**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest tests/test_admin_mcp_api.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/backend/app/catalog/mcp_admin.py apps/web/backend/app/api/admin_mcp_api.py apps/web/backend/app/main.py apps/web/backend/tests/test_admin_mcp_api.py
git commit -m "公共 MCP 管理端点

- mcp_admin 写入层：数据目录覆盖 + 原子写 + 恢复默认
- admin_mcp_api：列表/新增/编辑/删除/重置/测试连接，写入即热重载
- 仓库内置不可删（409 提示用 hidden 下线），覆盖编辑带 overlaid 标记"
```

---

### Task 9: 前端 — McpPicker、sessions store、AttachMenu 接入

**Files:**
- Modify: `apps/web/frontend/src/types.ts`
- Modify: `apps/web/frontend/src/stores/sessions.ts`
- Create: `apps/web/frontend/src/components/chat/McpPicker.tsx`
- Modify: `apps/web/frontend/src/components/chat/AttachMenu.tsx`
- Test: `cd apps/web/frontend && npm run build`（项目惯例：build 通过即交付）

**Interfaces:**
- Consumes: Task 7 `GET /api/v1/me/mcp/panel`、Task 5 `POST/PATCH sessions` 的 `enabled_mcp`
- Produces: `McpPanelItem` 类型、`sessions store` 的 `enabledMcp/draftEnabledMcp/setDraftMcp/setEnabledMcp`、`McpPicker` 组件（AttachMenu 消费）

- [ ] **Step 1: types.ts 与 sessions store**

`types.ts`：

```typescript
/** 「+」面板 MCP 合并视图行（GET /me/mcp/panel）。 */
export interface McpPanelItem {
  id: string
  name: string
  description: string
  source: 'user' | 'catalog'
  transport: string
  status: 'connected' | 'error' | 'unchecked' | string
  last_error: string
  tool_count: number
}
```

会话文档接口（找 `enabled_plugins` 所在的 session 接口定义）旁加 `enabled_mcp?: string[] | null`。

`stores/sessions.ts`（照 enabled_plugins 的每个出现点逐一加 enabled_mcp 对应物）：
- state：`draftEnabledMcp: string[] | null`（初值 null）、会话接口 `enabledMcp?: string[] | null`
- `create()`（约 :85-95）：options 解构加 `enabledMcp`，body 展开加 `...(enabledMcp ? { enabled_mcp: enabledMcp } : {})`
- actions：`setDraftMcp: (ids) => set({ draftEnabledMcp: ids })`、`setEnabledMcp: async (sessionId, ids)` → `PATCH` body `{ enabled_mcp: ids }`（照 `setEnabledPlugins` :136 写法，含乐观更新与错误回滚——照抄相邻实现）
- 重置草稿处（约 :154-155 两处 `draftEnabledPlugins: null`）同步加 `draftEnabledMcp: null`

- [ ] **Step 2: McpPicker.tsx（照 PluginPicker 全文结构）**

```tsx
/**
 * 「+」菜单「MCP」二级面板：对本会话附加的 MCP 开关（照 PluginPicker 语义）。
 *
 * **默认不附加**：只有显式打开的 MCP 才在本会话注入工具；切换即 PATCH 会话
 * 文档持久化，草稿态（会话未落库）先存 sessions store 的 draftEnabledMcp。
 * 数据源：GET /api/v1/me/mcp/panel（自建 enabled ∪ 可见公共），分组展示，
 * 公共条目带「公共」徽标；error 状态标灰并提示失败原因。
 */
import { useEffect, useState } from 'react'
import { Switch } from '@/components/admin/shared'
import { api } from '@/api/client'
import { toast } from '@/stores/toasts'
import { useSessionsStore } from '@/stores/sessions'
import type { McpPanelItem } from '@/types'
import PickerPanel from './PickerPanel'

interface McpPickerProps {
  /** 一级菜单展开方向（同步二级面板的生长方向）。 */
  direction: 'up' | 'down'
}

/** MCP 附加面板（会话级开关）。 */
export default function McpPicker({ direction }: McpPickerProps) {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = sessions.find((x) => x._id === currentId) ?? null
  const setEnabledMcp = useSessionsStore((s) => s.setEnabledMcp)
  const setDraftMcp = useSessionsStore((s) => s.setDraftMcp)
  const draftEnabledMcp = useSessionsStore((s) => s.draftEnabledMcp)
  const [items, setItems] = useState<McpPanelItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (items !== null) return
    api<McpPanelItem[]>('/api/v1/me/mcp/panel')
      .then(setItems)
      .catch((err: Error) => setError(err.message))
  }, [items])

  const effective = session ? session.enabled_mcp ?? [] : draftEnabledMcp ?? []

  const toggle = (id: string) => {
    const next = effective.includes(id)
      ? effective.filter((x) => x !== id)
      : [...effective, id]
    if (session) {
      setEnabledMcp(session._id, next).catch((err: Error) =>
        toast('error', `MCP 开关保存失败：${err.message}`),
      )
    } else {
      setDraftMcp(next)
    }
  }

  const q = query.trim().toLowerCase()
  const filtered = (items ?? []).filter(
    (m) => !q || m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
  )
  const mine = filtered.filter((m) => m.source === 'user')
  const shared = filtered.filter((m) => m.source === 'catalog')

  return (
    <PickerPanel
      direction={direction}
      widthClass="w-[300px]"
      maxHeightClass="max-h-[358px]"
      ariaLabel="本会话 MCP 附加"
      testId="composer-mcp-picker"
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索 MCP"
    >
      {error ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-state-error-primary)]">
          MCP 加载失败：{error}
        </div>
      ) : items === null ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          加载中…
        </div>
      ) : items.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          暂无可用 MCP（去「能力中心」添加或安装）
        </div>
      ) : (
        <>
          <div className="px-2 pb-1 pt-0.5 text-[11px] text-[var(--sa-alias-label-caption)]">
            默认不附加；打开后本会话启用该 MCP 的工具（刷新保持）
          </div>
          {[
            { label: '我的 MCP', rows: mine },
            { label: '公共 MCP', rows: shared },
          ].map(({ label, rows }) =>
            rows.length ? (
              <div key={label}>
                <div className="px-2 pb-0.5 pt-1.5 text-[11px] font-medium text-[var(--sa-alias-label-caption)]">
                  {label}
                </div>
                {rows.map((m) => (
                  <div
                    key={m.id}
                    title={m.status === 'error' ? `连接失败：${m.last_error}` : m.description}
                    className={`flex w-full items-start gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 transition-colors duration-[var(--sa-duration-fast)] ${
                      m.status === 'error'
                        ? 'opacity-60'
                        : 'hover:bg-[var(--sa-alias-interactive-bg-hover)]'
                    }`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                        {m.name}
                        {m.status === 'error' && (
                          <span className="ml-1 text-[11px] text-[var(--sa-alias-state-error-primary)]">
                            连接失败
                          </span>
                        )}
                      </span>
                      <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
                        {m.tool_count > 0 ? `${m.tool_count} 个工具 · ${m.transport}` : m.transport}
                      </span>
                    </span>
                    <span className="mt-0.5 shrink-0">
                      <Switch
                        checked={effective.includes(m.id)}
                        label={`${m.name} 在本会话附加`}
                        onChange={() => toggle(m.id)}
                      />
                    </span>
                  </div>
                ))}
              </div>
            ) : null,
          )}
        </>
      )}
    </PickerPanel>
  )
}
```

- [ ] **Step 3: AttachMenu 接入**

`AttachMenu.tsx`：
- import 区加 `import McpPicker from './McpPicker'`
- `panel` state 类型（`'none' | 'expert' | 'skill' | 'plugin'`）加 `| 'mcp'`
- 菜单列表里「插件」菜单项之后照抄一份「MCP」项（`ITEM_CLASS`/`ITEM_OPEN_CLASS` 同款，chevron 图标照抄插件项，`aria-expanded`/`onClick` 同款切 `panel === 'mcp' ? 'none' : 'mcp'`）
- 面板渲染处 `{panel === 'plugin' && <PluginPicker direction={direction} />}` 之后加：

```tsx
              {panel === 'mcp' && <McpPicker direction={direction} />}
```

- [ ] **Step 4: 建会话草稿流转**

检查新建会话的调用方（ChatView / Composer 里调 `sessions.create(...)` 且传 `enabledPlugins: draftEnabledPlugins` 的位置，全局搜 `draftEnabledPlugins` 的消费点），同位置补传 `enabledMcp: draftEnabledMcp`；建会话成功后的草稿重置已由 store 统一处理（Step 1 的重置字段）。

- [ ] **Step 5: 构建验证**

Run: `cd apps/web/frontend && npm run build`
Expected: 0 error（TS 类型全过）

- [ ] **Step 6: Commit**

```bash
git add apps/web/frontend/src/types.ts apps/web/frontend/src/stores/sessions.ts apps/web/frontend/src/components/chat/McpPicker.tsx apps/web/frontend/src/components/chat/AttachMenu.tsx
git commit -m "聊天输入框 + 面板新增 MCP 附加

- McpPicker：自建/公共分组、状态徽标、连接失败标灰提示
- sessions store enabledMcp 草稿态与 PATCH 切换（照 enabledPlugins 全链路）"
```

---

### Task 10: 前端 — 能力中心市场公共 MCP 与后台 McpAdmin 页

**Files:**
- Modify: `apps/web/frontend/src/types.ts`（`CatalogItem['kind']` 联合加 `'mcp'`）
- Modify: `apps/web/frontend/src/stores/catalog.ts`
- Modify: `apps/web/frontend/src/components/catalog/ExtensionCenter.tsx`
- Create: `apps/web/frontend/src/components/admin/McpAdmin.tsx`
- Modify: `apps/web/frontend/src/components/admin/AdminLayout.tsx`、`apps/web/frontend/src/components/admin/index.ts`
- Test: `cd apps/web/frontend && npm run build`

**Interfaces:**
- Consumes: Task 7/8 端点；既有 `useCatalogStore`（install/uninstall/setEnabled 已按 kind 泛化）、`Switch`/`form` 等管理页共用件
- Produces: 市场可装公共 MCP；后台 `/admin/mcp` 页

- [ ] **Step 1: 类型与 catalog store 泛化**

`types.ts`：`CatalogItem` 的 `kind` 联合类型加 `'mcp'`；市场行接口加可选 `transport?: string`。
`stores/catalog.ts`：

```typescript
const EMPTY: KindMap = { expert: [], skill: [], plugin: [], mcp: [] }
const KINDS: CatalogItem['kind'][] = ['expert', 'skill', 'plugin', 'mcp']
```

（`install/uninstall/setEnabled` 走 `PUT /me/capabilities/{kind}/{id}` 与 `market/{kind}`，后端已泛化，前端零改动自动生效；文件头注释「三类」改「四类」。）

- [ ] **Step 2: ExtensionCenter 市场页加公共 MCP**

`ExtensionCenter.tsx` 市场 tab：现有插件列表渲染之后加 MCP 分组（同卡片模型 `CapabilityCard` + `CardBadge`，徽标显示 `transport`；安装/启停/卸载按钮行为与插件一致，复用 store 同名 action，`kind` 传 `'mcp'`）。数据源 `useCatalogStore((state) => state.byKind.mcp)`。MCP 无个人配置表单——安装直接调 `install('mcp', id)`。

- [ ] **Step 3: McpAdmin.tsx（照 SkillsAdmin/PluginsAdmin 页面骨架）**

组件结构（列表 + 新建/编辑弹层 + 行内动作），关键行为：

- 列表 `GET /api/v1/admin/mcp`：列 = 名称/id、transport、策略（可见性 + 内置，`Switch` 切换调既有 `PUT /api/v1/me/admin/catalog/mcp/{id}/policy`）、状态（connected/error/unchecked + last_error 悬浮）、`overlaid` 徽标（「已覆盖」）
- 行动作：「编辑」（弹层表单回填）、「测试连接」（`POST /admin/mcp/{id}/test`，成功 toast 工具数、失败 toast 原因，完成后重拉列表）、「恢复默认」（仅 overlaid 行显示，`POST /admin/mcp/{id}/reset`，确认弹窗）、「删除」（仅非仓库内置即 overlaid 可删，`DELETE`，确认弹窗；仓库内置行不显示删除、显示提示「内置条目请用隐藏下线」——列表行若无 overlaid 且来自仓库根即内置，`overlaid=false` 即如此判定）
- 表单弹层（新建/编辑共用）：字段 id（编辑时只读）、名称、描述、transport 单选（`stdio` / `streamable-http` 切换字段组：stdio = command/args(逗号或逐行)/cwd/env(k-v)；http = url/headers(k-v)/bearer_token）、timeout_s；提交 `POST /admin/mcp` 或 `PUT /admin/mcp/{id}`；422 错误 toast 后端消息
- 样式全部用既有管理页组件与 `--sa-*` token（照 `SkillsAdmin.tsx` 的表格/弹层/表单写法逐样式照抄）

- [ ] **Step 4: AdminLayout 导航与导出**

`AdminLayout.tsx` 左导航加「MCP 服务」项（照「插件」项结构，路由 `/admin/mcp`）；`admin/index.ts` 导出 `McpAdmin`；路由注册处（搜 `PluginsAdmin` 的路由挂载点）加 McpAdmin 同款一条。

- [ ] **Step 5: 构建验证**

Run: `cd apps/web/frontend && npm run build`
Expected: 0 error

- [ ] **Step 6: Commit**

```bash
git add apps/web/frontend/src
git commit -m "能力中心市场与后台管理页支持公共 MCP

- catalog store 第四类 mcp；市场安装/启停/卸载与插件同模型
- 后台 McpAdmin：列表/策略/覆盖编辑/恢复默认/测试连接/删除"
```

---

### Task 11: 收尾 — 全量回归、样例 manifest、README

**Files:**
- Create: `apps/web/backend/catalog/mcp/README.md`（目录说明，非条目——**注意 loader 只认 `mcp.json`，README 目录无 manifest 会被自然跳过，但要确认 `_scan_mcps` 对无 manifest 目录静默跳过，Task 1 实现已保证**）
- Modify: `README.md`（功能清单：MCP 会话附加 + 公共 MCP 管理；使用方式两段）
- Test: 全量 pytest + npm build

- [ ] **Step 1: 全量后端测试**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest -v`
Expected: 全 PASS（既有测试零回归）

- [ ] **Step 2: harness 包测试确认零改动**

Run: `cd packages/synlys-harness && conda run -n synlysagent python -m pytest -v`
Expected: 全 PASS（本特性 harness 无改动，跑一遍作证）

- [ ] **Step 3: 前端构建**

Run: `cd apps/web/frontend && npm run build`
Expected: 0 error

- [ ] **Step 4: README 更新**

功能清单补：聊天窗 `+` 面板 MCP 附加（会话级开关、默认不附加）；能力中心市场可安装管理员发布的公共 MCP（stdio/HTTP）；管理后台「MCP 服务」页（覆盖编辑/恢复默认/策略/测试连接）。按 README 既有章节风格写，不新开顶层章节。

- [ ] **Step 5: Commit**

```bash
git add README.md apps/web/backend/catalog/mcp/README.md
git commit -m "MCP 特性收尾

- 全量回归通过；README 功能清单补 MCP 会话附加与公共 MCP 管理"
```

---

## 执行注意事项

- Task 5/7/8 的测试夹具（测试 app + 内存 store + 真实 CapabilityService）在三个任务间可复用，建议第一个写的地方抽 `tests/conftest.py` 公共夹具
- Task 8 的 `_reload_catalog` 依赖 `app.state.settings` 属性名——实现前先 `grep -n "app.state.settings" apps/web/backend/app/main.py` 确认（若无此属性则从 lifespan 闭包取 settings 的既有模式改写）
- Task 9 Step 4 的「建会话调用方」需全局搜 `draftEnabledPlugins` 消费点确认（Composer/ChatView），漏改会导致草稿勾选丢失
- Windows 开发机跑 stdio 测试用 `sys.executable` 做假 server 命令（Task 3 测试已如此），避免 npx 依赖
