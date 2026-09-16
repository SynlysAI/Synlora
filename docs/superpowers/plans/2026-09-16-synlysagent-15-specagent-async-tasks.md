# Spec_Agent 五个谱图异步任务接入实施计划（插件化）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Spec_Agent 的 5 种谱图解析异步任务（NMR / IR / GPC / Raman / LCMS）接入平台——模型提交即返回 job_id，完成后自动唤醒整合结果；宿主只新增一个**通用连接器扩展点**（约 60 行），业务适配全部落在 `catalog/plugins/spec_agent/` 插件目录内。

**Architecture:** C1（统一 Job 注册表）已建好机制：`JobService` 管状态机与持久化、`JobPoller` 轮询、终态唤醒 agent、`JobConnector` 协议对接外部系统。C1 留下两个缺口，本计划补齐：① 生产环境**没有连接器注册点**（registry 创建晚于插件 startup）→ 宿主加通用扩展点，插件在 `plugin.json` 声明 `connectors_module` 即可注册连接器；② 连接器调度上下文缺 `workspace_root`，读不到用户工作区的谱图文件 → 补齐。插件侧新增 `connectors.py`：一个参数化的 `SpectraTaskConnector`，负责"读工作区文件 → 上传 Spec_Agent 换 file_id → 提任务 → 轮询 → 取结果"。

**Tech Stack:** Python 3.12 / FastAPI / httpx（插件内照 `tools.py` 的 `_make_client()` + MockTransport 可注入模式）/ pytest（`asyncio_mode = "auto"`）；conda 环境 `synlysagent`。前端零改动（复用 C1 的 `systemWake` 提示条与 `job.*` 工具卡片）。

---

## 0. 设计依据：Spec_Agent 接口调研结论（2026-09-16 实测）

源码 `E:\github_project\Spec_Agent`，部署 `http://10.26.15.93:8001`。以下结论取自其 `backend/openapi.json` 与 `app/api/v1/endpoints/tasks.py`：

| 用途 | 接口 | 关键结构 |
|---|---|---|
| 上传谱图 | `POST /api/v1/files/upload` | multipart：`file`（binary，必填）+ `biz_type`（可选）；返回 `{file_id, file_name, file_size, file_ext, storage_path, ...}` |
| 提交任务 | `POST /api/v1/tasks/{nmr\|ir\|gpc\|raman\|lcms}` | body `{input: {input_type, file_id}, params: {...}, options: {priority?, callback_url?}}`；返回 `{task_id, task_type, status}` |
| 查状态 | `GET /api/v1/tasks/{task_id}` | `{task_id, task_type, status, progress, message, created_at, updated_at}` |
| 取结果 | `GET /api/v1/tasks/{task_id}/result` | `{task_id, status, result?, error?}` |
| 列任务 | `GET /api/v1/tasks` | 分页；`status` / `task_type` 可选过滤 |

**三个必须记住的事实：**

1. **只能以 `file_id` 提交。** `tasks.py::_ensure_uploaded_file_input` 硬校验 `input_data["input_type"] == "file_id"`，否则 400「当前版本仅支持上传文件方式提交任务」。所以连接器**必须先上传**再提交——这是本计划把 `workspace_root` 注入调度上下文的原因。
2. **状态枚举是 `PENDING / QUEUED / RUNNING / SUCCESS / FAILED / CANCELED`**，与本平台 harness 的 `pending/running/completed/failed/cancelled` 不同名，必须经连接器的状态映射翻译（`SUCCESS→COMPLETED`、`CANCELED→CANCELLED`，注意上游只有一个 L）。
3. **`task_type` 取值**：`nmr_analysis` / `ir_analysis` / `gpc_analysis` / `raman_analysis` / `lcms_analysis`。URL 路径段是短名（`nmr`/`ir`/`gpc`/`raman`/`lcms`）。

**认证**：上游 `get_current_user` 依赖，与其他子平台一致走 `Authorization: Bearer <token>`。本平台已有 `ctx.extra["ai4ms_token"]`（按登录用户代签）与插件配置里的服务 token，插件照既有约定「动态 token 优先、配置里的服务 token 兜底」。

**结果形态**：`result` 是自由 JSON 对象（`additionalProperties: true`），不同谱图类型结构不同。本计划把它序列化为 JSON 文本写入 job 的 `result` 字段（截断后进唤醒文本），不解析其内部结构。

---

## 1. 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `apps/web/backend/app/catalog/loader.py` | 修改 | `PluginPackage` 加 `connectors_module` 字段；新增 `load_plugin_connectors()` |
| `apps/web/backend/app/services/job_connectors.py` | 修改 | `JobConnector` 协议加 `status_map` 属性（连接器自带状态词汇表）；`register()` 支持从连接器取映射 |
| `apps/web/backend/app/plugins/service.py` | 修改 | `PluginService` 接 `job_connectors`；`_attach()` 注册插件连接器（幂等、防重名） |
| `apps/web/backend/app/main.py` | 修改 | `job_connectors` 创建提前到 `PluginService` 构造之前并透传 |
| `apps/web/backend/app/services/job_service.py` | 修改 | `_ctx_for` 带 `workspace_root`；终态成功时调 `fetch_result` 落 `result` 字段 |
| `apps/web/backend/app/services/agent_service.py` | 修改 | `ctx_extra` 注入 `workspace_root`；提交时把工作根写进 job 文档 |
| `apps/web/backend/catalog/plugins/spec_agent/connectors.py` | 新建 | 5 个谱图任务的连接器（上传 / 提交 / 轮询 / 取结果 / 取消） |
| `apps/web/backend/catalog/plugins/spec_agent/plugin.json` | 修改 | 声明 `connectors_module`；专家白名单补 `job.*` |
| `apps/web/backend/catalog/plugins/spec_agent/skills/spec-spectra/SKILL.md` | 新建 | 5 个 kind 的参数说明（模型据此知道怎么填 `kind` 与 `params`） |
| `apps/web/backend/tests/test_plugin_connectors.py` | 新建 | 插件连接器的单元与端到端测试（httpx MockTransport 模拟 Spec_Agent） |
| `apps/web/backend/tests/test_job_connectors.py` | 修改 | 协议新增能力（`status_map`）的测试 |
| `apps/web/backend/README.md` | 修改 | 插件机制章节补「连接器扩展点」；后台任务章节补结果回填 |

---

## 2. 关键契约（后续任务都依赖）

**1. 插件连接器声明**

插件目录放 `connectors.py`，模块级导出**实例列表**：

```python
CONNECTORS = [conn_1, conn_2, ...]   # 每个元素实现 JobConnector 协议
```

`plugin.json` 里 `"connectors_module": "connectors.py"`（可选字段，缺省 = 本插件不贡献连接器）。

**2. `JobConnector` 协议新增 `status_map` 属性**（连接器自带状态词汇表，注册时无需外部传映射）：

```python
class JobConnector(Protocol):
    kind: str
    plugin_id: str
    status_map: dict[str, JobStatus]   # 外部状态原文（小写）→ 统一状态

    async def submit(self, params: dict, ctx: dict) -> str: ...
    async def poll(self, external_id: str, ctx: dict) -> str: ...
    async def cancel(self, external_id: str, ctx: dict) -> bool: ...
```

`JobConnectorRegistry.register(connector, status_map=None)`：`status_map` 为 None 时回落到 `connector.status_map`。

**3. 连接器调度上下文 `ctx` 的键**（宿主填充，插件只读）：

```python
{
  "config": {插件配置},          # 提交路径取 ctx_extra["plugins"][plugin_id]；轮询路径按 user_id 重解析
  "ai4ms_token": "<代签 token 或空串>",
  "workspace_root": "<用户工作区根的绝对路径或空串>",   # 本计划新增
}
```

**4. 可选能力 `fetch_result`**（不放进 Protocol，用 `getattr` 探测——避免破坏既有连接器的 `isinstance` 校验）：

```python
async def fetch_result(self, external_id: str, ctx: dict) -> str:
    """取任务结果的人类可读摘要（仅成功终态时由 JobService 调用）。"""
```

`JobService.refresh` 在映射到 `COMPLETED` 时探测并调用它，把返回文本写入 job 的 `result` 字段（唤醒文本会带上）。

**5. job 文档新增字段 `workspace_root`**：提交时从 `ctx_extra` 取并落库，轮询路径据此重建 `ctx["workspace_root"]`（轮询不需要读文件，但取结果时可能用到相对路径信息，且保持两条路径上下文一致）。

---

## Task 1: 插件连接器扩展点（宿主）

**Files:**
- Modify: `apps/web/backend/app/catalog/loader.py`
- Modify: `apps/web/backend/app/services/job_connectors.py`
- Test: `apps/web/backend/tests/test_catalog_loader.py`、`tests/test_job_service.py`

- [ ] **Step 1: 写失败测试（loader）**

在 `apps/web/backend/tests/test_catalog_loader.py` 末尾追加：

```python
def test_plugin_package_declares_connectors_module(tmp_path):
    """plugin.json 声明 connectors_module 时被解析进包。"""
    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / "skills").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0",
        "tools_module": "tools.py", "connectors_module": "connectors.py",
    }), encoding="utf-8")
    index = scan_catalog([tmp_path])
    assert index.plugins["demo"].connectors_module == "connectors.py"


def test_plugin_package_without_connectors_module(tmp_path):
    """未声明时为空串（表示本插件不贡献连接器）。"""
    plugin_dir = tmp_path / "plugins" / "plain"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "plain", "name": "Plain", "version": "1.0.0",
        "tools_module": "tools.py",
    }), encoding="utf-8")
    index = scan_catalog([tmp_path])
    assert index.plugins["plain"].connectors_module == ""


def test_load_plugin_connectors_collects_list(tmp_path):
    """load_plugin_connectors 收集模块级 CONNECTORS 列表。"""
    from app.catalog.loader import load_plugin_connectors

    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "connectors.py").write_text(
        "class _C:\n"
        "    kind = 'k'\n"
        "    plugin_id = 'demo'\n"
        "CONNECTORS = [_C()]\n",
        encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0",
        "tools_module": "tools.py", "connectors_module": "connectors.py",
    }), encoding="utf-8")
    index = scan_catalog([tmp_path])
    got = load_plugin_connectors(index.plugins["demo"])
    assert len(got) == 1 and got[0].kind == "k"


def test_load_plugin_connectors_missing_or_broken(tmp_path):
    """未声明连接器 / 模块缺失 / 模块导入报错，都返回空列表并告警。"""
    from app.catalog.loader import load_plugin_connectors

    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0",
        "tools_module": "tools.py",
    }), encoding="utf-8")
    index = scan_catalog([tmp_path])
    package = index.plugins["demo"]
    assert load_plugin_connectors(package) == []          # 未声明

    (plugin_dir / "connectors.py").write_text("raise RuntimeError('炸了')",
                                              encoding="utf-8")
    broken = PluginPackage(
        id="demo", name="Demo", version="1.0.0", description="", directory=plugin_dir,
        tools_module="tools.py", connectors_module="connectors.py")
    assert load_plugin_connectors(broken) == []           # 导入失败
```

> 文件头若无 `import json` / `PluginPackage` import，按需补上（先读该文件确认既有 import）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_loader.py -v -k connectors
```
Expected: FAIL — `TypeError: PluginPackage.__init__() got an unexpected keyword argument 'connectors_module'` / `ImportError: cannot import name 'load_plugin_connectors'`

- [ ] **Step 3: 实现 loader 改动**

`app/catalog/loader.py`：

1. `PluginPackage` 加字段（放在 `tools_module` 之后）：

```python
    connectors_module: str = ""   # 连接器模块文件名（空 = 本插件不贡献连接器）
```

并在类 docstring 的 Attributes 里补一行：

```
        connectors_module: 连接器模块文件名（相对插件目录）；空串表示不贡献连接器。
```

2. `_scan_plugins` 构造 `PluginPackage` 处补一个参数：

```python
            connectors_module=str(data.get("connectors_module") or ""),
```

3. 文件末尾新增：

```python
def load_plugin_connectors(package: PluginPackage) -> list[Any]:
    """动态导入插件连接器模块并收集模块级 `CONNECTORS` 列表。

    Args:
        package: 插件包。

    Returns:
        连接器实例列表；未声明、模块缺失或导入失败时返回空列表并告警。
    """
    if not package.connectors_module:
        return []
    module_path = package.directory / package.connectors_module
    if not module_path.is_file():
        logger.warning("插件 %s 的连接器模块不存在: %s", package.id, module_path)
        return []
    # 模块名与工具模块区分（同一插件可同时有 tools.py 与 connectors.py）
    module_name = f"synlora_plugin_{re.sub(r'[^0-9a-zA-Z_]', '_', package.id)}_connectors"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("插件 %s 连接器模块无法加载: %s", package.id, module_path)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 插件代码不可信，导入失败不阻断启动
        sys.modules.pop(module_name, None)
        logger.warning("插件 %s 连接器模块导入失败: %s", package.id, exc)
        return []
    raw = getattr(module, "CONNECTORS", None) or []
    if not isinstance(raw, (list, tuple)):
        logger.warning("插件 %s 的 CONNECTORS 不是列表，已忽略", package.id)
        return []
    return list(raw)
```

- [ ] **Step 4: 写失败测试（协议 status_map + register 回落）**

在 `apps/web/backend/tests/test_job_service.py` 末尾追加：

```python
async def test_register_falls_back_to_connector_status_map(store):
    """register 不传 status_map 时回落到连接器自带的 status_map。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1", script=["done"])
    conn.status_map = FAKE_STATUS_MAP          # 连接器自带词汇表
    reg.register(conn)                          # 不传映射
    assert reg.get("k").map_status("done") is JobStatus.COMPLETED


async def test_explicit_status_map_wins_over_connector(store):
    """显式传 status_map 时以显式为准（兼容既有调用点）。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1", script=["done"])
    conn.status_map = FAKE_STATUS_MAP
    reg.register(conn, status_map={"done": JobStatus.FAILED})
    assert reg.get("k").map_status("done") is JobStatus.FAILED


async def test_register_requires_some_status_map(store):
    """两者都没有则报错（缺映射会让状态永远映射不上、任务永不终结）。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("k", plugin_id="p1")
    with pytest.raises(ValueError):
        reg.register(conn)
```

- [ ] **Step 5: 实现协议与注册表改动**

`app/services/job_connectors.py`：

1. `JobConnector` 协议加属性（放在 `plugin_id` 之后）：

```python
    status_map: dict[str, JobStatus]
    """外部状态原文（小写）→ 统一状态。连接器自带词汇表，注册时无需外部传入。"""
```

2. `register()` 改为：

```python
    def register(self, connector: JobConnector,
                 status_map: dict[str, JobStatus] | None = None) -> None:
        """注册一个连接器。

        Args:
            connector: 连接器实例。
            status_map: 外部状态原文 → 统一状态；缺省回落到连接器自带的
                `connector.status_map`（状态词汇表本就属于连接器）。

        Raises:
            ValueError: kind 为空、连接器未实现协议（缺 submit/poll/cancel
                或它们不是 async def）、未提供状态映射、或该 kind 已被注册。
        """
        kind = str(getattr(connector, "kind", "")).strip()
        if not kind:
            raise ValueError("连接器 kind 不能为空")
        if not isinstance(connector, JobConnector):
            raise ValueError(
                f"连接器未实现 JobConnector 协议（需 kind/plugin_id/status_map 与 "
                f"submit/poll/cancel 三个异步方法）: {type(connector).__name__}")
        for method in ("submit", "poll", "cancel"):
            if not inspect.iscoroutinefunction(getattr(connector, method)):
                raise ValueError(
                    f"连接器方法必须是 async def: "
                    f"{type(connector).__name__}.{method}")
        if kind in self._items:
            raise ValueError(f"任务类型已注册: {kind}")
        source = status_map if status_map is not None else getattr(
            connector, "status_map", None)
        if not source:
            raise ValueError(
                f"连接器 {kind} 未提供状态映射（register 的 status_map 参数或"
                f" connector.status_map 属性），缺映射会让任务状态永远映射不上")
        normalized = {str(k).strip().lower(): v for k, v in source.items()}
        self._items[kind] = RegisteredConnector(connector, normalized)
```

3. `FakeConnector` 补 `status_map` 属性（让它满足协议，且既有测试可继续显式传映射）：

```python
        # 自带状态词汇表（与 FAKE_STATUS_MAP 一致；注册时也可显式覆盖）
        self.status_map = dict(FAKE_STATUS_MAP)
```

（注意：`FAKE_STATUS_MAP` 定义在 `FakeConnector` 之前，可直接引用。）

4. **`BoomConnector` 之类测试内的临时连接器**：跑全量时若报"未提供状态映射"，在测试里补 `status_map = {...}` 类属性。

- [ ] **Step 6: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_loader.py tests/test_job_service.py -v
```
Expected: 全绿

- [ ] **Step 7: 全量回归**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -q
```
Expected: 全绿（既有 `register(conn, status_map=...)` 调用点不受影响）

- [ ] **Step 8: 提交**

```bash
git add apps/web/backend/app/catalog/loader.py apps/web/backend/app/services/job_connectors.py apps/web/backend/tests/test_catalog_loader.py apps/web/backend/tests/test_job_service.py
git commit -m "插件框架支持声明连接器，连接器自带状态映射

- plugin.json 新增可选 connectors_module，loader 动态收集 CONNECTORS
- JobConnector 协议加 status_map：状态词汇表属于连接器，
  register 缺省回落它（缺映射直接报错，避免任务永不终结）"
```

---

## Task 2: PluginService 注册插件连接器

**Files:**
- Modify: `apps/web/backend/app/plugins/service.py`
- Modify: `apps/web/backend/app/main.py`
- Test: `apps/web/backend/tests/test_plugin_service.py`

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_plugin_service.py` 末尾追加（**先读该文件**确认既有 fixture 名与构造 `PluginService` 的方式，沿用其写法）：

```python
async def test_attach_registers_plugin_connectors(store, plugin_package_with_connector):
    """挂载插件时把其连接器注册进 JobConnectorRegistry（幂等）。"""
    reg = JobConnectorRegistry()
    service = PluginService(
        registry=ToolRegistry(), config_store=..., packages={...},
        skill_service=..., assistant_repo=..., job_connectors=reg)

    service.ensure_attached("demo")
    assert [k for k in reg.kinds if k.startswith("demo.")] == ["demo.task"]

    service.ensure_attached("demo")          # 幂等：重复挂载不报错
    assert len([k for k in reg.kinds if k.startswith("demo.")]) == 1


async def test_attach_without_job_registry_is_noop(store, plugin_package_with_connector):
    """未注入 job_connectors 时静默跳过（保持既有装配向后兼容）。"""
    service = PluginService(..., job_connectors=None)
    service.ensure_attached("demo")          # 不抛
```

> `plugin_package_with_connector` fixture：在 `tmp_path` 下造一个带 `connectors.py`（内含 `CONNECTORS = [_DemoConnector()]`，`kind="demo.task"`、`plugin_id="demo"`、`status_map={"done": JobStatus.COMPLETED}`，三个 async 方法）的插件包，用 `scan_catalog([tmp_path])` 得到 `PluginPackage`。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_service.py -v -k connector
```
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'job_connectors'`

- [ ] **Step 3: 实现 PluginService 改动**

`app/plugins/service.py`：

1. `__init__` 加参数与字段：

```python
    def __init__(self, registry: "ToolRegistry", config_store: "PluginConfigStore",
                 packages: dict[str, PluginPackage], skill_service: Any,
                 assistant_repo: Any, job_connectors: Any = None) -> None:
```

docstring 的 Args 补：

```
            job_connectors: 任务连接器注册表（插件声明的连接器注册于此）；
                None 时跳过（未接入后台任务的宿主）。
```

函数体加：

```python
        self._job_connectors = job_connectors
        # 本服务已注册的连接器 kind（{插件 id: {kind}}），用于区分 startup 重放与跨插件冲突
        self._attached_connectors: dict[str, set[str]] = {}
```

2. `_attach()` 末尾（技能根那段之后）追加：

```python
        self._attach_connectors(package)

    def _attach_connectors(self, package: PluginPackage) -> None:
        """注册插件声明的任务连接器（幂等；未注入注册表时静默跳过）。

        与工具注册同口径：注册是"能力可用性"（进程级，谁装都该挂），可见性
        过滤由 CapabilityService 另算；被其它来源占用的 kind 告警跳过。

        Args:
            package: 插件包。
        """
        if self._job_connectors is None:
            return
        attached = self._attached_connectors.setdefault(package.id, set())
        for connector in load_plugin_connectors(package):
            kind = str(getattr(connector, "kind", "")).strip()
            if not kind or kind in attached:
                continue  # 无名或本插件已注册（startup 重放），静默跳过
            if kind in self._job_connectors.kinds:
                logger.warning("任务类型 %s 已被其它来源注册，跳过插件 %s 的同名连接器",
                               kind, package.id)
                continue
            try:
                self._job_connectors.register(connector)
            except ValueError as exc:
                # 连接器形状/映射不合法：告警跳过，不阻断插件挂载
                logger.warning("插件 %s 的连接器 %s 注册失败: %s",
                               package.id, kind, exc)
                continue
            attached.add(kind)
```

3. import 补：`from app.catalog.loader import PluginPackage, load_plugin_connectors, load_plugin_tools`

- [ ] **Step 4: `main.py` 调整装配顺序**

当前顺序（Task 12 of C1）：`plugin_service.startup()` → `app.state.job_connectors = JobConnectorRegistry()` → `JobService(...)`。

**必须改成**：registry 先建 → 传给 `PluginService` → 再 startup。

```python
    # 任务连接器注册表：必须先于 PluginService（插件在其 startup/挂载时注册连接器）
    app.state.job_connectors = JobConnectorRegistry()
    app.state.plugin_service = PluginService(
        registry=REGISTRY, config_store=plugin_config_store, packages=index.plugins,
        skill_service=app.state.skill_service,
        assistant_repo=app.state.assistant_repo,
        job_connectors=app.state.job_connectors,
    )
    await app.state.plugin_service.startup(
        extra_plugin_ids=await UserCapabilityRepo(store).installed_plugin_ids())
```

并**删掉**原来在 AgentService 之后那段的 `app.state.job_connectors = JobConnectorRegistry()` 一行（避免覆盖已注册的连接器）。`JobService(...)` 构造处的 `connectors=app.state.job_connectors` 保持不变。

同时把那段注释改成与事实一致：

```python
    # 后台任务：任务服务 → 轮询器。连接器注册表在 PluginService 之前已建
    # （插件在其挂载时注册连接器，见上）；唤醒回调和 run 结束回调在此接线。
```

- [ ] **Step 5: 跑测试**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_service.py tests/test_jobs_e2e.py -v
```
Expected: 全绿

- [ ] **Step 6: 全量回归**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -q
```
Expected: 全绿（尤其 `test_jobs_e2e.py` 的装配链路不能被顺序调整破坏）

- [ ] **Step 7: 提交**

```bash
git add apps/web/backend/app/plugins/service.py apps/web/backend/app/main.py apps/web/backend/tests/test_plugin_service.py
git commit -m "插件挂载时注册任务连接器

- PluginService 接 job_connectors，_attach 里注册插件声明的连接器
  （幂等、跨插件重名告警跳过、形状不合法不阻断挂载）
- main.py 把 registry 创建提到 PluginService 之前（插件 startup 才能注册）"
```

---

## Task 3: 调度上下文补 `workspace_root`

**为什么**：Spec_Agent 只接受 `file_id` 提交，连接器必须**读用户工作区的谱图文件**上传。而当前 `ctx` 只有 `{config, ai4ms_token}`，插件拿不到工作根。

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Modify: `apps/web/backend/app/services/job_service.py`
- Test: `apps/web/backend/tests/test_job_service.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_job_service.py` 末尾追加：

```python
async def test_submit_persists_workspace_root(store):
    """提交时把工作区根落进 job 文档（轮询路径据此重建 ctx）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]))
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"workspace_root": "/data/users/u1/sessions/s1/workspace"})
    doc = await service.get(result.data["job_id"])
    assert doc["workspace_root"] == "/data/users/u1/sessions/s1/workspace"


async def test_ctx_for_carries_workspace_root_on_both_paths(store):
    """提交路径从 ctx_extra 取工作根；轮询路径从 job 文档取。"""
    service, reg = _job_service(store)
    conn = make_fake_connector("k", plugin_id="p1", script=["queued"])
    reg.register(conn)
    await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"workspace_root": "/w/sub"})
    # 提交路径：连接器收到的 ctx 带工作根
    assert conn.submitted[0]["ctx"]["workspace_root"] == "/w/sub"
    # 轮询路径：无 ctx_extra 时从 job 文档重建
    doc = await service.list_for_session("s1")
    ctx = await service._ctx_for("u1", "p1",  # noqa: SLF001
                                 workspace_root=str(doc[0].get("workspace_root") or ""))
    assert ctx["workspace_root"] == "/w/sub"


async def test_ctx_for_without_workspace_root_is_empty_string(store):
    """无工作区（历史任务）时给空串，插件据此给出可读错误。"""
    service, _ = _job_service(store)
    ctx = await service._ctx_for("u1", "p1")  # noqa: SLF001
    assert ctx["workspace_root"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v -k workspace
```
Expected: FAIL — `KeyError: 'workspace_root'`

- [ ] **Step 3: 实现**

**(a) `agent_service.py`** —— `chat()` 里的 `ctx_extra_snapshot` 加一个键（找 `"wake_source"` 附近）：

```python
                # 工作区根：插件连接器读取用户文件（如谱图上传）时用
                "workspace_root": str(workspace_root),
```

**(b) `job_service.py`** —— 三处：

1. `_ctx_for` 签名与返回：

```python
    async def _ctx_for(self, user_id: str, plugin_id: str,
                       ctx_extra: dict | None = None,
                       workspace_root: str = "") -> dict:
        """构造连接器调用上下文。

        Args:
            user_id: 任务归属用户（轮询路径用它重新解析配置）。
            plugin_id: 归属插件 id。
            ctx_extra: 提交路径可直接给出的运行上下文（含 plugins/ai4ms_token/
                workspace_root）；为 None 时（轮询路径）从插件配置存储与代签服务重建。
            workspace_root: 轮询路径的工作区根（提交路径从 ctx_extra 取，忽略本参数）。

        Returns:
            {"config": {插件配置}, "ai4ms_token": "<token 或空串>",
             "workspace_root": "<工作区根或空串>"}。
        """
        if ctx_extra is not None:
            plugins = ctx_extra.get("plugins") or {}
            return {
                "config": dict(plugins.get(plugin_id) or {}),
                "ai4ms_token": str(ctx_extra.get("ai4ms_token") or ""),
                "workspace_root": str(ctx_extra.get("workspace_root") or ""),
            }
        config: dict = {}
        if self._plugin_config_store is not None:
            try:
                config = await self._plugin_config_store.resolved_for_user(
                    user_id, plugin_id)
            except Exception:  # noqa: BLE001 解密失败等：按无配置处理，任务照常轮询
                _LOGGER.warning("轮询时解析插件配置失败 plugin=%s user=%s",
                                plugin_id, user_id, exc_info=True)
        return {"config": config, "ai4ms_token": "", "workspace_root": workspace_root}
```

2. `submit` 落库时加字段（在 `"params": params,` 之后）：

```python
            "workspace_root": str((ctx_extra or {}).get("workspace_root") or ""),
```

3. `refresh` 里调 `_ctx_for` 的那处补参数：

```python
        ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                  registered.connector.plugin_id,
                                  workspace_root=str(doc.get("workspace_root") or ""))
```

（`cancel` 里的 `_ctx_for` 调用同样补上，保持一致。）

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: 全绿

- [ ] **Step 5: 全量回归 + 提交**

```bash
cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest -q
git add apps/web/backend/app/services/agent_service.py apps/web/backend/app/services/job_service.py apps/web/backend/tests/test_job_service.py
git commit -m "连接器调度上下文补 workspace_root

- agent_service 把工作根注入 ctx_extra，submit 落进 job 文档
- _ctx_for 两条路径都带 workspace_root（提交从 ctx_extra、轮询从 job 文档）
- 插件据此能读取用户工作区的文件（Spec_Agent 只接受 file_id 提交）"
```

---

## Task 4: 任务结果回填（`fetch_result`）

**为什么**：C1 的 `result` 字段**没有任何写入方**，唤醒文本对模型只说"任务已结束"却不带结果，模型只能反问用户。补一个可选能力：连接器实现 `fetch_result` 时，`JobService` 在任务成功终态取回结果写入 job 文档。

**Files:**
- Modify: `apps/web/backend/app/services/job_service.py`
- Test: `apps/web/backend/tests/test_job_service.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_refresh_fetches_result_on_success(store):
    """成功终态时调连接器的 fetch_result 并把结果写入 job 文档。"""
    class ResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            return '{"peaks": [1.2, 3.4]}'

    service, reg = _job_service(store)
    reg.register(ResultConnector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    assert doc["result"] == '{"peaks": [1.2, 3.4]}'


async def test_refresh_without_fetch_result_leaves_result_empty(store):
    """连接器未实现 fetch_result 时结果保持空（不报错）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    assert not doc.get("result")


async def test_fetch_result_failure_does_not_break_transition(store):
    """取结果失败只告警，任务状态照常落地（结果留空）。"""
    class BrokenResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            raise RuntimeError("取结果炸了")

    service, reg = _job_service(store)
    reg.register(BrokenResultConnector("k", plugin_id="p1", script=["done"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"


async def test_failed_task_does_not_fetch_result(store):
    """失败终态不取结果（上游失败时 result 无意义）。"""
    calls: list[str] = []

    class CountingResultConnector(FakeConnector):
        async def fetch_result(self, external_id, ctx):
            calls.append(external_id)
            return "x"

    service, reg = _job_service(store)
    reg.register(CountingResultConnector("k", plugin_id="p1", script=["failed"]))
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "failed"
    assert calls == []
```

> `FakeConnector` 的 `status_map` 已含 `"failed" → FAILED`（Task 1 加的），故这里可直接用。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v -k fetch_result
```
Expected: FAIL — `KeyError: 'result'`

- [ ] **Step 3: 实现**

`job_service.py` 的 `refresh`（`_refresh_locked`）里，落库前补结果。把当前这段：

```python
        fields: dict = {"status": mapped.value, "last_raw_status": str(raw)}
        if is_terminal(mapped):
            fields["ended_at"] = time.time()
        updated = await self._repo.update(doc["_id"], fields)
```

改为：

```python
        fields: dict = {"status": mapped.value, "last_raw_status": str(raw)}
        if is_terminal(mapped):
            fields["ended_at"] = time.time()
        updated = await self._repo.update(doc["_id"], fields)
        if mapped is JobStatus.COMPLETED:
            result = await self._fetch_result(registered.connector,
                                              str(doc.get("external_id", "")), ctx)
            if result:
                updated = await self._repo.update(doc["_id"], {"result": result})
```

并在 `refresh` 之后新增私有方法：

```python
    async def _fetch_result(self, connector: Any, external_id: str,
                            ctx: dict) -> str:
        """取任务结果（连接器未实现或取回失败时返回空串）。

        Args:
            connector: 连接器实例。
            external_id: 外部任务 id。
            ctx: 连接器调用上下文。

        Returns:
            结果文本；失败时空串（只告警，不影响状态落地与唤醒）。
        """
        fetch = getattr(connector, "fetch_result", None)
        if not callable(fetch):
            return ""
        try:
            return str(await fetch(external_id, ctx) or "")
        except Exception:  # noqa: BLE001 取结果失败不阻断状态流转
            _LOGGER.warning("取任务结果失败 job_external_id=%s", external_id,
                            exc_info=True)
            return ""
```

- [ ] **Step 4: 跑测试确认通过 + 全量 + 提交**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
conda run -n synlysagent --no-capture-output python -m pytest -q
git add apps/web/backend/app/services/job_service.py apps/web/backend/tests/test_job_service.py
git commit -m "任务成功终态回填结果

- refresh 在 COMPLETED 时探测连接器的可选 fetch_result 并写入 job.result
- 取结果失败只告警，不影响状态落地与唤醒（结果留空）
- 唤醒文本自此带上结果，模型无需再反问用户"
```

---

## Task 5: 插件连接器实现（5 个谱图任务）

**Files:**
- Create: `apps/web/backend/catalog/plugins/spec_agent/connectors.py`
- Test: `apps/web/backend/tests/test_plugin_connectors.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_plugin_connectors.py`：

```python
"""Spec_Agent 谱图任务连接器测试（httpx MockTransport 模拟上游）。"""
import json
from pathlib import Path

import httpx
import pytest

from synlys_harness import JobStatus

from app.services.job_connectors import JobSubmitFailed

# 插件目录不在 sys.path 上，按 loader 的方式动态加载
PLUGIN_DIR = (Path(__file__).resolve().parents[1]
              / "catalog" / "plugins" / "spec_agent")


def _load_module():
    """动态加载插件的连接器模块（每次返回同一模块实例）。"""
    import importlib.util
    import sys

    name = "spec_agent_connectors_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "connectors.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connectors():
    """插件的 5 个连接器实例。"""
    return _load_module().CONNECTORS


@pytest.fixture
def workspace(tmp_path):
    """造一个含谱图文件的工作区。"""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sample.nmr").write_bytes(b"fake-nmr-binary")
    return root


def _ctx(root, transport):
    """构造连接器上下文（把 MockTransport 塞进 client 工厂）。"""
    return {"config": {"base_url": "http://spec.test"},
            "ai4ms_token": "tok-1",
            "workspace_root": str(root)}


def test_five_kinds_declared(connectors):
    """声明 5 个谱图任务，kind 与 plugin_id 正确、状态映射齐备。"""
    kinds = sorted(c.kind for c in connectors)
    assert kinds == ["spec.task.gpc", "spec.task.ir", "spec.task.lcms",
                     "spec.task.nmr", "spec.task.raman"]
    for c in connectors:
        assert c.plugin_id == "spec_agent"
        assert c.status_map["success"] is JobStatus.COMPLETED
        assert c.status_map["canceled"] is JobStatus.CANCELLED


async def test_submit_uploads_then_creates_task(connectors, workspace, monkeypatch):
    """submit 先上传拿 file_id，再以 file_id 提任务，返回上游 task_id。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "message": "ok",
                                             "data": {"file_id": "FILE-1"}})
        if request.url.path == "/api/v1/tasks/nmr":
            body = json.loads(request.content)
            assert body["input"] == {"input_type": "file_id", "file_id": "FILE-1"}
            return httpx.Response(200, json={"code": 0, "message": "ok",
                                             "data": {"task_id": "T-9",
                                                      "task_type": "nmr_analysis",
                                                      "status": "PENDING"}})
        return httpx.Response(404)

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")

    task_id = await nmr.submit({"path": "sample.nmr"}, _ctx(workspace, None))

    assert task_id == "T-9"
    assert [r.url.path for r in seen] == ["/api/v1/files/upload",
                                          "/api/v1/tasks/nmr"]
    assert seen[0].headers["authorization"] == "Bearer tok-1"


async def test_submit_rejects_missing_or_escaping_path(connectors, workspace,
                                                       monkeypatch):
    """缺少 path、越界路径、文件不存在都给出可读失败。"""
    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(500)))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")

    with pytest.raises(JobSubmitFailed):
        await nmr.submit({}, _ctx(workspace, None))
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "../../etc/passwd"}, _ctx(workspace, None))
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "nope.nmr"}, _ctx(workspace, None))


async def test_submit_requires_configured_base_url(connectors, workspace):
    """未配置服务地址时给出可读失败。"""
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    ctx = {"config": {}, "ai4ms_token": "", "workspace_root": str(workspace)}
    with pytest.raises(JobSubmitFailed):
        await nmr.submit({"path": "sample.nmr"}, ctx)


async def test_poll_returns_raw_status(connectors, monkeypatch):
    """poll 返回上游状态原文（由 status_map 翻译）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"task_id": "T-9",
                                                  "status": "RUNNING",
                                                  "progress": 40}})

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    assert await nmr.poll("T-9", {"config": {"base_url": "http://spec.test"}}) == "RUNNING"


async def test_fetch_result_serializes_payload(connectors, monkeypatch):
    """fetch_result 把上游 result 对象序列化为文本。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"task_id": "T-9",
                                                  "status": "SUCCESS",
                                                  "result": {"peaks": [1.2, 3.4]}}})

    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    text = await nmr.fetch_result("T-9", {"config": {"base_url": "http://spec.test"}})
    assert "peaks" in text and "1.2" in text


async def test_cancel_posts_nothing_and_reports_false(connectors, monkeypatch):
    """上游无取消接口 → cancel 返回 False（本地仍收敛为 cancelled）。"""
    module = _load_module()
    monkeypatch.setattr(module, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(404)))
    nmr = next(c for c in connectors if c.kind == "spec.task.nmr")
    assert await nmr.cancel("T-9", {"config": {"base_url": "http://spec.test"}}) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_connectors.py -v
```
Expected: FAIL — `FileNotFoundError`（`connectors.py` 尚不存在）

- [ ] **Step 3: 实现插件连接器**

创建 `apps/web/backend/catalog/plugins/spec_agent/connectors.py`：

```python
"""Spec_Agent 谱图解析异步任务的连接器（插件包自带，宿主动态加载）。

覆盖上游 5 种任务：NMR / IR / GPC / Raman / LC-MS。上游约束（2026-09-16 实测）：
- 提交**只接受 file_id**（`input_type="file_id"`），故必须先上传谱图文件；
- 状态枚举 `PENDING/QUEUED/RUNNING/SUCCESS/FAILED/CANCELED`（单 L），
  由本模块的 `SPEC_STATUS_MAP` 翻译成 harness 的统一状态；
- 结果走独立端点 `/tasks/{id}/result`，`result` 是自由 JSON。

配置与凭证沿用宿主通用通道：`ctx["config"]`（插件配置）+ `ctx["ai4ms_token"]`
（按登录用户代签）；`ctx["workspace_root"]` 是用户工作区根，谱图文件从这里读。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from synlys_harness import JobStatus

# 宿主定义的连接器异常：插件用它区分"提交被拒"与"查询失败"（宿主按类型捕获，
# 必须用同一个类）。这是插件对宿主的最小依赖，与 tools.py 依赖 synlys_harness 同类。
from app.services.job_connectors import JobPollFailed, JobSubmitFailed

PLUGIN_ID = "spec_agent"

CONNECT_TIMEOUT_S = 10.0   # 连接超时（宿主不可达时快速失败）
REQUEST_TIMEOUT_S = 60.0   # 读超时（上传/提交/查状态都是秒级操作）
UPLOAD_TIMEOUT_S = 120.0   # 上传单独放宽（谱图文件可能较大）

# 上游状态原文（小写）→ 统一状态；上游用的是 SUCCESS/CANCELED，与 harness 不同名
SPEC_STATUS_MAP: dict[str, JobStatus] = {
    "pending": JobStatus.PENDING,
    "queued": JobStatus.PENDING,
    "running": JobStatus.RUNNING,
    "success": JobStatus.COMPLETED,
    "failed": JobStatus.FAILED,
    "canceled": JobStatus.CANCELLED,
}

# 测试可经 monkeypatch 注入 MockTransport（None = 走真实网络）
_transport: httpx.BaseTransport | None = None


class SpectraTaskConnector:
    """一种谱图解析任务的连接器（5 种任务各一个实例）。

    Attributes:
        kind: 本平台的任务类型（如 spec.task.nmr）。
        endpoint: 上游 URL 路径段（nmr/ir/gpc/raman/lcms）。
        task_type: 上游 task_type（如 nmr_analysis）。
        status_map: 上游状态原文 → 统一状态。
    """

    plugin_id = PLUGIN_ID

    def __init__(self, kind: str, endpoint: str, task_type: str) -> None:
        """初始化。

        Args:
            kind: 本平台任务类型。
            endpoint: 上游 URL 路径段。
            task_type: 上游任务类型标识。
        """
        self.kind = kind
        self.endpoint = endpoint
        self.task_type = task_type
        self.status_map = dict(SPEC_STATUS_MAP)

    # ---------- 连接器协议 ----------

    async def submit(self, params: dict, ctx: dict) -> str:
        """读工作区文件 → 上传换 file_id → 提交任务，返回上游 task_id。

        Args:
            params: 工具传入的参数，需含 `path`（工作区内的谱图文件相对路径）；
                可选 `params`（透传给上游的任务参数对象）。
            ctx: 宿主填充的上下文（config/ai4ms_token/workspace_root）。

        Returns:
            上游 task_id。

        Raises:
            JobSubmitFailed: 未配置服务地址、缺 path、路径非法、文件不存在，
                或上游拒绝（含响应体摘要）。
        """
        base_url, headers = self._conn(ctx)
        target = self._locate(ctx, params)
        async with self._client(UPLOAD_TIMEOUT_S) as client:
            file_id = await self._upload(client, base_url, headers, target)
            body = {
                "input": {"input_type": "file_id", "file_id": file_id},
                "params": params.get("params") or {},
            }
            try:
                resp = await client.post(
                    f"{base_url}/api/v1/tasks/{self.endpoint}", headers=headers,
                    json=body)
            except httpx.TimeoutException as exc:
                raise JobSubmitFailed(f"提交任务超时: {exc}") from exc
            except httpx.HTTPError as exc:
                raise JobSubmitFailed(f"提交任务失败: {exc}") from exc
        data = self._unwrap(resp, "提交任务")
        task_id = str((data or {}).get("task_id") or "")
        if not task_id:
            raise JobSubmitFailed(f"上游未返回 task_id: {str(data)[:200]}")
        return task_id

    async def poll(self, external_id: str, ctx: dict) -> str:
        """查询任务状态原文。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            上游状态原文（如 "RUNNING"）。

        Raises:
            JobPollFailed: 网络/上游异常（宿主保持原状态、下轮重试）。
        """
        base_url, headers = self._conn(ctx)
        async with self._client(REQUEST_TIMEOUT_S) as client:
            try:
                resp = await client.get(
                    f"{base_url}/api/v1/tasks/{external_id}", headers=headers)
            except httpx.HTTPError as exc:
                raise JobPollFailed(f"查询任务状态失败: {exc}") from exc
        data = self._unwrap(resp, "查询任务状态")
        return str((data or {}).get("status") or "")

    async def fetch_result(self, external_id: str, ctx: dict) -> str:
        """取任务结果并序列化为文本（仅成功终态由宿主调用）。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            结果 JSON 文本；上游无结果时返回空串。
        """
        base_url, headers = self._conn(ctx)
        async with self._client(REQUEST_TIMEOUT_S) as client:
            try:
                resp = await client.get(
                    f"{base_url}/api/v1/tasks/{external_id}/result", headers=headers)
            except httpx.HTTPError:
                return ""  # 取结果失败不阻断状态流转（宿主只记日志）
        data = self._unwrap(resp, "取任务结果", raise_on_error=False) or {}
        result = data.get("result")
        if result is None:
            return ""
        return json.dumps(result, ensure_ascii=False)

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """取消任务。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            恒为 False：上游 v1 未提供取消接口，本平台的"取消"是本地收敛
            （任务标记为 cancelled、停止轮询）；上游任务会自行跑完。
        """
        return False

    # ---------- 内部 ----------

    def _conn(self, ctx: dict) -> tuple[str, dict[str, str]]:
        """取上游地址与请求头。

        Args:
            ctx: 连接器上下文。

        Returns:
            (base_url 去尾斜杠, headers)。

        Raises:
            JobSubmitFailed: 未配置 base_url。
        """
        config = ctx.get("config") or {}
        base_url = str(config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise JobSubmitFailed(
                "谱图解析插件未配置服务地址（请在管理后台的插件页安装并填写）")
        headers: dict[str, str] = {}
        # 凭证顺序：宿主按登录用户代签的动态 token 优先，回落插件配置的服务 token
        token = str(ctx.get("ai4ms_token") or config.get("token") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return base_url, headers

    def _locate(self, ctx: dict, params: dict) -> Path:
        """把 path 参数解析为工作区内的真实文件。

        Args:
            ctx: 连接器上下文（取 workspace_root）。
            params: 工具参数。

        Returns:
            谱图文件绝对路径。

        Raises:
            JobSubmitFailed: 缺 path、无工作区、路径越界或文件不存在。
        """
        rel = str(params.get("path") or "").strip()
        if not rel:
            raise JobSubmitFailed("缺少参数 path（工作区内的谱图文件路径）")
        root_str = str(ctx.get("workspace_root") or "")
        if not root_str:
            raise JobSubmitFailed("当前会话没有可用的工作区，无法读取谱图文件")
        root = Path(root_str).resolve()
        try:
            target = (root / rel).resolve()
        except OSError as exc:
            raise JobSubmitFailed(f"路径非法: {rel}") from exc
        if root not in target.parents:
            raise JobSubmitFailed(f"路径越界: {rel}")
        if not target.is_file():
            raise JobSubmitFailed(f"文件不存在: {rel}")
        return target

    async def _upload(self, client: httpx.AsyncClient, base_url: str,
                      headers: dict[str, str], target: Path) -> str:
        """上传谱图文件，返回上游 file_id。

        Args:
            client: HTTP 客户端。
            base_url: 上游地址。
            headers: 请求头。
            target: 本地文件路径。

        Returns:
            上游 file_id。

        Raises:
            JobSubmitFailed: 上传失败或未返回 file_id。
        """
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise JobSubmitFailed(f"读取文件失败: {exc}") from exc
        files = {"file": (target.name, content, "application/octet-stream")}
        try:
            resp = await client.post(f"{base_url}/api/v1/files/upload",
                                     headers=headers, files=files)
        except httpx.TimeoutException as exc:
            raise JobSubmitFailed(f"上传谱图文件超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JobSubmitFailed(f"上传谱图文件失败: {exc}") from exc
        data = self._unwrap(resp, "上传谱图文件")
        file_id = str((data or {}).get("file_id") or "")
        if not file_id:
            raise JobSubmitFailed(f"上游未返回 file_id: {str(data)[:200]}")
        return file_id

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        """构造 HTTP 客户端（测试经模块级 `_transport` 注入 MockTransport）。

        Args:
            timeout_s: 读超时。

        Returns:
            异步客户端。
        """
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S),
            transport=_transport)

    @staticmethod
    def _unwrap(resp: httpx.Response, action: str,
                raise_on_error: bool = True) -> dict | None:
        """解包上游统一响应（`{code, message, data}`）。

        Args:
            resp: 上游响应。
            action: 动作名（拼错误文案用）。
            raise_on_error: 失败时是否抛 JobSubmitFailed（False 时返回 None）。

        Returns:
            `data` 字段；失败且不抛时返回 None。

        Raises:
            JobSubmitFailed: HTTP 非 200、非 JSON、或 code != 0。
        """
        def _fail(msg: str) -> None:
            if raise_on_error:
                raise JobSubmitFailed(msg)

        if resp.status_code in (401, 403):
            _fail("访问凭证无效或已过期（管理员可在管理后台「插件」页更新凭证）")
            return None
        if resp.status_code != 200:
            _fail(f"{action}失败：上游返回 {resp.status_code} {resp.text[:200]}")
            return None
        try:
            body = resp.json() or {}
        except ValueError:
            _fail(f"{action}失败：上游返回非 JSON")
            return None
        if body.get("code") != 0:
            _fail(f"{action}失败：{body.get('message') or body.get('code')}")
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None


# 本插件贡献的连接器（宿主 PluginService 挂载时注册）
CONNECTORS = [
    SpectraTaskConnector("spec.task.nmr", "nmr", "nmr_analysis"),
    SpectraTaskConnector("spec.task.ir", "ir", "ir_analysis"),
    SpectraTaskConnector("spec.task.gpc", "gpc", "gpc_analysis"),
    SpectraTaskConnector("spec.task.raman", "raman", "raman_analysis"),
    SpectraTaskConnector("spec.task.lcms", "lcms", "lcms_analysis"),
]
```

**关于 `JobPollFailed` / `JobSubmitFailed` 的来源**：它们由宿主定义在 `app.services.job_connectors`，插件**直接 import 同一个类**——宿主按类型捕获（`except JobPollFailed` 保持原状态重试、`except JobSubmitFailed` 判失败），若插件另定义同名类就捕获不到了。所以上面的 import 必须照写。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_connectors.py -v
```
Expected: 7 passed

- [ ] **Step 5: 全量回归 + 提交**

```bash
cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest -q
git add apps/web/backend/catalog/plugins/spec_agent/connectors.py apps/web/backend/tests/test_plugin_connectors.py
git commit -m "Spec_Agent 五个谱图任务接入为后台任务

- 新增插件连接器：读工作区文件 → 上传换 file_id → 提任务 → 轮询 → 取结果
- 上游 SUCCESS/CANCELED 经状态映射翻译为 completed/cancelled
- 上游无取消接口，cancel 返回 False（本地收敛，任务不再被轮询）"
```

---

## Task 6: 插件声明、技能说明与专家白名单

**Files:**
- Modify: `apps/web/backend/catalog/plugins/spec_agent/plugin.json`
- Create: `apps/web/backend/catalog/plugins/spec_agent/skills/spec-spectra/SKILL.md`
- Test: `apps/web/backend/tests/test_spec_agent_plugin.py`

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_spec_agent_plugin.py` 末尾追加：

```python
def test_plugin_declares_connectors_and_skill():
    """spec_agent 插件声明连接器模块与谱图任务技能。"""
    plugin_dir = (Path(__file__).resolve().parents[1]
                  / "catalog" / "plugins" / "spec_agent")
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["connectors_module"] == "connectors.py"
    assert "spec-spectra" in manifest["skills"]
    skill = plugin_dir / "skills" / "spec-spectra" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    for kind in ("spec.task.nmr", "spec.task.ir", "spec.task.gpc",
                 "spec.task.raman", "spec.task.lcms"):
        assert kind in text
```

（文件头若无 `json` / `Path` import，按需补。）

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_spec_agent_plugin.py -v -k connectors_and_skill
```
Expected: FAIL — `KeyError: 'connectors_module'`

- [ ] **Step 3: 改 plugin.json**

在 `apps/web/backend/catalog/plugins/spec_agent/plugin.json` 里：

1. `"tools_module": "tools.py",` 之后加一行：

```json
  "connectors_module": "connectors.py",
```

2. `"skills": ["spec-nmr"],` 改为：

```json
  "skills": ["spec-nmr", "spec-spectra"],
```

3. 专家 `tool_whitelist` 里补 `job.*`（虽然宿主的 `SKILL_TOOLS` 已无条件追加，显式列出让管理员在详情页一眼看到该专家能用后台任务）：

```json
      "job.submit",
      "job.status",
      "job.list",
      "job.cancel"
```

- [ ] **Step 4: 新建技能说明**

创建 `apps/web/backend/catalog/plugins/spec_agent/skills/spec-spectra/SKILL.md`：

```markdown
---
name: spec-spectra
description: 谱图解析异步任务（NMR / IR / GPC / Raman / LC-MS）。用户要求解析谱图文件、给出谱峰或分子量分布时使用；用 job.submit 提交，完成后系统会通知你。
version: "1.0"
author: AI⁴MS
tags: [谱图, 异步任务]
---

# 谱图解析异步任务

把工作区里的**谱图文件**提交给 Spec_Agent 做解析。任务在后台跑，完成后系统会
自动通知你继续处理——**提交后不要反复查询，也不要重复提交同一文件**。

## 用法

用 `job.submit` 提交，`kind` 按下表选，`params` 至少给 `path`：

```
job.submit(
  kind="spec.task.nmr",
  params={"path": "files/sample.nmr"},
  label="样品 A 的核磁解析")
```

- `path`：**工作区内的相对路径**（用 `file.list` 查看有哪些文件）
- `params.params`（可选）：透传给上游的任务参数对象，见下表

## 五种任务

| kind | 谱图类型 | 说明 |
|---|---|---|
| `spec.task.nmr` | 核磁共振（NMR） | 峰检测与积分；可给 `nucleus`、`threshold`、`min_distance` 等峰检测参数 |
| `spec.task.ir` | 红外（IR） | 官能团与谱峰归属 |
| `spec.task.gpc` | 凝胶渗透色谱（GPC） | 分子量与分子量分布 |
| `spec.task.raman` | 拉曼（Raman） | 拉曼峰位与归属 |
| `spec.task.lcms` | 液质联用（LC-MS） | 色谱峰与质谱解析 |

## 其他操作

- 查进度：`job.status(job_id)`（只在用户主动问、或完成通知信息不足时用）
- 列任务：`job.list()`
- 取消：`job.cancel(job_id)`——**上游无取消接口**，取消是本地停止跟踪，
  上游任务仍会跑完；所以请在提交前确认文件选对了

## 什么时候用同步三件套

`spec.nmr.forward` / `spec.nmr.reverse` / `spec.nmr.search` 是**同步**的分子结构
预测与库检索（输入 SMILES 或化学位移，不走文件），适合快速试算；本技能是**异步**
的谱图文件解析，适合跑真实谱图数据。按用户诉求选：给了文件用这里，给了结构或
位移串用同步三件套。
```

- [ ] **Step 5: 跑测试 + 全量 + 提交**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_spec_agent_plugin.py -v
conda run -n synlysagent --no-capture-output python -m pytest -q
git add apps/web/backend/catalog/plugins/spec_agent/plugin.json apps/web/backend/catalog/plugins/spec_agent/skills/spec-spectra/SKILL.md apps/web/backend/tests/test_spec_agent_plugin.py
git commit -m "spec_agent 插件声明连接器与谱图任务技能

- plugin.json 声明 connectors_module 与 spec-spectra 技能，白名单补 job.*
- 技能说明写清 5 个 kind、path 参数与异步语义（提交后勿轮询）
- 与同步三件套的分工（文件走异步、结构/位移走同步）"
```

---

## Task 7: 端到端验收与文档

**Files:**
- Test: `apps/web/backend/tests/test_jobs_e2e.py`（追加）
- Modify: `apps/web/backend/README.md`、`README.md`

- [ ] **Step 1: 写端到端测试**

在 `tests/test_jobs_e2e.py` 末尾追加（**用真插件连接器 + MockTransport 模拟 Spec_Agent**，验证"装配 → 提交 → 轮询 → 唤醒"整条链路）：

```python
async def test_spec_agent_plugin_end_to_end(app, session_id, tmp_path, monkeypatch):
    """插件连接器走完整链路：提交 → 轮询成功 → 结果回填 → 会话收到唤醒消息。"""
    import importlib.util
    import json as _json
    from pathlib import Path as _Path

    import httpx

    # 1) 让插件的连接器模块走 MockTransport，模拟 Spec_Agent 的 5 个端点
    plugin_dir = (_Path(__file__).resolve().parents[1]
                  / "catalog" / "plugins" / "spec_agent")
    name = "spec_agent_connectors_e2e"
    spec = importlib.util.spec_from_file_location(name, plugin_dir / "connectors.py")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "F1"}})
        if request.url.path.endswith("/tasks/nmr"):
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "PENDING"}})
        if request.url.path == "/api/v1/tasks/T1":
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "SUCCESS"}})
        if request.url.path == "/api/v1/tasks/T1/result":
            return httpx.Response(200, json={"code": 0,
                                             "data": {"task_id": "T1",
                                                      "status": "SUCCESS",
                                                      "result": {"peaks": [1.2]}}})
        return httpx.Response(404)

    monkeypatch.setattr(module, "_transport", httpx.MockTransport(handler))

    # 2) 注册插件连接器（宿主装配路径：这里直接注册到运行中的 registry）
    for connector in module.CONNECTORS:
        app.state.job_connectors.register(connector)

    # 3) 造工作区文件 + 插件配置（base_url 指向 Mock 上游）
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "sample.nmr").write_bytes(b"data")

    result = await app.state.job_service.handle(
        {"action": "submit", "kind": "spec.task.nmr",
         "params": {"path": "sample.nmr"}, "label": "端到端 NMR"},
        user={"sub": "u-user"}, session_id=session_id,
        ctx_extra={"workspace_root": str(workspace),
                   "plugins": {"spec_agent": {"base_url": "http://spec.test"}}})
    assert result.ok is True

    # 4) 两轮 tick：状态推到 SUCCESS 并回填结果
    await app.state.job_poller.tick()
    doc = await app.state.job_service.get(result.data["job_id"])
    assert doc["status"] == "completed"
    assert "peaks" in (doc.get("result") or "")

    # 5) 唤醒消息落进会话（含结果摘要）
    import asyncio
    wake: list = []
    for _ in range(40):
        events = await app.state.event_repo.list_events(session_id)
        wake = [e for e in events if e.type.value == "user/message"
                and e.payload.get("kind") == "job_completed"]
        if wake:
            break
        await asyncio.sleep(0.05)
    assert len(wake) == 1
    assert "/api/v1/files/upload" in calls and "/api/v1/tasks/nmr" in calls
```

- [ ] **Step 2: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs_e2e.py -v
```
Expected: 全绿（4 个用例）

- [ ] **Step 3: 文档**

**(a) `apps/web/backend/README.md`** 的「后台任务（Job 注册表）」章节补两段：

```markdown
**连接器扩展点（插件接入异步任务）**：插件目录放 `connectors.py` 并在 `plugin.json`
声明 `"connectors_module": "connectors.py"`，模块级导出 `CONNECTORS = [连接器实例, ...]`。
宿主在挂载插件时把它们注册进 `JobConnectorRegistry`（幂等；kind 跨插件重名告警跳过）。
连接器需实现 `JobConnector` 协议：`kind` / `plugin_id` / `status_map`（外部状态原文 →
统一状态）与 `submit` / `poll` / `cancel` 三个异步方法；可选实现
`fetch_result(external_id, ctx) -> str`，宿主在任务成功终态时调用它并把返回文本写入
job 的 `result`（会出现在唤醒消息里）。

连接器的调用上下文（`ctx`）由宿主填充：`{"config": 插件配置, "ai4ms_token": 用户代签
凭证, "workspace_root": 用户工作区根的绝对路径}`。

**已接入**：`spec_agent` 插件的 5 种谱图异步任务（`spec.task.nmr/ir/gpc/raman/lcms`）——
读工作区谱图文件 → 上传换 file_id → 提交任务 → 轮询 → 取结果。上游只接受 file_id
提交（故必须工作区根）；上游无取消接口，取消是本地停止跟踪。
```

**(b) 根 `README.md`**：把首页功能概述那段末尾补一句：

```markdown
AI⁴MS 子平台异步任务（Spec_Agent 五种谱图解析）经插件连接器接入，提交后自动跟踪与汇总。
```

并把「后续路线」里 C2 那行更新为已完成（保持与 13 号路线文档口径一致）。

- [ ] **Step 4: 提交**

```bash
git add apps/web/backend/tests/test_jobs_e2e.py apps/web/backend/README.md README.md
git commit -m "Spec_Agent 谱图异步任务端到端验收与文档

- e2e：插件连接器 + Mock 上游跑通提交→轮询→结果回填→唤醒
- 后端 README 补连接器扩展点与 ctx 契约；根 README 同步功能说明"
```

---

## 验收清单（全部任务完成后逐条核对）

- [x] `cd packages/synlys-harness && conda run -n synlysagent python -m pytest -q` 全绿
- [x] `cd apps/web/backend && conda run -n synlysagent python -m pytest -q` 全绿
- [x] `cd apps/web/frontend && npm run build` 通过（本计划不改前端，作回归）
- [ ] **真机验证（需 Spec_Agent 可达）**：管理后台「插件」页把 `spec_agent` 的服务地址填成 `http://10.26.15.93:8001`（凭证按需）→ 装上并启用 → 新建会话选「谱图解析专家」→ 上传一个谱图文件 → 让模型解析 → 观察：工具卡显示 `job.submit`、任务 ID 返回、若干秒后会话出现"后台任务已完成"提示条、模型给出解析结论
- [x] 版本号升到 `0.12.0-beta.1`（`app/version.py` 与 `frontend/package.json` 同步；新增能力、向下兼容）
- [x] 全链路自动化验收（Task 7）：`tests/test_jobs_e2e.py::test_spec_agent_plugin_end_to_end` 以 MockTransport 模拟上游，跑通「读工作区文件 → 上传换 file_id → 提任务 → 轮询 SUCCESS → `fetch_result` 回填 → 会话收到一条 `kind=job_completed` 唤醒消息」

## 明确不做（留给后续）

- **结果落工作区文件**：体量大的结果目前只进 job 文档（截断后进唤醒文本）。要交付成文件可让模型在唤醒后自行调 `file.write`，或后续给连接器加"结果落盘"能力
- **任务进度百分比**：上游 `progress` 字段已在响应里，但本平台状态机只有五态、无进度维度
- **`kind` 可见性收窄**：C1 审查记的遗留——`job.submit` 未按插件可见性过滤 `kind`，接了真连接器后需要补（用 `CapabilityService` 按 `connector.plugin_id` 收窄 `kinds`）
- **任务独立面板**：目前只在对话流里提示，无任务列表页
- **轮询失败无上限**：`JobPoller` 无失败计数上限、`JobService` 也不据 `poll_failures` 判失败。
  凭证过期（401）这类不可自愈的失败会让任务**永久停在 pending/running**——修复后至少会累计
  `poll_failures` 与 `last_poll_error`，但这两个字段**没有 API 出口**，用户侧仍无感知。建议后续
  补"连续失败 N 次判失败"并把 `last_poll_error` 透出到 job 查询响应。
- **插件文案**：插件的 `system_prompt` 未提及异步能力（仅 `description` 同步了），人设措辞属产品决策