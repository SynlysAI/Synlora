# Synlora 插件迁移（1 顶层智能体 + 3 子智能体）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Synlora 的 4 个插件（Spec_Agent 谱图解析、Poly_Agent 高分子预测、Sciverse 文献检索、ARS 学术研究技能套件）迁移为 Yuxi 内置能力，全部同步调用，不做后台 Job 模式。形态按交互密度分配：**ARS 学术研究 → 顶层智能体**（ChatbotAgent，交互密集：引导/确认/诚信闸门依赖 `ask_user_question` 与 `present_artifacts`）；**谱图解析 / 文献检索 / 高分子预测 → 子智能体**（SubAgentBackend，"输入→计算→结果"型任务，输入参数由主 Agent 派发前向用户收集，子智能体输入不足时直接返回缺失清单）。

**Architecture:** 工具注册进 `toolkits/buildin/`（每个插件一个模块，`category` 用插件专属值，不进默认 `"all"` 工具集）；工具可见性走「技能 `tool_dependencies` 门控 + preset `preload_skills` 预载」的现成链路（同 knowledge-base Skill 模式），该链路对 ChatbotAgent 与 SubAgentBackend 同样生效。技能放 `skills/buildin/<slug>/`，技能正文用中性措辞（"向调用方确认"），顶层与子智能体两个语境都成立。preset 混合形态：`presets/academic_research.py`（ChatbotAgent，同 `presets/deep_research.py`）+ `presets/subagents/{spec_agent,poly_agent,sciverse}.py`（SubAgentBackend，禁用 `ask_user_question`/`present_artifacts` 是框架既有约束，不改框架；子智能体产物写共享 Workdir 的 outputs/ 并在回复中给出相对路径，由主 Agent 决定展示）。主智能体通过 `context.subagents` 引用子智能体（`subagent_start` 派发 / `subagent_await` 收结果），preset description 写明"调用时需提供的输入"，引导主 Agent 先收齐参数再派发。上游服务地址与凭证全部走环境变量（`docker-compose.yml` 的 `x-api-worker-env` 锚点 + `.env.template`），未配置时工具 fail-closed 返回结构化错误。Synlora 的异步 Job（spec.task.*）改造成单工具内「上传 → 提交 → 内嵌轮询至终态 → 返回结果」。

**Tech Stack:** Python 3.12 / LangChain `@tool`（Yuxi 扩展装饰器）/ httpx.AsyncClient / Pydantic args_schema / ProvisionerSandboxBackend（工作区文件读写）/ pytest + httpx.MockTransport。

**Spec:** 本计划自含（需求来自 2026-10-08 会话确认：迁移对象为 Synlora 现有 4 插件；上游地址部署时可配置；形态统一内置子智能体；同步调用取代 Job 模式）。源项目参照实现：`E:\agent_projects\Synlora\apps\web\backend\catalog\plugins\{spec_agent,poly_agent,sciverse,ars}\`。

## Global Constraints

- **工具名必须 snake_case**（OpenAI function name 不允许点号）：`spec.nmr.forward` → `spec_nmr_forward` 等以此类推。
- **新工具 `category` 一律用插件专属值**（`spec_agent` / `poly_agent` / `sciverse`），**严禁 `buildin`**：`resolve_agent_resource_options` 的 `"all"` 工具集只收 `category="buildin"` 的工具，专属 category 保证 18 个新工具不泄漏进普通智能体的默认工具集。
- **错误处理双轨制**（照抄现有代码模式）：路径/参数非法 → `raise ValueError`（`handle_tool_error=True` 兜底转错误消息，同 `ocr_parse_file`）；上游服务问题 → 返回结构化 dict `{"ok": False, "error": "<错误码>", "message": "<人话描述>"}`，**不抛异常**（同 `web_search` 的 `{"error": ...}` 模式）。
- **超时层级**（自小到大，缺一不可）：单次 HTTP 读超时 < 谱图任务轮询总预算（`SPEC_TASK_MAX_WAIT_SECONDS`，默认 1500s，clamp 到 [60, 3300]）< worker ARQ `job_timeout`（默认 3600s）。连接超时一律 10s。
- **未配置 fail-closed**：`SPEC_AGENT_BASE_URL` / `POLY_AGENT_BASE_URL`+`USERNAME`+`PASSWORD` / `SCIVERSE_BASE_URL`+`API_TOKEN` 缺失时，工具返回 `{"ok": False, "error": "<插件名>_unconfigured", "message": "…设置环境变量 XXX"}`，不抛异常、不警告刷屏。
- **不迁移 ai4ms_token 代签机制**：Spec_Agent 认证只用静态 `SPEC_AGENT_TOKEN`（留空则不发 Authorization 头）。
- **路径安全**：所有读工作区文件的工具参数做 `..` 段拒绝 + 只允许落在当前 `workdir_path` 下（照抄 `tools.py::_resolve_ocr_source_path` 的口径）。
- **测试克制**：只写 `backend/test/unit/` 单测（httpx.MockTransport + FakeRuntime，不依赖 docker 服务与真实上游），不写 integration/e2e。
- **注释规范**：新函数写中文 docstring，有参数/返回值的带 `Args:` / `Returns:` 段；模块级常量大写。
- **git 提交**：简洁中文标题 + 要点列表，不加 AI 共同编辑署名。
- 现有 preset/技能发现测试（`test_builtin_discovery.py`、`test_agent_repository_deep_research.py`）断言了完整 slug/skill 集合，新增条目后必须同步更新，否则 `make test` 红。

## Review Focus

执行完自查这五个点（对应测试已分配进各任务）：

1. **工具 category 误写 `buildin` → 全体智能体工具列表被 18 个谱图工具污染**。Task 1 Step 6 断言 `get_tool_instances_by_category("buildin")` 不含任何 `spec_*` / `poly_*` / `sciverse_*` 工具。
2. **技能 `tool_dependencies` 与工具注册名拼写不一致 → 启动期 `validate_skill_dependencies` 报错或工具永远不加载**。Task 5 Step 4 断言每个内置技能的 `tool_dependencies` 都能在 `get_all_tool_instances()` 名字集合里找到。
3. **轮询总预算 ≥ worker job_timeout → ARQ 杀 job、lease 收敛 failed、结果丢失**。Task 2 Step 5 断言 `_task_max_wait_s()` clamp 上限 3300 且默认 1500。
4. **路径逃逸（`../`、绝对虚拟路径注入）读到工作区外文件**。Task 1 Step 5 用 FakeRuntime 直接测 `download_workspace_file` 拒绝 `..`、拒绝空路径、正常相对路径拼进 workdir。
5. **preset `ensure_preset` 只首次落库、不回填**：改 preset 代码对已落库环境不生效。测试无法覆盖（行为特性），Task 7 收尾步骤要求本地删掉同 slug 的 Agent 行（或用全新库）验证 preset 真实生效，并在提交信息里注明该特性。

---

### Task 1: 工作区 IO 公共模块 + Spec_Agent 核磁三件套工具

**Files:**
- Create: `backend/package/yuxi/agents/toolkits/buildin/_workspace_io.py`
- Create: `backend/package/yuxi/agents/toolkits/buildin/spec_agent.py`
- Modify: `backend/package/yuxi/agents/toolkits/buildin/__init__.py`（追加 import）
- Test: `backend/test/unit/agents/toolkits/buildin/test_workspace_io.py`、`backend/test/unit/agents/toolkits/buildin/test_spec_agent_tools.py`

**Interfaces:**
- Consumes: `yuxi.agents.backends.sandbox.ProvisionerSandboxBackend`、`yuxi.agents.toolkits.registry.tool`、langgraph `ToolRuntime`。
- Produces（后续任务依赖，签名固定）:
  - `download_workspace_file(relative_path: str, runtime: ToolRuntime, *, max_bytes: int) -> tuple[bytes, str]`（返回文件字节与文件名；路径非法/文件不存在 `raise ValueError`）
  - `upload_workspace_file(content: bytes, relative_path: str, runtime: ToolRuntime) -> str`（写入工作区，返回工作区相对显示路径）
  - `spec_agent.py` 模块级 `_transport: httpx.BaseTransport | None = None`（测试注入 MockTransport 的钩子，poly/sciverse 模块同款）
  - 工具 `spec_nmr_forward` / `spec_nmr_reverse` / `spec_nmr_search`（`category="spec_agent"`）

- [ ] **Step 1: 写 `_workspace_io` 的失败测试**

`backend/test/unit/agents/toolkits/buildin/test_workspace_io.py` 全文：

```python
"""工作区 IO 公共模块的单元测试（FakeRuntime，不依赖沙盒服务）。"""

from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.buildin._workspace_io import (
    download_workspace_file,
    resolve_virtual_path,
    upload_workspace_file,
)


def _fake_runtime():
    """构造带 runtime scope 的假 ToolRuntime。"""
    scope = {
        "runtime_scope_id": "run-123",
        "uid": "u-abc",
        "workdir_relative_path": "/projects/2026-01-01_00-00-00_demo",
    }
    return SimpleNamespace(config={"configurable": scope}, context=None, state=None)


WORKDIR = "/projects/2026-01-01_00-00-00_demo"


def test_resolve_rejects_empty_path():
    with pytest.raises(ValueError):
        resolve_virtual_path("", _fake_runtime())


def test_resolve_rejects_parent_escape():
    for bad in ("../etc/passwd", "outputs/../../x.txt", "/a/../b"):
        with pytest.raises(ValueError):
            resolve_virtual_path(bad, _fake_runtime())


def test_resolve_normalizes_relative_and_absolute():
    runtime = _fake_runtime()
    assert resolve_virtual_path("uploads/a.txt", runtime) == f"{WORKDIR}/uploads/a.txt"
    assert resolve_virtual_path("/uploads/a.txt", runtime) == f"{WORKDIR}/uploads/a.txt"
    # 已带 workdir 前缀的绝对虚拟路径原样接受
    assert resolve_virtual_path(f"{WORKDIR}/uploads/a.txt", runtime) == f"{WORKDIR}/uploads/a.txt"


def test_resolve_rejects_paths_outside_workdir():
    with pytest.raises(ValueError):
        resolve_virtual_path("/home/gem/other/x.txt", _fake_runtime())


def test_download_missing_scope_raises(monkeypatch):
    runtime = SimpleNamespace(config={"configurable": {}}, context=None, state=None)
    with pytest.raises(ValueError, match="运行时"):
        download_workspace_file("a.txt", runtime, max_bytes=100)


def test_upload_then_download_roundtrip(monkeypatch, tmp_path):
    """用假 backend 验证 upload 写入与路径回传。"""
    from yuxi.agents.toolkits.buildin import _workspace_io

    stored = {}

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def upload_authorized_file_from_path(self, virtual_path, local_path):
            stored[virtual_path] = open(local_path, "rb").read()

        def download_authorized_file_to_path(self, virtual_path, local_path, max_bytes):
            if virtual_path not in stored:
                raise ValueError(f"文件不存在或不是普通文件: {virtual_path}")
            with open(local_path, "wb") as fh:
                fh.write(stored[virtual_path])

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)
    runtime = _fake_runtime()

    display = upload_workspace_file(b"hello", "outputs/r.txt", runtime)
    assert display == "outputs/r.txt"
    assert stored[f"{WORKDIR}/outputs/r.txt"] == b"hello"

    data, name = download_workspace_file("outputs/r.txt", runtime, max_bytes=100)
    assert data == b"hello"
    assert name == "r.txt"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_workspace_io.py -v`
Expected: FAIL（`ModuleNotFoundError: yuxi.agents.toolkits.buildin._workspace_io`）

- [ ] **Step 3: 实现 `_workspace_io.py`**

`backend/package/yuxi/agents/toolkits/buildin/_workspace_io.py` 全文：

```python
"""插件类工具的工作区文件读写：统一虚拟路径校验与沙盒 backend 读写。

路径口径与 toolkits/buildin/tools.py 的 ocr_parse_file 保持一致：
输入接受工作区相对路径或以当前 workdir 开头的绝对虚拟路径，拒绝 .. 越界。
"""

import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import PurePosixPath

from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend


def _runtime_scope_value(runtime: ToolRuntime, key: str) -> str | None:
    """从 LangGraph config/context/state 中查找 runtime scope 值。"""
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    sources = (
        configurable if isinstance(configurable, dict) else {},
        getattr(runtime, "context", None),
        getattr(runtime, "state", None) if isinstance(getattr(runtime, "state", None), dict) else {},
    )
    for source in sources:
        value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_runtime_scope(runtime: ToolRuntime) -> tuple[str, str, str]:
    """读取 execution runtime、用户与 Workdir 路径。"""
    runtime_thread_id = _runtime_scope_value(runtime, "runtime_scope_id") or _runtime_scope_value(
        runtime, "thread_id"
    )
    uid = _runtime_scope_value(runtime, "uid")
    workdir_path = _runtime_scope_value(runtime, "workdir_relative_path")
    if not runtime_thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    if not uid:
        raise ValueError("当前运行时缺少 uid")
    if not workdir_path:
        raise ValueError("当前运行时缺少 workdir_relative_path")
    return runtime_thread_id, uid, workdir_path.rstrip("/")


def resolve_virtual_path(relative_path: str, runtime: ToolRuntime) -> str:
    """把工具入参路径解析为当前 Run 工作区内的虚拟绝对路径。

    Args:
        relative_path: 工作区相对路径（如 uploads/a.txt），或以当前 workdir
            开头的绝对虚拟路径；两种写法都不允许包含 .. 段。
        runtime: LangGraph 工具运行时。

    Returns:
        归一化后的虚拟绝对路径（以 workdir 开头）。

    Raises:
        ValueError: 路径为空、包含 .. 段或落在当前 workdir 之外时抛出。
    """
    _, _, workdir_path = _resolve_runtime_scope(runtime)
    normalized = str(relative_path or "").strip()
    if not normalized:
        raise ValueError("文件路径不能为空")
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError("文件路径不允许包含 ..")
    if normalized.startswith(f"{workdir_path}/"):
        return normalized
    candidate = f"{workdir_path}/{normalized.lstrip('/')}"
    if not candidate.startswith(f"{workdir_path}/"):
        raise ValueError("文件路径必须位于当前工作区内")
    return candidate


def _make_backend(runtime: ToolRuntime, runtime_scope_id: str, uid: str, workdir_path: str):
    """按当前 runtime scope 构造沙盒 backend。"""
    return ProvisionerSandboxBackend(
        thread_id=runtime_scope_id,
        uid=uid,
        workdir_path=workdir_path,
        create_if_missing=True,
    )


async def download_workspace_file(
    relative_path: str, runtime: ToolRuntime, *, max_bytes: int
) -> tuple[bytes, str]:
    """从当前 Run 工作区读取文件字节。

    Args:
        relative_path: 工作区相对路径或 workdir 内的绝对虚拟路径。
        runtime: LangGraph 工具运行时。
        max_bytes: 文件大小上限（字节）。

    Returns:
        (文件字节, 文件名) 二元组。

    Raises:
        ValueError: 路径非法、超出大小上限或文件不存在时抛出。
    """
    runtime_scope_id, uid, workdir_path = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime, runtime_scope_id, uid, workdir_path)
    suffix = PurePosixPath(virtual_path).suffix
    with tempfile.NamedTemporaryFile(
        prefix="yuxi-plugin-source-", suffix=suffix, delete=False
    ) as temp_file:
        temp_path = temp_file.name
    try:
        await asyncio.to_thread(
            backend.download_authorized_file_to_path, virtual_path, temp_path, max_bytes
        )
        with open(temp_path, "rb") as fh:
            return fh.read(), PurePosixPath(virtual_path).name
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)


async def upload_workspace_file(content: bytes, relative_path: str, runtime: ToolRuntime) -> str:
    """把字节写入当前 Run 工作区（父/子智能体共享同一 Workdir）。

    Args:
        content: 文件字节。
        relative_path: 目标工作区相对路径（如 outputs/result.json）。
        runtime: LangGraph 工具运行时。

    Returns:
        写入后的工作区相对显示路径（如 outputs/result.json）。

    Raises:
        ValueError: 路径非法时抛出。
    """
    runtime_scope_id, uid, workdir_path = _resolve_runtime_scope(runtime)
    virtual_path = resolve_virtual_path(relative_path, runtime)
    backend = _make_backend(runtime, runtime_scope_id, uid, workdir_path)
    with tempfile.NamedTemporaryFile(
        prefix="yuxi-plugin-output-", suffix=PurePosixPath(virtual_path).suffix, delete=False
    ) as temp_file:
        temp_file.write(content)
        temp_path = temp_file.name
    try:
        await asyncio.to_thread(backend.upload_authorized_file_from_path, virtual_path, temp_path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
    return virtual_path[len(workdir_path) + 1:]
```

注：`_runtime_scope_value` 与 `tools.py` 中同名私有函数重复，这是有意的——`tools.py` 的版本是模块私有且带 OCR 专属逻辑，避免跨模块 import 私有名。若执行时发现可直接复用公共形态，保持本文件自包含优先。

- [ ] **Step 4: 跑 `_workspace_io` 测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_workspace_io.py -v`
Expected: PASS（7 项全绿）

- [ ] **Step 5: 写 Spec_Agent NMR 三件套的失败测试**

`backend/test/unit/agents/toolkits/buildin/test_spec_agent_tools.py` 全文：

```python
"""Spec_Agent 核磁三件套工具的单元测试（MockTransport，不依赖真实上游）。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import spec_agent


def _set_env(monkeypatch, base_url="http://spec.test", token="tok-1"):
    monkeypatch.setenv("SPEC_AGENT_BASE_URL", base_url)
    if token is None:
        monkeypatch.delenv("SPEC_AGENT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SPEC_AGENT_TOKEN", token)


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(spec_agent, "_transport", transport)


@pytest.mark.asyncio
async def test_unconfigured_returns_error_dict(monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_BASE_URL", raising=False)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is False
    assert result["error"] == "spec_agent_unconfigured"
    assert "SPEC_AGENT_BASE_URL" in result["message"]


@pytest.mark.asyncio
async def test_forward_success(monkeypatch):
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/nmrserver/forward"
        assert request.headers["Authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={
            "code": 0, "message": "ok",
            "data": {"items": [{"smiles": "CCO", "c_shifts": [18.2]}]},
        })

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert "CCO" in result["content"]


@pytest.mark.asyncio
async def test_unauthorized_is_distinct_error(monkeypatch):
    _set_env(monkeypatch)
    _install_transport(monkeypatch, lambda request: httpx.Response(401, json={"detail": "no"}))
    result = await spec_agent.spec_nmr_search.ainvoke({"c_shifts_input": "18.2"})
    assert result["error"] == "unauthorized"
    assert "SPEC_AGENT_TOKEN" in result["message"]


@pytest.mark.asyncio
async def test_upstream_error_code(monkeypatch):
    _set_env(monkeypatch)
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"code": 500, "message": "模型内部错误"}),
    )
    result = await spec_agent.spec_nmr_reverse.ainvoke({"formula": "C2H6O"})
    assert result["error"] == "upstream_error"
    assert "模型内部错误" in result["message"]


@pytest.mark.asyncio
async def test_empty_token_omits_auth_header(monkeypatch):
    _set_env(monkeypatch, token=None)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    _install_transport(monkeypatch, handler)
    result = await spec_agent.spec_nmr_forward.ainvoke({"smiles_input": "CCO"})
    assert result["ok"] is True
    assert "Authorization" not in (seen["auth"] or "")
```

- [ ] **Step 6: 实现 `spec_agent.py`（NMR 部分）并在 `buildin/__init__.py` 注册**

`backend/package/yuxi/agents/toolkits/buildin/spec_agent.py` 本任务先交付 NMR 三件套（Task 2 再追加五类谱图任务工具）。全文：

```python
"""Spec_Agent 谱图解析内置工具。

来源：Synlora 插件 catalog/plugins/spec_agent（tools.py 同步三件套平移；
connectors.py 的五类异步任务在 Task 2 改造为同步内嵌轮询工具）。
认证只用静态 SPEC_AGENT_TOKEN（不迁移 ai4ms_token 代签机制）。
"""

import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool

CONNECT_TIMEOUT_S = 10.0
NMR_REQUEST_TIMEOUT_S = 360.0

# 测试注入 MockTransport 的钩子；None 时 httpx 使用默认传输
_transport: httpx.BaseTransport | None = None


def _settings() -> dict[str, str]:
    """读取 Spec_Agent 环境变量配置。"""
    return {
        "base_url": (os.getenv("SPEC_AGENT_BASE_URL") or "").strip().rstrip("/"),
        "token": (os.getenv("SPEC_AGENT_TOKEN") or "").strip(),
    }


def _error(error: str, message: str) -> dict:
    """构造上游类错误的结构化返回（不抛异常，供模型阅读后自行决策）。"""
    return {"ok": False, "error": error, "message": message}


def _make_client(**kwargs) -> httpx.AsyncClient:
    """构造带统一超时的 httpx 客户端。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(kwargs.pop("timeout", NMR_REQUEST_TIMEOUT_S), connect=CONNECT_TIMEOUT_S),
        transport=_transport,
        **kwargs,
    )


def _headers(settings: dict[str, str]) -> dict[str, str]:
    """构造请求头；未配置 token 时不发送认证头。"""
    return {"Authorization": f"Bearer {settings['token']}"} if settings["token"] else {}


async def _call_nmrserver(path: str, payload: dict) -> dict:
    """调用 nmrserver 同步接口并做错误归一化。

    Args:
        path: 接口名（forward / reverse / search）。
        payload: 请求 JSON 体。

    Returns:
        成功为 {"ok": True, "count": N, "content": "逐条 JSON 行"}；
        失败为 {"ok": False, "error": 错误码, "message": 描述}。
    """
    import json

    settings = _settings()
    if not settings["base_url"]:
        return _error(
            "spec_agent_unconfigured",
            "Spec_Agent 未配置服务地址：请在部署配置中设置环境变量 SPEC_AGENT_BASE_URL",
        )
    try:
        async with _make_client() as client:
            resp = await client.post(
                f"{settings['base_url']}/api/v1/nmrserver/{path}",
                headers=_headers(settings),
                json=payload,
            )
    except httpx.TimeoutException as exc:
        return _error("timeout", f"谱图服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接谱图服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"谱图服务请求失败: {exc}")
    if resp.status_code in (401, 403):
        return _error(
            "unauthorized",
            "访问凭证无效或已过期：请核对部署配置中的 SPEC_AGENT_TOKEN（这不是参数错误，请勿原样重试）",
        )
    if resp.status_code != 200:
        return _error("http_error", f"谱图服务返回 {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json() or {}
    except ValueError:
        return _error("upstream_error", f"谱图服务返回非 JSON: {resp.text[:200]}")
    if body.get("code") != 0:
        return _error("upstream_error", f"谱图服务错误: {body.get('message') or body.get('code')}")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _error("upstream_error", f"谱图服务返回结构异常: {str(data)[:200]}")
    items = data["items"]
    if not items:
        return {"ok": True, "count": 0, "content": "（未返回结果）"}
    return {
        "ok": True,
        "count": len(items),
        "content": "\n".join(json.dumps(item, ensure_ascii=False) for item in items),
    }


class SpecNmrForwardInput(BaseModel):
    smiles_input: str = Field(description="待预测的分子 SMILES，多个分子每行一个")


class SpecNmrReverseInput(BaseModel):
    c_shifts_input: str = Field(default="", description="实测碳谱化学位移，逗号或换行分隔")
    h_shifts_input: str = Field(default="", description="实测氢谱化学位移，逗号或换行分隔")
    h_split_input: str = Field(default="", description="氢谱峰裂分信息（如 3.2, t, 2H 每行一条）")
    formula: str = Field(default="", description="已知分子式（如 C2H6O），可留空")
    allowed_elements: str = Field(default="", description="候选元素集合，逗号分隔，可留空")
    candidates: str = Field(default="", description="候选 SMILES 列表约束，可留空")


class SpecNmrSearchInput(BaseModel):
    c_shifts_input: str = Field(default="", description="实测碳谱化学位移，逗号或换行分隔")
    h_shifts_input: str = Field(default="", description="实测氢谱化学位移，逗号或换行分隔")
    h_split_input: str = Field(default="", description="氢谱峰裂分信息，可留空")
    num_search: int = Field(default=500, ge=10, le=10000, description="数据库检索样本数，默认 500")
    topk: int = Field(default=10, ge=1, le=100, description="返回候选数，默认 10")
    allowed_elements: str = Field(default="C,H,N,O", description="候选元素集合，默认 C,H,N,O")


SPEC_NMR_FORWARD_DESCRIPTION = """核磁正向预测：给定分子 SMILES（可多行）预测其 13C/1H 化学位移。
上游为模型推理，单次可能需要数分钟，调用后请耐心等待，不要重复提交相同请求。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁正向预测",
    description=SPEC_NMR_FORWARD_DESCRIPTION,
    args_schema=SpecNmrForwardInput,
)
async def spec_nmr_forward(smiles_input: str) -> dict:
    """给定分子 SMILES 预测 13C/1H 化学位移（正向预测）。"""
    return await _call_nmrserver("forward", {"smiles_input": smiles_input})


SPEC_NMR_REVERSE_DESCRIPTION = """核磁反向预测：给定实测化学位移（碳谱/氢谱/裂分）反推候选分子结构。
可附分子式、元素约束或候选列表缩小范围。上游为模型推理，单次可能需要数分钟。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁反向预测",
    description=SPEC_NMR_REVERSE_DESCRIPTION,
    args_schema=SpecNmrReverseInput,
)
async def spec_nmr_reverse(
    c_shifts_input: str = "",
    h_shifts_input: str = "",
    h_split_input: str = "",
    formula: str = "",
    allowed_elements: str = "",
    candidates: str = "",
) -> dict:
    """给定实测化学位移反推候选分子结构（反向预测）。"""
    return await _call_nmrserver(
        "reverse",
        {
            "c_shifts_input": c_shifts_input,
            "h_shifts_input": h_shifts_input,
            "h_split_input": h_split_input,
            "formula": formula,
            "allowed_elements": allowed_elements,
            "candidates": candidates,
        },
    )


SPEC_NMR_SEARCH_DESCRIPTION = """核磁数据库检索：给定实测化学位移在数据库中检索匹配分子。
返回按匹配度排序的候选列表。检索样本数与候选数可调。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁数据库检索",
    description=SPEC_NMR_SEARCH_DESCRIPTION,
    args_schema=SpecNmrSearchInput,
)
async def spec_nmr_search(
    c_shifts_input: str = "",
    h_shifts_input: str = "",
    h_split_input: str = "",
    num_search: int = 500,
    topk: int = 10,
    allowed_elements: str = "C,H,N,O",
) -> dict:
    """给定实测化学位移在核磁数据库中检索匹配分子。"""
    return await _call_nmrserver(
        "search",
        {
            "c_shifts_input": c_shifts_input,
            "h_shifts_input": h_shifts_input,
            "h_split_input": h_split_input,
            "num_search": str(num_search),
            "topk": str(topk),
            "allowed_elements": allowed_elements,
        },
    )
```

Modify `backend/package/yuxi/agents/toolkits/buildin/__init__.py`，追加：

```python
from .spec_agent import spec_nmr_forward, spec_nmr_reverse, spec_nmr_search

__all__ = [
    # ...既有条目保持不变...
    "spec_nmr_forward",
    "spec_nmr_reverse",
    "spec_nmr_search",
]
```

- [ ] **Step 7: 跑本任务测试 + category 防泄漏断言**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_spec_agent_tools.py -v`

再补一个即时验证（不落测试文件，临时命令行断言 category 隔离，对应 Review Focus #1）：

Run: `cd E:\agent_projects\Yuxi\backend && uv run python -c "from yuxi.agents.toolkits.service import get_tool_instances_by_category; names = [t.name for t in get_tool_instances_by_category('buildin')]; assert not any(n.startswith(('spec_','poly_','sciverse_')) for n in names), names; print('category isolation OK')"`

Expected: 测试 PASS + 输出 `category isolation OK`

- [ ] **Step 8: Commit**

```bash
git add backend/package/yuxi/agents/toolkits/buildin/_workspace_io.py backend/package/yuxi/agents/toolkits/buildin/spec_agent.py backend/package/yuxi/agents/toolkits/buildin/__init__.py backend/test/unit/agents/toolkits/buildin/test_workspace_io.py backend/test/unit/agents/toolkits/buildin/test_spec_agent_tools.py
git commit -m "新增工作区 IO 公共模块与 Spec_Agent 核磁三件套工具

- _workspace_io：虚拟路径校验 + 沙盒读写（拒绝 .. 越界）
- spec_nmr_forward/reverse/search：同步调用 nmrserver，错误归一化
- 工具 category 用 spec_agent，不进默认 all 工具集"
```

---

### Task 2: Spec_Agent 五类谱图文件解析（同步内嵌轮询）

**Files:**
- Modify: `backend/package/yuxi/agents/toolkits/buildin/spec_agent.py`（追加五类任务实现）
- Modify: `backend/package/yuxi/agents/toolkits/buildin/__init__.py`（追加 import）
- Test: `backend/test/unit/agents/toolkits/buildin/test_spec_agent_tasks.py`

**Interfaces:**
- Consumes: Task 1 的 `_settings` / `_error` / `_make_client` / `_headers` / `_transport`、`download_workspace_file`。
- Produces: 工具 `spec_task_nmr` / `spec_task_ir` / `spec_task_gpc` / `spec_task_raman` / `spec_task_lcms`（`category="spec_agent"`），统一入参 `(path: str, params: dict | None)`，返回 `{"ok": True, "task_id", "status", "result"}` 或错误 dict；内部函数 `_run_spectra_task(endpoint, default_mode, path, params, runtime) -> dict`；`_task_max_wait_s() -> float`。

- [ ] **Step 1: 写失败测试**

`backend/test/unit/agents/toolkits/buildin/test_spec_agent_tasks.py` 全文：

```python
"""Spec_Agent 五类谱图同步任务工具的单元测试（状态机/超时/失败原因/路径安全）。"""

from types import SimpleNamespace

import httpx
import pytest

from yuxi.agents.toolkits.buildin import spec_agent


WORKDIR = "/projects/2026-01-01_00-00-00_demo"


def _runtime():
    scope = {"runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}
    return SimpleNamespace(config={"configurable": scope}, context=None, state=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SPEC_AGENT_BASE_URL", "http://spec.test")
    monkeypatch.delenv("SPEC_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("SPEC_TASK_MAX_WAIT_SECONDS", raising=False)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(spec_agent.asyncio, "sleep", _instant_sleep)


def _install(monkeypatch, handler):
    monkeypatch.setattr(spec_agent, "_transport", httpx.MockTransport(handler))


def _install_file(monkeypatch, content=b"FAKE-SPECTRUM"):
    from yuxi.agents.toolkits.buildin import _workspace_io

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def download_authorized_file_to_path(self, virtual_path, local_path, max_bytes):
            with open(local_path, "wb") as fh:
                fh.write(content)

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)


def _stateful_handler():
    """上传→提交→(pending→running→success)→取结果 的完整状态机。"""
    state = {"uploaded": 0, "status": "pending", "result": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files/upload":
            state["uploaded"] += 1
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "f-1"}})
        if path.startswith("/api/v1/tasks/") and path.endswith("/result"):
            if state["status"] == "failed":
                return httpx.Response(200, json={
                    "code": 0,
                    "data": {"result": None, "error": {
                        "error_code": "E1", "error_message": "解析失败", "error_detail": "峰数不足"}},
                })
            return httpx.Response(200, json={
                "code": 0, "data": {"result": {"peaks": [1, 2, 3]}, "error": None}})
        if request.method == "POST" and path.startswith("/api/v1/tasks/"):
            assert "file_id" in (request.read() and "{}")  # 触发读取避免流警告
            state["status"] = "pending"
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-9"}})
        # GET /api/v1/tasks/{id}
        if state["status"] == "pending":
            state["status"] = "running"
        elif state["status"] == "running":
            state["status"] = state.get("final", "success")
        return httpx.Response(200, json={"code": 0, "data": {"status": state["status"]}})

    return handler, state


@pytest.mark.asyncio
async def test_full_success_flow(monkeypatch):
    handler, state = _stateful_handler()
    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    result = await spec_agent.spec_task_nmr.ainvoke(
        {"path": "uploads/nmr.txt", "params": {"nucleus": "13C"}}, config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}}
    )
    assert result["ok"] is True
    assert result["task_id"] == "t-9"
    assert result["result"] == {"peaks": [1, 2, 3]}
    assert state["uploaded"] == 1


@pytest.mark.asyncio
async def test_failed_task_returns_upstream_reason(monkeypatch):
    handler, state = _stateful_handler()
    state["final"] = "failed"
    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    result = await spec_agent.spec_task_ir.ainvoke(
        {"path": "uploads/ir.csv"}, config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}}
    )
    assert result["ok"] is False
    assert result["error"] == "upstream_failed"
    assert "解析失败" in result["message"] and "峰数不足" in result["message"]


@pytest.mark.asyncio
async def test_timeout_budget_clamped(monkeypatch):
    assert spec_agent._task_max_wait_s() == 1500.0
    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "999999")
    assert spec_agent._task_max_wait_s() == 3300.0
    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "1")
    assert spec_agent._task_max_wait_s() == 60.0


@pytest.mark.asyncio
async def test_timeout_returns_task_id(monkeypatch):
    handler, _ = _stateful_handler()
    # 让状态永远停在 running
    def stuck(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"status": "RUNNING"}})

    monkeypatch.setenv("SPEC_TASK_MAX_WAIT_SECONDS", "60")
    _install(monkeypatch, stuck)
    _install_file(monkeypatch)
    # 让 sleep 推进单调时钟不可行，直接把 deadline 置于过去
    monkeypatch.setattr(spec_agent.time, "monotonic", lambda: 1e12)
    result = await spec_agent.spec_task_lcms.ainvoke(
        {"path": "uploads/lcms.dat"}, config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}}
    )
    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert "t-" in result["message"] or "task_id" in result


@pytest.mark.asyncio
async def test_raman_default_mode_applied(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files/upload":
            return httpx.Response(200, json={"code": 0, "data": {"file_id": "f-1"}})
        if request.method == "POST":
            captured["body"] = request.read().decode()
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-1"}})
        if path.endswith("/result"):
            return httpx.Response(200, json={"code": 0, "data": {"result": {}, "error": None}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "success"}})

    _install(monkeypatch, handler)
    _install_file(monkeypatch)
    await spec_agent.spec_task_raman.ainvoke(
        {"path": "uploads/r.txt"}, config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}}
    )
    assert '"mode": "function_groups"' in captured["body"] or '"mode":"function_groups"' in captured["body"]

    captured.clear()
    await spec_agent.spec_task_raman.ainvoke(
        {"path": "uploads/r.txt", "params": {"mode": "greedy_decode"}},
        config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}},
    )
    assert "greedy_decode" in captured["body"]


@pytest.mark.asyncio
async def test_path_escape_rejected(monkeypatch):
    from yuxi.agents.toolkits.buildin._workspace_io import resolve_virtual_path

    with pytest.raises(ValueError):
        await spec_agent._run_spectra_task(
            "nmr", None, "../escape.txt", None, _runtime())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_spec_agent_tasks.py -v`
Expected: FAIL（`spec_task_nmr` 不存在 / `spec_agent._run_spectra_task` 不存在）

- [ ] **Step 3: 实现五类任务工具（追加到 `spec_agent.py`）**

在 `spec_agent.py` 追加（import 区补 `import asyncio`、`import json`、`import time`、`from langgraph.prebuilt.tool_node import ToolRuntime`、`from ._workspace_io import download_workspace_file`）：

```python
TASK_QUICK_TIMEOUT_S = 60.0    # 上传/提交/查状态/取结果都是秒级操作
TASK_UPLOAD_TIMEOUT_S = 120.0  # 上传单独放宽
TASK_POLL_INTERVAL_S = 5.0
TASK_MAX_WAIT_S_DEFAULT = 1500.0  # 轮询总预算；必须显著小于 worker job_timeout(3600s)
TASK_MAX_WAIT_S_CEILING = 3300.0
TASK_MAX_WAIT_S_FLOOR = 60.0
TASK_FILE_MAX_BYTES = 100 * 1024 * 1024
RESULT_RENDER_LIMIT = 30000  # 回给模型的结果字符预算


def _task_max_wait_s() -> float:
    """读取并钳制谱图任务轮询总预算（秒）。"""
    raw = (os.getenv("SPEC_TASK_MAX_WAIT_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return TASK_MAX_WAIT_S_DEFAULT
    return min(max(value, TASK_MAX_WAIT_S_FLOOR), TASK_MAX_WAIT_S_CEILING)


def _unwrap(resp: httpx.Response, action: str) -> tuple[dict | None, dict | None]:
    """解析上游 {code, message, data} 包络。

    Args:
        resp: 上游响应。
        action: 动作名（用于错误消息，如“上传谱图文件”）。

    Returns:
        (错误 dict 或 None, data dict 或 None)。
    """
    if resp.status_code in (401, 403):
        return _error(
            "unauthorized",
            "访问凭证无效或已过期：请核对 SPEC_AGENT_TOKEN（这不是参数错误，请勿原样重试）",
        ), None
    if resp.status_code != 200:
        return _error("http_error", f"{action}失败：上游返回 {resp.status_code} {resp.text[:200]}"), None
    try:
        body = resp.json() or {}
    except ValueError:
        return _error("upstream_error", f"{action}失败：上游返回非 JSON: {resp.text[:200]}"), None
    if body.get("code") != 0:
        return _error("upstream_error", f"{action}失败: {body.get('message') or body.get('code')}"), None
    data = body.get("data")
    if not isinstance(data, dict):
        return _error("upstream_error", f"{action}失败：上游返回结构异常: {str(data)[:200]}"), None
    return None, data


def _render_result(result: Any) -> Any:
    """超大结果截断，保护模型上下文。"""
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= RESULT_RENDER_LIMIT:
        return result
    return {
        "truncated": True,
        "preview": text[:RESULT_RENDER_LIMIT],
        "hint": f"结果共 {len(text)} 字符已截断；完整结果请在服务端按 task_id 取回",
    }


def _error_text(error: Any) -> str:
    """把上游 error 字段拼成可读文本。"""
    if isinstance(error, dict):
        code = error.get("error_code")
        message = error.get("error_message")
        detail = error.get("error_detail")
        parts = f"[{code}] {message}：{detail}" if code else f"{message}：{detail}"
        return parts if message or detail else str(error)
    return str(error or "未知原因")


async def _run_spectra_task(
    endpoint: str, default_mode: str | None, path: str, params: dict | None, runtime: ToolRuntime
) -> dict:
    """上传谱图文件→提交解析任务→内嵌轮询至终态→取结果（同步语义）。

    Args:
        endpoint: 上游任务类型（nmr/ir/gpc/raman/lcms）。
        default_mode: 缺省 mode 兜底值（仅 raman 用 function_groups）。
        path: 谱图文件的工作区相对路径。
        params: 任务参数（可选，自由字典，字段见 spec-spectra 技能参数表）。
        runtime: LangGraph 工具运行时。

    Returns:
        成功为 {"ok": True, "task_id", "status", "result"}；失败为错误 dict。
    """
    settings = _settings()
    if not settings["base_url"]:
        return _error(
            "spec_agent_unconfigured",
            "Spec_Agent 未配置服务地址：请在部署配置中设置环境变量 SPEC_AGENT_BASE_URL",
        )
    try:
        file_bytes, file_name = await download_workspace_file(
            path, runtime, max_bytes=TASK_FILE_MAX_BYTES
        )
    except ValueError:
        raise  # 路径类问题按参数错误抛出，由工具框架兜底
    headers = _headers(settings)
    upstream_params = dict(params or {})
    if default_mode and not upstream_params.get("mode"):
        upstream_params["mode"] = default_mode
    base = settings["base_url"]
    max_wait = _task_max_wait_s()
    deadline = time.monotonic() + max_wait
    async with _make_client(timeout=TASK_QUICK_TIMEOUT_S) as client:
        try:
            upload = await client.post(
                f"{base}/api/v1/files/upload",
                headers=headers,
                files={"file": (file_name, file_bytes, "application/octet-stream")},
                timeout=TASK_UPLOAD_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            return _error("http_error", f"上传谱图文件失败: {exc}")
        err, data = _unwrap(upload, "上传谱图文件")
        if err:
            return err
        file_id = data.get("file_id")
        if not file_id:
            return _error("upstream_error", f"上传谱图文件失败：上游未返回 file_id: {str(data)[:200]}")
        try:
            submit = await client.post(
                f"{base}/api/v1/tasks/{endpoint}",
                headers=headers,
                json={"input": {"input_type": "file_id", "file_id": file_id},
                      "params": upstream_params},
            )
        except httpx.HTTPError as exc:
            return _error("http_error", f"提交解析任务失败: {exc}")
        err, data = _unwrap(submit, "提交解析任务")
        if err:
            return err
        task_id = data.get("task_id")
        if not task_id:
            return _error("upstream_error", f"提交解析任务失败：上游未返回 task_id: {str(data)[:200]}")
        # 内嵌轮询：pending/queued/running → 终态
        while True:
            try:
                status_resp = await client.get(f"{base}/api/v1/tasks/{task_id}", headers=headers)
            except httpx.HTTPError as exc:
                return _error("http_error", f"查询任务状态失败（task_id={task_id}）: {exc}")
            err, data = _unwrap(status_resp, "查询任务状态")
            if err:
                return err
            status = str(data.get("status") or "").strip().lower()
            if status in ("success", "completed"):
                break
            if status in ("failed", "canceled", "cancelled"):
                try:
                    result_resp = await client.get(
                        f"{base}/api/v1/tasks/{task_id}/result", headers=headers)
                    err, data = _unwrap(result_resp, "获取任务失败原因")
                    reason = _error_text((data or {}).get("error")) if not err else err["message"]
                except httpx.HTTPError:
                    reason = "上游未返回失败原因"
                return _error("upstream_failed", f"任务失败（task_id={task_id}）：{reason}")
            if time.monotonic() > deadline:
                return _error(
                    "timeout",
                    f"任务 {task_id} 超过 {int(max_wait)} 秒仍在 {status} 状态，本次调用放弃等待；"
                    f"上游任务仍在运行，再次调用会重新提交新任务，请酌情调整参数后重试",
                )
            await asyncio.sleep(TASK_POLL_INTERVAL_S)
        try:
            result_resp = await client.get(f"{base}/api/v1/tasks/{task_id}/result", headers=headers)
        except httpx.HTTPError as exc:
            return _error("http_error", f"获取任务结果失败（task_id={task_id}）: {exc}")
        err, data = _unwrap(result_resp, "获取任务结果")
        if err:
            return err
        return {
            "ok": True,
            "task_id": task_id,
            "status": "success",
            "result": _render_result(data.get("result")),
        }
```

五个工具薄包装（`runtime` 参数由 LangGraph 注入，不进 args_schema）：

```python
class SpecTaskInput(BaseModel):
    path: str = Field(description="谱图文件的工作区相对路径（如 uploads/nmr.txt）")
    params: dict | None = Field(
        default=None,
        description="任务参数字典（可选）。可用字段与默认值见 spec-spectra 技能的参数表；"
                    "不认识的字段不要传，自造字段会被上游静默忽略",
    )


def _spectra_tool(endpoint: str, display_name: str, description: str, default_mode: str | None = None):
    """工厂：生成五类谱图任务的同构工具。"""
    @tool(
        category="spec_agent",
        tags=["谱图", "文件解析"],
        display_name=display_name,
        description=description,
        args_schema=SpecTaskInput,
    )
    async def spectra_task(path: str, params: dict | None = None, runtime: ToolRuntime = None) -> dict:
        return await _run_spectra_task(endpoint, default_mode, path, params, runtime)
    return spectra_task


SPEC_TASK_NMR_DESCRIPTION = """解析核磁（NMR）谱图文件：峰识别、积分、multiplet 检测。
常用参数：nucleus（1H/13C）、threshold、integration_method 等，完整参数表见 spec-spectra 技能。
同步等待结果，通常数十秒到数分钟。"""

SPEC_TASK_IR_DESCRIPTION = """解析红外（IR）光谱文件。
常用参数：mode（greedy_decode/beam_search/retrieval 等 4 种）、k、x0/x1 波数区间、transmittance。
同步等待结果；完整参数表见 spec-spectra 技能。"""

SPEC_TASK_GPC_DESCRIPTION = """解析凝胶渗透色谱（GPC）谱图文件：分子量分布。
常用参数：detect_mode（auto/manual）、manual_interval（manual 时必填 [start, end]）。
同步等待结果；完整参数表见 spec-spectra 技能。"""

SPEC_TASK_RAMAN_DESCRIPTION = """解析拉曼（Raman）光谱文件。
默认 mode=function_groups（官能团识别）；greedy_decode/beam_search 当前部署不可用，除非明确要求不要传。
其余参数与 IR 一致；完整参数表见 spec-spectra 技能。同步等待结果。"""

SPEC_TASK_LCMS_DESCRIPTION = """解析液质（LC-MS）数据文件，基本无可调参数。
同步等待结果。"""


spec_task_nmr = _spectra_tool("nmr", "核磁谱图文件解析", SPEC_TASK_NMR_DESCRIPTION)
spec_task_ir = _spectra_tool("ir", "红外光谱文件解析", SPEC_TASK_IR_DESCRIPTION)
spec_task_gpc = _spectra_tool("gpc", "GPC 谱图文件解析", SPEC_TASK_GPC_DESCRIPTION)
spec_task_raman = _spectra_tool("raman", "拉曼光谱文件解析", SPEC_TASK_RAMAN_DESCRIPTION, default_mode="function_groups")
spec_task_lcms = _spectra_tool("lcms", "液质数据文件解析", SPEC_TASK_LCMS_DESCRIPTION)
```

> 注：工厂内闭包的 `spectra_task.__name__` 会是统一的 `spectra_task`——`@tool` 装饰器用 `langchain_tool` 默认按 `__name__` 命名会重名！因此工厂里必须在装饰器上显式 `name_or_callable=工具名`。修正形态：给 `@tool(...)` 增加实参 `name_or_callable=f"spec_task_{endpoint}"`（`registry.tool` 支持该参数透传给 `langchain_tool`）。五个工具名分别为 `spec_task_nmr/ir/gpc/raman/lcms`。执行时按此修正，测试里 `spec_agent.spec_task_nmr` 的断言不变。

Modify `buildin/__init__.py` 追加：

```python
from .spec_agent import spec_task_gpc, spec_task_ir, spec_task_lcms, spec_task_nmr, spec_task_raman
```

并把五个名字加进 `__all__`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_spec_agent_tasks.py -v`
Expected: PASS（6 项全绿；若工厂命名问题导致重名报错，按 Step 3 注释修正后重跑）

- [ ] **Step 5: Commit**

```bash
git add backend/package/yuxi/agents/toolkits/buildin/spec_agent.py backend/package/yuxi/agents/toolkits/buildin/__init__.py backend/test/unit/agents/toolkits/buildin/test_spec_agent_tasks.py
git commit -m "Spec_Agent 五类谱图文件解析改为同步工具

- 上传 file_id→提交→内嵌轮询至终态→返回结果，总预算默认 1500s 可配
- 失败原因从 result 接口 error 字段取回；超时返回 task_id 说明去向
- Raman 缺省 mode 兜底 function_groups，显式传值优先"
```

---

### Task 3: Poly_Agent 五工具（登录缓存 + 401 重登 + 轮询兜底）

**Files:**
- Create: `backend/package/yuxi/agents/toolkits/buildin/poly_agent.py`
- Modify: `backend/package/yuxi/agents/toolkits/buildin/__init__.py`
- Test: `backend/test/unit/agents/toolkits/buildin/test_poly_agent_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `download_workspace_file`；`registry.tool`。
- Produces: 工具 `poly_tg_predict` / `poly_electrolyte_predict` / `poly_raman_analyze` / `poly_reactivity_fit` / `poly_silicon_bond`（`category="poly_agent"`）；模块级 `_transport`、`_reset_token_cache()`（测试用）。

- [ ] **Step 1: 写失败测试**

`backend/test/unit/agents/toolkits/buildin/test_poly_agent_tools.py` 全文：

```python
"""Poly_Agent 工具的单元测试：登录缓存、401 重登、轮询兜底、未配置。"""

import httpx
import pytest

from yuxi.agents.toolkits.buildin import poly_agent


@pytest.fixture(autouse=True)
def _clean_token_cache():
    poly_agent._reset_token_cache()
    yield
    poly_agent._reset_token_cache()


def _env(monkeypatch, complete=True):
    monkeypatch.setenv("POLY_AGENT_BASE_URL", "http://poly.test" if complete else "")
    monkeypatch.setenv("POLY_AGENT_USERNAME", "u" if complete else "")
    monkeypatch.setenv("POLY_AGENT_PASSWORD", "p" if complete else "")


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "code": 0, "data": {"access_token": "tok-a", "expires_at": 9999999999.0}})


@pytest.mark.asyncio
async def test_unconfigured(monkeypatch):
    _env(monkeypatch, complete=False)
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "CCOc1ccc"})
    assert result["error"] == "poly_agent_unconfigured"
    assert "POLY_AGENT_BASE_URL" in result["message"]


@pytest.mark.asyncio
async def test_login_token_cached(monkeypatch):
    _env(monkeypatch)
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            logins["n"] += 1
            return _login_ok(request)
        if request.url.path.startswith("/api/v1/research-engine/algorithm-runs"):
            assert request.headers["Authorization"] == "Bearer tok-a"
            return httpx.Response(200, json={
                "code": 0,
                "data": {"run_id": "r-1", "status": "completed",
                         "output_summary": {"tg_c": 220.5}},
            })
        raise AssertionError(f"unexpected {request.url.path}")

    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    for _ in range(3):
        result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "CCOc1ccc"})
        assert result["ok"] is True
    assert logins["n"] == 1  # 平台级账号缓存，三次调用只登录一次


@pytest.mark.asyncio
async def test_401_forces_relogin(monkeypatch):
    _env(monkeypatch)
    state = {"logins": 0, "first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            state["logins"] += 1
            return httpx.Response(200, json={
                "code": 0, "data": {"access_token": f"tok-{state['logins']}",
                                     "expires_at": 9999999999.0}})
        if request.url.path.startswith("/api/v1/research-engine/algorithm-runs"):
            if state["first"]:
                state["first"] = False
                return httpx.Response(401, json={"detail": "revoked"})
            return httpx.Response(200, json={
                "code": 0, "data": {"run_id": "r-2", "status": "completed",
                                     "output_summary": {"tg_c": 210.0}}})
        raise AssertionError(f"unexpected {request.url.path}")

    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "X"})
    assert result["ok"] is True
    assert state["logins"] == 2  # 缓存 token 被吊销后强制重登一次并重试


@pytest.mark.asyncio
async def test_async_fallback_polling(monkeypatch):
    """上游若改异步（返回 queued），轮询详情接口直到终态。"""
    _env(monkeypatch)
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return _login_ok(request)
        if request.method == "POST":
            return httpx.Response(200, json={
                "code": 0, "data": {"run_id": "r-3", "status": "queued"}})
        polls["n"] += 1
        status = "running" if polls["n"] < 2 else "completed"
        return httpx.Response(200, json={
            "code": 0, "data": {"run_id": "r-3", "status": status,
                                 "output_summary": {"tg_c": 205.0}}})

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(poly_agent.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(poly_agent, "_transport", httpx.MockTransport(handler))
    result = await poly_agent.poly_tg_predict.ainvoke({"smiles": "X"})
    assert result["ok"] is True
    assert polls["n"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_poly_agent_tools.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `poly_agent.py`**

以 Synlora `catalog/plugins/poly_agent/tools.py`（579 行）为底本平移，差异点：
1. `@tool`（Synlora harness 版）→ Yuxi `registry.tool`，`category="poly_agent"`，参数 schema 从 JSON Schema 转 Pydantic `args_schema`；
2. `ctx.extra["plugins"]["poly_agent"]` 配置读取 → `os.getenv("POLY_AGENT_BASE_URL"/"POLY_AGENT_USERNAME"/"POLY_AGENT_PASSWORD")`；
3. `ctx.workspace_root` + `_safe_path` 文件读取 → `download_workspace_file(path, runtime, max_bytes=10*1024*1024)`；
4. `ToolResult(ok, content, error)` → dict 返回（`{"ok": True, "run_id", "status", "content": output_summary JSON 字符串}` / `_error(...)`）；
5. 登录缓存沿用模块级 dict + `asyncio.Lock`（worker 单进程内存态，与 Synlora 同语义）。

核心代码（完整骨架，错误码全集 `poly_agent_unconfigured/timeout/connection_error/unauthorized/http_error/upstream_error/bad_request/no_workspace` 保持不变）：

```python
"""Poly_Agent 高分子垂类预测内置工具。

来源：Synlora 插件 catalog/plugins/poly_agent/tools.py 平移。
平台级账号自动登录并缓存 token（到期前 5 分钟重登，401 强制重登重试一次）。
"""

import asyncio
import json
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.agents.toolkits.buildin._workspace_io import download_workspace_file
from langgraph.prebuilt.tool_node import ToolRuntime

CONNECT_TIMEOUT_S = 10.0
REQUEST_TIMEOUT_S = 600.0   # 上游同步执行模型推理（实测约 44s），放宽留余量
POLL_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 5.0
UPLOAD_TIMEOUT_S = 120.0
TOKEN_REFRESH_MARGIN_S = 300.0
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 上游限制 10MiB

LOGIN_PATH = "/api/v1/auth/login"
RUNS_PATH = "/api/v1/research-engine/algorithm-runs"

SPECTRUM_MIME = {
    ".txt": "text/plain", ".dat": "text/plain", ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_transport: httpx.BaseTransport | None = None
_token_cache: dict = {"token": "", "expires_at": 0.0}
_token_lock = asyncio.Lock()


def _reset_token_cache() -> None:
    """清空平台级 token 缓存（测试隔离用）。"""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0


def _settings() -> dict[str, str]:
    """读取 Poly_Agent 环境变量配置。"""
    return {
        "base_url": (os.getenv("POLY_AGENT_BASE_URL") or "").strip().rstrip("/"),
        "username": (os.getenv("POLY_AGENT_USERNAME") or "").strip(),
        "password": (os.getenv("POLY_AGENT_PASSWORD") or "").strip(),
    }


def _error(error: str, message: str) -> dict:
    """构造结构化错误返回。"""
    return {"ok": False, "error": error, "message": message}


def _make_client() -> httpx.AsyncClient:
    """构造统一超时的 httpx 客户端。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        transport=_transport,
    )
```

`_login` / `_ensure_token`（双重检查锁）/ `_clear_token` 从 Synlora 原样平移（`_login` 抛 `RuntimeError`，登录路径 200 但 `code != 0` / 缺 `access_token` 同样抛）；`_run_algorithm(ctx 配置改为内部读取)`、`_result_from_run`、`_post_run` 平移为模块私有 async 函数，其中 `file_part` 由 `poly_raman_analyze` 经 `download_workspace_file` 读出后按 `SPECTRUM_MIME` 组装 `("spectrum_file", (文件名, bytes, mime))`。

五个工具的 `args_schema`（字段与 Synlora plugin parameters 一一对应）：

| 工具 | algorithm_id | args_schema 字段（类型/默认值） |
|---|---|---|
| `poly_tg_predict` | `PI_Tg_predictor` | `smiles: str`（必填，单个 SMILES） |
| `poly_electrolyte_predict` | `fluorinated_electrolyte_model_2` | `formulations: list[dict]`（必填，元素字段见技能正文）、`recommendation_limit: int = 5`、`recommendation_task_type: str = ""` |
| `poly_raman_analyze` | `raman_structure_analyzer`（multipart） | `path: str`（必填，工作区相对路径）、`spectype: str = "raman"`、`mode: str = "function_groups"`、`x0: float | None`、`x1: float | None`、`k: int = 3`、`transmittance: bool = False`、`device: str = "cpu"` |
| `poly_reactivity_fit` | `reactivity_ratio_fitter` | `data_rows: list[dict]`（必填，每行含 比例/转化率1/转化率2）、`delta_f_default: float = 0.01` |
| `poly_silicon_bond` | `silicon_bond7_ssl_ensemble_seed0` | `sample: dict`（必填，五条对齐光谱+组成，原样透传） |

每个工具形态（以 tg_predict 为例，其余四个同构）：

```python
class PolyTgPredictInput(BaseModel):
    smiles: str = Field(description="聚酰亚胺单体/结构的 SMILES（单个）")


@tool(
    category="poly_agent",
    tags=["高分子", "预测"],
    display_name="聚酰亚胺 Tg 预测",
    description="预测聚酰亚胺玻璃化转变温度 Tg。输入单个 SMILES。模型推理可能需要数分钟。",
    args_schema=PolyTgPredictInput,
)
async def poly_tg_predict(smiles: str) -> dict:
    """预测聚酰亚胺玻璃化转变温度。"""
    return await _run_algorithm("PI_Tg_predictor", {"smiles": smiles})
```

`poly_raman_analyze` 签名带 `runtime: ToolRuntime`（读谱图文件），`poly_electrolyte_predict` / `poly_reactivity_fit` 的 `formulations` / `data_rows` 若上游要求字符串化数组则按 Synlora 原实现处理（Synlora 传 JSON 数组即可，平移时保持 dict/list 直传）。

`buildin/__init__.py` 追加：

```python
from .poly_agent import (
    poly_electrolyte_predict,
    poly_raman_analyze,
    poly_reactivity_fit,
    poly_silicon_bond,
    poly_tg_predict,
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_poly_agent_tools.py -v`
Expected: PASS（5 项全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/package/yuxi/agents/toolkits/buildin/poly_agent.py backend/package/yuxi/agents/toolkits/buildin/__init__.py backend/test/unit/agents/toolkits/buildin/test_poly_agent_tools.py
git commit -m "新增 Poly_Agent 高分子垂类预测五工具

- 平台账号自动登录与 token 缓存（到期前重登、401 强制重登重试一次）
- 上游改异步时内嵌轮询兜底至终态
- Raman 谱图文件经工作区读取后 multipart 上传"
```

---

### Task 4: Sciverse 五工具

**Files:**
- Create: `backend/package/yuxi/agents/toolkits/buildin/sciverse.py`
- Modify: `backend/package/yuxi/agents/toolkits/buildin/__init__.py`
- Test: `backend/test/unit/agents/toolkits/buildin/test_sciverse_tools.py`

**Interfaces:**
- Consumes: `registry.tool`、Task 1 的 `upload_workspace_file`。
- Produces: 工具 `sciverse_agentic_search` / `sciverse_content` / `sciverse_resource` / `sciverse_meta_catalog` / `sciverse_meta_search`（`category="sciverse"`）；模块级 `_transport`。

- [ ] **Step 1: 写失败测试**

`backend/test/unit/agents/toolkits/buildin/test_sciverse_tools.py`：

```python
"""Sciverse 工具的单元测试：认证、参数校验、渲染截断、附件落盘。"""

from types import SimpleNamespace

import httpx
import pytest

from yuxi.agents.toolkits.buildin import sciverse


WORKDIR = "/projects/2026-01-01_00-00-00_demo"


def _runtime():
    scope = {"runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}
    return SimpleNamespace(config={"configurable": scope}, context=None, state=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SCIVERSE_BASE_URL", "https://sciverse.test")
    monkeypatch.setenv("SCIVERSE_API_TOKEN", "sv-tok")


def _install(monkeypatch, handler):
    monkeypatch.setattr(sciverse, "_transport", httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_unconfigured(monkeypatch):
    monkeypatch.delenv("SCIVERSE_API_TOKEN", raising=False)
    result = await sciverse.sciverse_agentic_search.ainvoke({"query": "polymer"})
    assert result["error"] == "sciverse_unconfigured"


@pytest.mark.asyncio
async def test_agentic_search_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agentic-search"
        assert request.headers["Authorization"] == "Bearer sv-tok"
        return httpx.Response(200, json={"snippets": [{"doc_id": "d1", "text": "hello"}]})

    _install(monkeypatch, handler)
    result = await sciverse.sciverse_agentic_search.ainvoke({"query": "polymer"})
    assert result["ok"] is True
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_meta_search_rejects_query_with_sort(monkeypatch):
    result = await sciverse.sciverse_meta_search.ainvoke(
        {"query": "x", "sort": [{"field": "year", "order": "desc"}]})
    assert result["error"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_not_found(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(404, json={"detail": "gone"}))
    result = await sciverse.sciverse_content.ainvoke({"doc_id": "nope"})
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_render_truncation(monkeypatch):
    big = {"text": "x" * 60000}

    _install(monkeypatch, lambda request: httpx.Response(200, json=big))
    result = await sciverse.sciverse_content.ainvoke({"doc_id": "d1"})
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["content"]) <= sciverse.RENDER_MAX_CHARS + 200


@pytest.mark.asyncio
async def test_resource_saved_to_workspace(monkeypatch):
    from yuxi.agents.toolkits.buildin import _workspace_io

    uploaded = {}

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def upload_authorized_file_from_path(self, virtual_path, local_path):
            uploaded[virtual_path] = open(local_path, "rb").read()

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)
    _install(monkeypatch, lambda request: httpx.Response(
        200, content=b"PDF-BYTES", headers={"content-type": "application/pdf"}))

    result = await sciverse.sciverse_resource.ainvoke(
        {"file_name": "paper.pdf", "save_relative_path": "outputs/paper.pdf"},
        config={"configurable": {
            "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}},
    )
    assert result["ok"] is True
    assert result["saved_path"] == "outputs/paper.pdf"
    assert uploaded[f"{WORKDIR}/outputs/paper.pdf"] == b"PDF-BYTES"


@pytest.mark.asyncio
async def test_resource_rejects_bad_names(monkeypatch):
    for bad in ("../x.pdf", "/abs.pdf", "a\\b.pdf", "..\\..\\x"):
        result = await sciverse.sciverse_resource.ainvoke(
            {"file_name": bad},
            config={"configurable": {
                "runtime_scope_id": "run-1", "uid": "u-1", "workdir_relative_path": WORKDIR}},
        )
        assert result["error"] == "invalid_arguments"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_sciverse_tools.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `sciverse.py`**

以 Synlora `catalog/plugins/sciverse/tools.py`（515 行）为底本平移，差异点：
1. 配置读取：`os.getenv("SCIVERSE_BASE_URL")` / `os.getenv("SCIVERSE_API_TOKEN")`，缺失返回 `{"ok": False, "error": "sciverse_unconfigured", ...}`；
2. 认证头 `Authorization: Bearer {SCIVERSE_API_TOKEN}`（原样，不用代签）；
3. `_render_body` 渲染上限 `RENDER_MAX_CHARS = 40000` 保持，超限截断并在返回 dict 里带 `truncated: True` 与 `chars`；
4. `sciverse_resource` 的落盘从 `ctx.workspace_root` 直写改为 `upload_workspace_file(content, save_relative_path, runtime)`；`file_name` 校验（禁反斜杠、禁 `/` 开头、禁 `..` 段）原样保留，`save_relative_path` 的安全性由 `_workspace_io.resolve_virtual_path` 保证（`ValueError` 转为 `invalid_arguments` dict 返回）；大于 50MB 返回 `too_large`；
5. 工具名转 snake_case，`category="sciverse"`。

五个工具的接口契约（与 Synlora 完全一致，仅改名）：

| 工具 | 上游 | args_schema 关键字段 |
|---|---|---|
| `sciverse_agentic_search` | `POST /agentic-search` | `query: str`（必填，≤4096 字符）、`top_k: int = 10`（1–100）、`sub_queries: int = 0`（0–4） |
| `sciverse_content` | `GET /content` | `doc_id: str`（必填）、`offset: int | None`、`limit: int = 700`（1–50000，仅传 offset 时生效） |
| `sciverse_resource` | `GET /resource` | `file_name: str`（必填）、`save_relative_path: str | None` |
| `sciverse_meta_catalog` | `GET /meta-catalog` | `include_sample_values: bool = False` |
| `sciverse_meta_search` | `POST /meta-search` | `query`（与 sort 互斥）、`filters: list[dict]`、`sort: list[dict]`、`fields: list[str]`、`page: int = 1`、`page_size: int = 25`（1–200）、`cursor: str | None`（与 page>1 互斥）、`freshness_boost: "NONE"/"MILD"/"STRONG"` |

参数校验规则原样平移：filters/sort/fields 支持 list 或 JSON 数组字符串（解析失败 `invalid_arguments`）；每项必须 dict 且 field 非空；freshness_boost 大写枚举校验。

`buildin/__init__.py` 追加：

```python
from .sciverse import (
    sciverse_agentic_search,
    sciverse_content,
    sciverse_meta_catalog,
    sciverse_meta_search,
    sciverse_resource,
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/toolkits/buildin/test_sciverse_tools.py -v`
Expected: PASS（8 项全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/package/yuxi/agents/toolkits/buildin/sciverse.py backend/package/yuxi/agents/toolkits/buildin/__init__.py backend/test/unit/agents/toolkits/buildin/test_sciverse_tools.py
git commit -m "新增 Sciverse 文献检索五工具

- 语义检索/结构化检索/全文分页/字段字典/附件下载
- 附件经工作区写入（虚拟路径校验），>50MB 拒绝
- 结果渲染 40000 字符预算，超限截断标记"
```

---

### Task 5: 四个工具载体技能（spec-nmr / spec-spectra / poly-prediction / sciverse-search）

**Files:**
- Create: `backend/package/yuxi/agents/skills/buildin/spec-nmr/SKILL.md`
- Create: `backend/package/yuxi/agents/skills/buildin/spec-spectra/SKILL.md`
- Create: `backend/package/yuxi/agents/skills/buildin/poly-prediction/SKILL.md`
- Create: `backend/package/yuxi/agents/skills/buildin/sciverse-search/SKILL.md`
- Test: `backend/test/unit/agents/test_builtin_discovery.py`（更新既有断言）

**Interfaces:**
- Consumes: Task 1–4 注册的 13 个工具名。
- Produces: 四个内置技能 slug（`spec-nmr` / `spec-spectra` / `poly-prediction` / `sciverse-search`），其 `tool_dependencies` 是对应工具进入 ToolNode 的唯一通道；Task 7 的 preset `skills` / `preload_skills` 引用这些 slug。

- [ ] **Step 1: 写失败测试（更新发现断言）**

先读 `backend/test/unit/agents/test_builtin_discovery.py`，找到断言 `list_builtin_skill_specs()` slug 集合与 `discover_agent_presets()` slug 集合的两处断言。技能集合断言改为（本任务先加技能；preset 集合在 Task 7 再改）：

```python
expected_skills = {
    # ...既有技能条目保持不变...
    "spec-nmr",
    "spec-spectra",
    "poly-prediction",
    "sciverse-search",
}
```

并在同文件追加一个工具依赖一致性测试（对应 Review Focus #2）：

```python
def test_builtin_skill_tool_dependencies_resolve():
    """每个内置技能声明的工具依赖都必须是已注册的工具名。"""
    from yuxi.agents.toolkits.registry import get_all_tool_instances
    from yuxi.agents.skills.buildin import list_builtin_skill_specs

    registered = {t.name for t in get_all_tool_instances()}
    for spec in list_builtin_skill_specs():
        for tool_name in spec["tool_dependencies"]:
            assert tool_name in registered, f"{spec['slug']} 依赖未注册的工具 {tool_name}"
```

（若 `list_builtin_skill_specs` 的实际 import 路径为 `yuxi.services.skills.shared`，以现有代码为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py -v`
Expected: FAIL（新 slug 未出现 / 依赖不解析）

- [ ] **Step 3: 写四个 SKILL.md**

**`spec-nmr/SKILL.md`**——frontmatter 换成 Yuxi 格式（加 `tool_dependencies`），正文从 `E:\agent_projects\Synlora\apps\web\backend\catalog\plugins\spec_agent\skills\spec-nmr\SKILL.md`（43 行）复制，仅做工具名替换（`spec.nmr.forward` → `spec_nmr_forward` 等三处）与 `file.read`/`python.run` → `read_file`/`execute` 的措辞替换：

```markdown
---
name: spec-nmr
description: '核磁（NMR）谱图解析（正向预测结构→谱、反向预测谱→结构、数据库检索）。用户说"解析这个核磁谱""这个结构NMR什么样""根据碳谱推测结构"时使用。'
version: "1.0"
tool_dependencies: ["spec_nmr_forward", "spec_nmr_reverse", "spec_nmr_search"]
---
```

正文替换映射：`spec.nmr.forward/reverse/search` → 对应 snake_case 工具名；`file.read` / `python.run` → `read_file` / `execute`；`ask_user`（输入不完整先问）→ 中性措辞「输入不完整时先向调用方确认并列出缺失项，不要臆造」（该技能同时被顶层与子智能体使用，不写死交互工具名）；`file.send` → 「结果文件写 outputs/ 并在回复中给出相对路径」。其余原样（该技能本来就全是同步工具调用，无需结构性改写）。

**`spec-spectra/SKILL.md`**——这是改写重点。frontmatter：

```markdown
---
name: spec-spectra
description: '谱图文件解析（NMR / IR / GPC / Raman / LC-MS 五类）。用户要求解析谱图文件、给出谱峰或分子量分布时使用；同步调用对应工具等待结果。含五种任务的完整参数表与默认值。'
version: "2.0"
tool_dependencies: ["spec_task_nmr", "spec_task_ir", "spec_task_gpc", "spec_task_raman", "spec_task_lcms"]
---
```

以 Synlora `spec-spectra/SKILL.md`（191 行，version 1.3）为底本，逐条执行以下改写（**五类任务的参数表与默认值整段原样保留**，这是技能的核心价值）：

1. 开头异步叙事段（"任务在后台运行，状态在右侧运行信息中显示……"）替换为：
   > 五类谱图文件解析已改为**同步工具**：调用 `spec_task_nmr` / `spec_task_ir` / `spec_task_gpc` / `spec_task_raman` / `spec_task_lcms`，传入谱图文件的 `path`（工作区相对路径）与可选 `params`，工具会同步等待（通常数十秒到数分钟，上限约 25 分钟）并直接返回解析结果。
2. 五个 `job.submit(kind=..., params={...}, label=...)` 示例全部改写为对应工具调用示例，例如 Raman 一节：
   ```
   调用 spec_task_raman：
   - path: uploads/raman.txt
   - params: {"mode": "function_groups"}
   （mode 缺省时平台自动兜底 function_groups；greedy_decode / beam_search 当前部署不可用，除非明确要求不要传）
   ```
   其余四类同理（nmr/ir/gpc/lcms 的 params 字段照参数表写示例值）。
3. 删除"失败时 job.status 会返回上游给出的失败原因"与末尾"其他操作"一节（`job.status`/`job.list`/`job.cancel` 及"上游无取消接口"的说明）——同步模式下失败原因直接在工具返回的 `message` 里。
4. "什么时候用同步三件套"一节保留，话术改为对比 `spec_nmr_forward/reverse/search`（输入是 SMILES/化学位移文本）与 `spec_task_*`（输入是谱图文件）的选型。
5. 保留不动的段落：五类参数表全部、"不要自己发明字段名，自造字段会被上游静默忽略"、"不要重复提交同一文件"（改为"同一文件不要重复提交解析，等待返回即可"）。

**`poly-prediction/SKILL.md`**——新建（Synlora 无此技能，内容整理自工具参数契约），全文：

```markdown
---
name: poly-prediction
description: '高分子垂类模型预测：聚酰亚胺 Tg、氟基电解质配方、Raman 光谱解析、共聚竞聚率拟合、硅键谱图集成评分。用户要预测高分子性能、解析高分子 Raman 谱、拟合竞聚率时使用。'
version: "1.0"
tool_dependencies: ["poly_tg_predict", "poly_electrolyte_predict", "poly_raman_analyze", "poly_reactivity_fit", "poly_silicon_bond"]
---

# 高分子垂类预测

五个模型工具的分工与输入要求：

## poly_tg_predict：聚酰亚胺玻璃化转变温度
- 输入：单个 SMILES 字符串。
- 输出：Tg 预测值（℃）与模型版本。

## poly_electrolyte_predict：含氟电解质配方预测与推荐
- 输入：`formulations` 配方数组，每个元素包含 lithium_salt、lithium_salt_mol_L、
  electrolyte_component_1/2/3 与对应 _mol_ratio、monomer_1/2 与对应 _mass_ratio、
  electrolyte_mass_ratio_in_polymer_mix、filler_type、filler_wt_pct；
  无对应组分时传空串或 0，不要删字段。
- `recommendation_limit` 控制推荐配方数（默认 5）。

## poly_raman_analyze：Raman 光谱结构解析
- 输入：谱图文件的工作区相对路径 `path`（txt/dat/csv/xlsx，≤10MiB）。
- `mode` 默认 function_groups（官能团识别）；波数区间 x0/x1 可选。

## poly_reactivity_fit：共聚竞聚率拟合
- 输入：`data_rows` 实验数据行数组，每行含「比例」（投料比，如 "2:8"）、
  「转化率1」「转化率2」（如 "2.53%"）。

## poly_silicon_bond：硅键谱图集成评分
- 输入：`sample` 对象（五条对齐光谱 + 组成信息），按用户提供的结构原样组装透传。

## 使用纪律
- 上游同步执行模型推理，可能需要数分钟：一次只调一个工具，不要并发重复提交。
- 输入不完整时先向调用方确认并列出缺失项，不要用 0 或空值凑数。
- 预测结果是模型估计：输出时给出数值、单位与模型版本，提醒结合实验验证，
  不要臆造模型没有给出的结论；结果文件写 outputs/ 并在回复中给出相对路径。
```

**`sciverse-search/SKILL.md`**——新建，全文：

```markdown
---
name: sciverse-search
description: 'Sciverse 科技文献检索：智能语义检索、meta 结构化检索、文献全文分页读取与附件下载。用户要检索科学文献、查全文、下载原文附件时使用。'
version: "1.0"
tool_dependencies: ["sciverse_agentic_search", "sciverse_content", "sciverse_resource", "sciverse_meta_catalog", "sciverse_meta_search"]
---

# Sciverse 文献检索

## 检索策略
1. 开放式问题（综述、找相关工作）→ `sciverse_agentic_search` 语义检索；
   可用 `sub_queries`（0–4）让上游自动拆解子查询。
2. 精确条件（年份、字段筛选、排序）→ 先 `sciverse_meta_catalog` 查字段定义，
   再 `sciverse_meta_search` 结构化筛选（filters/sort/fields/page/page_size/cursor）。
3. 读到片段后用 `sciverse_content` 按 `offset` 分页取全文（limit 默认 700，最大 50000）。
4. 需要原文附件时用 `sciverse_resource`，给 `save_relative_path`（如 outputs/paper.pdf）
   保存到工作区后把路径告诉调用方。

## 使用纪律
- `query` 与 `sort` 互斥；`cursor` 与 `page > 1` 互斥。
- 检索范围不明确时先向调用方确认主题、时间范围与字段口径。
- 检索不到或证据不足时如实说明，不要编造文献。
- 回答时给出每条结论的文献来源（doc_id / 标题）。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py -v`
Expected: PASS（含新增的依赖一致性测试）

- [ ] **Step 5: Commit**

```bash
git add backend/package/yuxi/agents/skills/buildin/spec-nmr backend/package/yuxi/agents/skills/buildin/spec-spectra backend/package/yuxi/agents/skills/buildin/poly-prediction backend/package/yuxi/agents/skills/buildin/sciverse-search backend/test/unit/agents/test_builtin_discovery.py
git commit -m "新增四个工具载体内置技能

- spec-nmr：核磁三件套使用方法论（平移）
- spec-spectra：五类谱图任务参数表，job.submit 叙事改同步工具直调
- poly-prediction / sciverse-search：新建工具用法与纪律"
```

---

### Task 6: 四个 ARS 学术研究技能

**Files:**
- Create: `backend/package/yuxi/agents/skills/buildin/ars-deep-research/`（SKILL.md + references/ 3 个文件）
- Create: `backend/package/yuxi/agents/skills/buildin/ars-academic-paper/`（SKILL.md + references/ 2 个文件）
- Create: `backend/package/yuxi/agents/skills/buildin/ars-academic-paper-reviewer/`（SKILL.md + references/ 2 个文件）
- Create: `backend/package/yuxi/agents/skills/buildin/ars-academic-pipeline/`（SKILL.md + references/ 1 个文件）
- Test: `backend/test/unit/agents/test_builtin_discovery.py`（slug 集合再加 4 个）

**Interfaces:**
- Consumes: 无（纯方法论技能，无 `tool_dependencies`——web_search 是基础工具无需门控）。
- Produces: 技能 slug `ars-deep-research` / `ars-academic-paper` / `ars-academic-paper-reviewer` / `ars-academic-pipeline`，Task 7 的 ARS 子智能体引用。

- [ ] **Step 1: 更新发现测试断言（再加 4 个 slug）**

`test_builtin_discovery.py` 的技能集合在 Task 5 基础上追加 `"ars-deep-research", "ars-academic-paper", "ars-academic-paper-reviewer", "ars-academic-pipeline"`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py -v`
Expected: FAIL（4 个新 slug 未发现）

- [ ] **Step 3: 复制与改写**

从 `E:\agent_projects\Synlora\apps\web\backend\catalog\plugins\ars\skills\` 复制 4 个技能目录（含全部 `references/*.md`，references 是纯方法论内容**原样不动**）。frontmatter 统一转 Yuxi 格式（去掉 author/tags，保留 name/description/version，不加 tool_dependencies）。以 deep-research 为例：

```markdown
---
name: ars-deep-research
description: '学科无关的深度研究流程：研究问题打磨（FINER）、方法蓝图、系统文献检索、来源分级核验、跨源综合与矛盾处理、魔鬼代言人质疑，产出规范研究报告。用户要"深度研究、文献综述、系统性综述、元分析、事实核查、三段式文献扫描（WHY/HOW/WHAT）、引导我想清楚研究问题"时用。'
version: "1.0"
---
```

每个 SKILL.md 正文的「Synlora 沙箱适配」块整体替换为「Yuxi 适配」块，统一文本：

```markdown
## 平台适配（优先级高于正文中的通用工具指导）

- 公网检索用 web_search；需要深入读取命中页面时，可在沙盒中用 execute curl 获取。
- 计算/数据处理用 execute（Python）；文件读写用 read_file / write_file / ls。
- 阶段间确认点用 ask_user_question 向用户征询，不自动跳过诚信与评审类关键确认。
- 一切产物写入 outputs/ 目录并用 present_artifacts 展示给用户；引用格式默认 APA 7。
```

正文其余内容原样保留，但全文做以下机械替换（4 个技能一致）：

| Synlora 工具名 | 替换为 |
|---|---|
| `web.search` / `web.fetch` | `web_search`（fetch 相关句子并入"execute curl"措辞） |
| `file.read` / `file.write` / `file.list` | `read_file` / `write_file` / `ls` |
| `python.run` / `shell.run` | `execute` |
| `skill.read` / `skill.list` | 「read_file 技能文件」（激活技能后经 read_file 读取 SKILL.md） |
| `knowledge.search` | 删除该条指导（迁移首期不挂知识库工具） |
| `ask_user` | `ask_user_question`（交互语义原样保留——本迁移形态为顶层智能体，具备提问能力） |
| `file.send` | 「写入 outputs/ 并用 present_artifacts 展示」 |
| `job.submit` / `job.status` 等 | 删除（ARS 正文基本没有，出现即删） |

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/package/yuxi/agents/skills/buildin/ars-deep-research backend/package/yuxi/agents/skills/buildin/ars-academic-paper backend/package/yuxi/agents/skills/buildin/ars-academic-paper-reviewer backend/package/yuxi/agents/skills/buildin/ars-academic-pipeline backend/test/unit/agents/test_builtin_discovery.py
git commit -m "新增 ARS 学术研究四个内置技能

- deep-research / academic-paper / reviewer / pipeline 全流程方法论
- 平台适配块改写：ask_user 确认点改默认假设+待确认清单，产物写 outputs/"
```

---

### Task 7: 四个内置 preset（ARS 顶层智能体 + 三个子智能体）

**Files:**
- Create: `backend/package/yuxi/agents/presets/subagents/spec_agent.py`
- Create: `backend/package/yuxi/agents/presets/subagents/poly_agent.py`
- Create: `backend/package/yuxi/agents/presets/subagents/sciverse.py`
- Create: `backend/package/yuxi/agents/presets/academic_research.py`
- Test: `backend/test/unit/agents/test_builtin_discovery.py`、`backend/test/unit/repositories/test_agent_repository_deep_research.py`（更新 slug 集合）

**Interfaces:**
- Consumes: Task 5/6 的技能 slug；`AgentPreset`、`SubAgentBackend`（子智能体，`presets/subagents/` 目录）、`ChatbotAgent`（顶层，`presets/` 根目录，同 `presets/deep_research.py`）。`discover_agent_presets` 递归发现两种位置，无需改注册代码。
- Produces: 子智能体 slug `spec-agent` / `poly-agent` / `sciverse`（主智能体 `context.subagents` 引用，`subagent_start` 派发 / `subagent_await` 收结果）；顶层智能体 slug `academic-research`（用户直接选择对话，保留 `ask_user_question` / `present_artifacts` 完整交互）。

**输入参数收集机制（不改框架）**：子智能体被框架禁用 `ask_user_question`，参数缺口由主 Agent 侧闭环——① preset `description` 写明「调用时需提供的输入」，主 Agent 的 `SUBAGENT_SYSTEM_PROMPT` 会把子智能体描述注入，引导主 Agent 派发前先向用户收齐；② 子智能体 system_prompt 规定「输入不完整时直接返回缺失清单（开头列出、不调用工具、不臆造）」，主 Agent 收到后向用户补问并重新派发。

- [ ] **Step 1: 更新两个既有测试的 slug 集合（失败测试）**

`test_builtin_discovery.py` preset 集合追加 `"spec-agent", "poly-agent", "sciverse", "academic-research"`；
`test_agent_repository_deep_research.py` 的 `set(created)` 断言同步追加同样 4 个 slug（spec-agent / poly-agent / sciverse 为 `backend_id="SubAgentBackend"`、`is_subagent=True`；academic-research 为 ChatbotAgent、`is_subagent=False`——如断言按 backend 分组则各归各组）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py test/unit/repositories/test_agent_repository_deep_research.py -v`
Expected: FAIL（新 slug 未发现）

- [ ] **Step 3: 写四个 preset 文件**

`presets/subagents/spec_agent.py` 全文：

```python
"""Spec_Agent 谱图解析子智能体 preset。"""

from yuxi.agents.presets import AgentPreset

SYSTEM_PROMPT = """你是「谱图解析」子智能体，专注核磁（NMR）谱图与分子结构的双向解析，
并支持 NMR/IR/GPC/Raman/LC-MS 五类谱图文件的解析。

可用工具：
- spec_nmr_forward：给定分子 SMILES 预测 13C/1H 化学位移（正向预测）
- spec_nmr_reverse：给定实测化学位移反推候选结构（反向预测）
- spec_nmr_search：核磁数据库检索
- spec_task_nmr / spec_task_ir / spec_task_gpc / spec_task_raman / spec_task_lcms：
  谱图文件解析（传 path 与可选 params，完整参数表见 spec-spectra 技能）
- 读取工作区文件用 read_file / ls；结果文件写 outputs/ 目录

工作方式：
- 给定结构时用正向预测核对谱图；给定实测谱峰时用反向预测或数据库检索推断候选结构；
  输入是谱图文件时用 spec_task_* 解析
- 上游推理较慢（单次可能数分钟）：一次只调一个工具，不要重复提交相同请求
- 你无法向用户提问：调用方给的任务描述里缺少必要输入（SMILES / 化学位移数据 /
  谱图文件路径等）时，不要调用工具、不要臆造，直接在回复开头列出「缺失输入清单」
  并返回，由调用方补齐后重新派发

输出要求：
- 结论先行，说明依据与置信度；多候选要排序并说明理由
- 实测谱峰与候选明显不符时如实说明，不要臆造
- 生成的结果文件写入 outputs/ 并在回复末尾给出相对路径（调用方可用于向用户展示交付）"""

PRESET = AgentPreset(
    slug="spec-agent",
    name="谱图解析专家",
    description=(
        "核磁（NMR）正反向预测与库检索，NMR/IR/GPC/Raman/LC-MS 五类谱图文件解析。"
        "调用时需提供：分子 SMILES、实测化学位移数据或谱图文件的工作区路径，"
        "以及可选的解析参数；输入不完整会返回缺失清单。"
    ),
    backend_id="SubAgentBackend",
    context={
        "system_prompt": SYSTEM_PROMPT,
        "tools": [],
        "skills": ["spec-nmr", "spec-spectra"],
        "preload_skills": ["spec-nmr", "spec-spectra"],
    },
)
```

`presets/subagents/poly_agent.py` 全文：

```python
"""Poly_Agent 高分子性能预测子智能体 preset。"""

from yuxi.agents.presets import AgentPreset

SYSTEM_PROMPT = """你是「高分子性能预测」子智能体，可调用 Poly_Agent 平台的五个垂类模型：
聚酰亚胺玻璃化转变温度预测（poly_tg_predict）、含氟电解质配方性能预测与配方推荐
（poly_electrolyte_predict）、Raman 光谱结构解析（poly_raman_analyze）、
共聚竞聚率拟合（poly_reactivity_fit）、硅键谱图集成评分（poly_silicon_bond）。

工作方式：
- 先核对调用方给的任务描述里输入是否完整（SMILES / 配方表 / 光谱文件路径 /
  实验数据行）；你无法向用户提问：缺什么不要调用工具、不要用 0 或空值凑数，
  直接在回复开头列出「缺失输入清单」并返回，由调用方补齐后重新派发
- 模型推理较慢（可能数分钟）：一次只调一个工具，不要重复提交
- 谱图文件的工作区路径用 read_file / ls 确认存在后再提交

输出要求：
- 结果要给出数值、单位与模型版本，多候选要排序并说明理由
- 预测结果属于模型估计：提醒调用方结合实验验证
- 不要臆造模型没有输出的结论；结果文件写 outputs/ 并在回复末尾给出相对路径"""

PRESET = AgentPreset(
    slug="poly-agent",
    name="高分子性能预测专家",
    description=(
        "高分子垂类模型预测：聚酰亚胺 Tg、氟基电解质配方、Raman 解析、竞聚率拟合、硅键评分。"
        "调用时需提供：SMILES、完整配方表、光谱文件路径或实验数据行；"
        "输入不完整会返回缺失清单。"
    ),
    backend_id="SubAgentBackend",
    context={
        "system_prompt": SYSTEM_PROMPT,
        "tools": [],
        "skills": ["poly-prediction"],
        "preload_skills": ["poly-prediction"],
    },
)
```

`presets/subagents/sciverse.py` 全文：

```python
"""Sciverse 文献检索子智能体 preset。"""

from yuxi.agents.presets import AgentPreset

SYSTEM_PROMPT = """你是「科技文献检索」子智能体，使用 Sciverse 检索科学文献。

可用工具：
- sciverse_agentic_search：开放式语义检索（可用 sub_queries 自动拆解子查询）
- sciverse_meta_catalog / sciverse_meta_search：字段字典与结构化筛选
- sciverse_content：按 doc_id 分页读全文
- sciverse_resource：下载原文附件到工作区（传 save_relative_path 如 outputs/paper.pdf）

工作方式：
- 先从任务描述里明确主题、时间范围和字段口径；描述含糊时按最合理口径检索并在
  回复中说明所采用的口径，不要因此中止（检索范围问题通常不值得打断）
- 开放式问题优先 agentic_search 获取语义相关片段；精确条件先 meta_catalog
  查字段定义，再配合 meta_search 做结构化筛选
- 读到片段后用 content 按 offset 分段取全文
- query 与 sort 互斥；cursor 与 page>1 互斥

输出要求：
- 返回带文献来源（doc_id / 标题）与检索依据的结构化结果
- 检索不到或证据不足时如实说明，不要臆造文献"""

PRESET = AgentPreset(
    slug="sciverse",
    name="科技文献检索专家",
    description=(
        "Sciverse 文献检索：语义检索、结构化筛选、全文分页读取与附件下载。"
        "调用时建议提供：检索主题、时间范围与筛选口径。"
    ),
    backend_id="SubAgentBackend",
    context={
        "system_prompt": SYSTEM_PROMPT,
        "tools": [],
        "skills": ["sciverse-search"],
        "preload_skills": ["sciverse-search"],
    },
)
```

`presets/academic_research.py`（顶层智能体，ChatbotAgent）全文：

```python
"""学术研究（ARS）智能体 preset。"""

from yuxi.agents.presets import AgentPreset

SYSTEM_PROMPT = """你是学术研究助手，覆盖「研究 → 写作 → 评审 → 修订 → 定稿」全流程，
方法论来自 ARS 技能套件（ars-deep-research / ars-academic-paper /
ars-academic-paper-reviewer / ars-academic-pipeline）。

工作纪律：
- 动手前先 read_file 对应技能的 SKILL.md 读方法论：调研/综述/事实核查 →
  ars-deep-research；写论文/摘要/引用核查 → ars-academic-paper；评审已有稿件 →
  ars-academic-paper-reviewer；用户要端到端全流程 → ars-academic-pipeline；
  分不清用哪个时按技能 description 的路由表判断
- 用户意图模糊（例如只想想清楚研究问题）优先引导式（socratic/plan）而非直接产出
- 检索公网文献与事实核查用 web_search；每条参考文献必须逐条核实真实存在，
  核不实的一律标记 FAIL 不进正文（灰色地带按 FAIL 处理）
- 阶段间确认点用 ask_user_question 征询用户，不自动跳过诚信与评审类关键确认
- 一切产物写入 outputs/ 并用 present_artifacts 交付；引用格式默认 APA 7，
  用户指定其它格式时切换
- 语言跟随用户，学术术语保留英文；报告必须含局限性说明与 AI 使用披露"""

PRESET = AgentPreset(
    slug="academic-research",
    name="学术研究助手",
    description="学术研究全流程：选题调研、证据综合、论文写作、模拟同行评审与修订定稿。",
    backend_id="ChatbotAgent",
    context={
        "system_prompt": SYSTEM_PROMPT,
        "tools": ["web_search"],
        "skills": [
            "ars-deep-research",
            "ars-academic-paper",
            "ars-academic-paper-reviewer",
            "ars-academic-pipeline",
        ],
    },
)
```

> 设计说明：spec/poly/sciverse 三个子智能体用 `preload_skills` 让工具开箱即用（技能正文仍可 read_file 获取方法论）；它们被框架禁用 `ask_user_question`/`present_artifacts`，输入缺口靠「description 提示主 Agent 预收集 + 缺失清单返回」闭环（见本任务 Interfaces 的输入参数收集机制）；sciverse 对"检索口径含糊"采取合理默认并说明，不轻意中断（范围问题不值得打断主流程）。academic-research 为顶层智能体不 preload（技能是重方法论文档，按需激活省上下文），`tools: ["web_search"]` 显式收窄（web_search 未配置部署时会被 `_resolve_resource_selection` 静默过滤，不报错），保留 `ask_user_question` / `present_artifacts` 完整交互。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd E:\agent_projects\Yuxi\backend && uv run pytest test/unit/agents/test_builtin_discovery.py test/unit/repositories/test_agent_repository_deep_research.py -v`
Expected: PASS

- [ ] **Step 5: 本地验证 preset 落库生效（对应 Review Focus #5）**

开发库若已存在同名 slug（首次部署不会），先确认无残留：

Run: `cd E:\agent_projects\Yuxi && docker compose exec postgres psql -U <用户名> -d <库名> -c "SELECT slug FROM agents WHERE slug IN ('spec-agent','poly-agent','sciverse','academic-research');"`
Expected: 空结果（有残留则删除对应行，因 ensure_preset 只首次落库不回填）。

- [ ] **Step 6: Commit**

```bash
git add backend/package/yuxi/agents/presets/subagents/spec_agent.py backend/package/yuxi/agents/presets/subagents/poly_agent.py backend/package/yuxi/agents/presets/subagents/sciverse.py backend/package/yuxi/agents/presets/academic_research.py backend/test/unit/agents/test_builtin_discovery.py backend/test/unit/repositories/test_agent_repository_deep_research.py
git commit -m "新增 Synlora 迁移的四个内置 preset（1 顶层智能体 + 3 子智能体）

- academic-research：ChatbotAgent 顶层形态，保留 ask_user/present_artifacts 完整交互
- spec-agent / poly-agent / sciverse：SubAgentBackend 子智能体
- 参数缺口闭环：description 提示主 Agent 预收集 + 缺失清单返回后重派
- 工具经技能 tool_dependencies 门控 + preload_skills 预载开放"
```

---

### Task 8: 部署配置、前端图标、版本号与 README

**Files:**
- Modify: `docker-compose.yml`（`x-api-worker-env` 锚点，第 1 行起）
- Modify: `.env.template`
- Modify: `backend/pyproject.toml`（版本 0.7.3 → 0.8.0）
- Modify: `web/src/components/ToolCallingResult/toolRegistry.js`
- Modify: `README.md`

**Interfaces:**
- Consumes: 前述全部任务的工具名。
- Produces: 部署可配置的环境变量集合；前端工具图标兜底。

- [ ] **Step 1: compose 与 .env.template 加环境变量**

`docker-compose.yml` 第 1 行 `x-api-worker-env: &api-worker-env` 的映射里追加（api 与 worker 共用，工具在 worker 进程执行、API 侧校验与展示也读同一批变量）：

```yaml
      SPEC_AGENT_BASE_URL: ${SPEC_AGENT_BASE_URL:-}
      SPEC_AGENT_TOKEN: ${SPEC_AGENT_TOKEN:-}
      SPEC_TASK_MAX_WAIT_SECONDS: ${SPEC_TASK_MAX_WAIT_SECONDS:-1500}
      POLY_AGENT_BASE_URL: ${POLY_AGENT_BASE_URL:-}
      POLY_AGENT_USERNAME: ${POLY_AGENT_USERNAME:-}
      POLY_AGENT_PASSWORD: ${POLY_AGENT_PASSWORD:-}
      SCIVERSE_BASE_URL: ${SCIVERSE_BASE_URL:-}
      SCIVERSE_API_TOKEN: ${SCIVERSE_API_TOKEN:-}
```

`.env.template` 追加（带注释块）：

```bash
# ---------- Synlora 插件迁移的内置子智能体上游服务 ----------
# Spec_Agent 谱图解析（核磁三件套 + 五类谱图文件解析）
SPEC_AGENT_BASE_URL=http://10.26.15.93:8001
SPEC_AGENT_TOKEN=
# 五类谱图任务同步等待上限（秒），需小于 worker job 超时
SPEC_TASK_MAX_WAIT_SECONDS=1500
# Poly_Agent 高分子垂类预测（平台级账号，自动登录并缓存凭证）
POLY_AGENT_BASE_URL=http://10.26.15.93:8003
POLY_AGENT_USERNAME=
POLY_AGENT_PASSWORD=
# Sciverse 文献检索
SCIVERSE_BASE_URL=https://api.sciverse.space
SCIVERSE_API_TOKEN=
```

- [ ] **Step 2: 前端工具图标兜底**

`web/src/components/ToolCallingResult/toolRegistry.js`：import 区补 `FlaskConical, Microscope`（来自 `@lucide/vue`，`BookOpen` 已有），`TOOL_ICON_MAP` 追加：

```javascript
  spec_nmr_forward: Microscope,
  spec_nmr_reverse: Microscope,
  spec_nmr_search: Microscope,
  spec_task_nmr: Microscope,
  spec_task_ir: Microscope,
  spec_task_gpc: Microscope,
  spec_task_raman: Microscope,
  spec_task_lcms: Microscope,
  poly_tg_predict: FlaskConical,
  poly_electrolyte_predict: FlaskConical,
  poly_raman_analyze: FlaskConical,
  poly_reactivity_fit: FlaskConical,
  poly_silicon_bond: FlaskConical,
  sciverse_agentic_search: BookOpen,
  sciverse_content: BookOpen,
  sciverse_resource: BookOpen,
  sciverse_meta_catalog: BookOpen,
  sciverse_meta_search: BookOpen,
```

（工具中文名由后端 `display_name` 随工具列表下发，前端 `TOOL_NAME_MAP` 无需改。）

- [ ] **Step 3: 版本号与 README**

`backend/pyproject.toml`：`version = "0.7.3"` → `version = "0.8.0"`（向下兼容的新功能，升次版本号）。

`README.md` 功能清单新增一节（放现有智能体/技能相关章节附近，措辞与文件现有风格一致）：

```markdown
## 内置领域智能体与子智能体（Synlora 插件迁移）

**顶层智能体**（智能体列表直接选择对话，完整交互能力）：

| 智能体 | 能力 | 依赖配置 |
| --- | --- | --- |
| academic-research 学术研究助手 | 研究→写作→评审→修订→定稿全流程方法论（ARS 技能套件） | `web_search`（可选） |

**子智能体**（主智能体经 `subagents` 配置引用，`subagent_start` 派发、`subagent_await` 收结果；输入参数由主智能体派发前向用户收齐，不完整时子智能体返回缺失清单）：

| 子智能体 | 能力 | 依赖配置 |
| --- | --- | --- |
| spec-agent 谱图解析专家 | 核磁正/反向预测与库检索；NMR/IR/GPC/Raman/LC-MS 五类谱图文件解析（同步等待） | `SPEC_AGENT_BASE_URL`（可选 `SPEC_AGENT_TOKEN`、`SPEC_TASK_MAX_WAIT_SECONDS`） |
| poly-agent 高分子性能预测专家 | 聚酰亚胺 Tg、氟基电解质配方、Raman 解析、竞聚率拟合、硅键评分 | `POLY_AGENT_BASE_URL` + 账号密码 |
| sciverse 科技文献检索专家 | 语义检索、结构化筛选、全文分页、附件下载 | `SCIVERSE_BASE_URL` + `SCIVERSE_API_TOKEN` |

对应能力同时以内置技能形式提供（spec-nmr / spec-spectra / poly-prediction / sciverse-search / ars-*），
可挂到任意智能体。上游未配置时工具返回明确的未配置错误，不影响其他能力。
```

- [ ] **Step 4: 全量回归**

Run: `cd E:\agent_projects\Yuxi && make test`
Expected: 全部 PASS（若既有测试因新增工具/技能/preset 的全局注册受影响，逐个修复断言后重跑；不允许跳过）

前端改动验证：`cd E:\agent_projects\Yuxi\web && pnpm run build`（能构建即交付，真机验证由用户自测）。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.template backend/pyproject.toml web/src/components/ToolCallingResult/toolRegistry.js README.md
git commit -m "补齐 Synlora 领域能力的部署配置与展示

- compose/api-worker-env 与 .env.template 增加 8 个上游配置变量
- 前端工具图标兜底；版本号 0.7.3 -> 0.8.0
- README 增加内置智能体与子智能体功能说明"
```

---

## 验收清单（全部任务完成后）

1. `make test` 全绿；`pnpm run build` 通过。
2. `docker compose up` 后：智能体列表可见 academic-research（顶层智能体）；主智能体配置的子智能体可选项出现 spec-agent / poly-agent / sciverse；技能列表可见 8 个新内置技能。
3. 任一普通智能体的工具列表**不出现** spec_* / poly_* / sciverse_* 工具（category 隔离生效）。
4. 配置好上游地址后，给主智能体挂 `subagents: ["spec-agent"]`，对话"解析这个核磁谱"：主智能体先收齐输入（谱图文件路径等）再 `subagent_start` 派发，子智能体面板可观察 spec_task_nmr 的同步等待与结果返回；输入故意不完整时，子智能体应返回缺失清单而非臆造调用。
5. 选择 academic-research 智能体直接对话，验证 ask_user_question 阶段确认与 present_artifacts 交付可用。
6. 不配置 SPEC_AGENT_BASE_URL 时调用谱图工具，返回明确的未配置错误文案，无异常堆栈。
