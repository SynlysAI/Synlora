# 统一 Job 注册表（异步任务地基）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一套通用的后台长任务机制——工具提交即返回 task_id（不阻塞 step）、后台统一轮询外部系统状态、任务完成自动唤醒 agent 继续处理；`Spec_Agent` 异步谱图任务（C2）与 SpecLabOS（C3）都建在它上面。

**Architecture:** 机制归 harness、存储与编排归宿主。harness 侧只加**状态机**（`JobStatus` + 合法流转）与**四个工具声明**（`job.submit/status/list/cancel`，经 `ctx.extra["job_handler"]` 转发宿主）；宿主侧实现 `jobs` 集合（走既有 `DocumentStore`）、`JobService`（提交/查询/取消/流转）、`JobConnector` 协议与注册表（子平台各自实现）、`JobPoller`（后台定时轮询）与**完成唤醒**（任务终态时向所属会话注入一条系统消息并起新一轮 run）。

**Tech Stack:** Python 3.12 / pydantic v2 / FastAPI / asyncio（`DocumentStore` sqlite+mongodb 双后端）/ pytest（`asyncio_mode = "auto"`）；conda 环境 `synlysagent`。

**范围说明（重要）:** 本计划**不含**真实 Spec_Agent 异步接口对接（那是 C2）。为让本计划可独立端到端验收，Task 4 提供一个**测试用 Connector**（`FakeConnector`，纯内存，不依赖外部系统），并在测试中用它跑通"提交 → 轮询 → 完成唤醒"全链路。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `packages/synlys-harness/src/synlys_harness/jobs.py` | 新建 | 任务状态机：`JobStatus`、终态/活跃态常量、`can_transition`、`is_terminal` |
| `packages/synlys-harness/src/synlys_harness/__init__.py` | 修改 | 导出上述符号 |
| `packages/synlys-harness/src/synlys_harness/tools/builtin.py` | 修改 | 声明 `job.submit / job.status / job.list / job.cancel` 四个工具 |
| `packages/synlys-harness/tests/test_jobs.py` | 新建 | 状态机单测 |
| `packages/synlys-harness/tests/test_builtin.py` | 修改 | 期望工具清单加 4 个 job 工具 + 工具行为测 |
| `apps/web/backend/app/db/store.py` | 修改 | `COLLECTION_INDEXES` 加 `jobs` |
| `apps/web/backend/app/db/repos.py` | 修改 | 新增 `JobRepo` |
| `apps/web/backend/app/services/job_connectors.py` | 新建 | `JobConnector` 协议、`JobConnectorRegistry`、`FakeConnector` |
| `apps/web/backend/app/services/job_service.py` | 新建 | 提交/查询/取消/状态流转/完成通知（核心编排） |
| `apps/web/backend/app/services/job_poller.py` | 新建 | 后台轮询 task 生命周期 |
| `apps/web/backend/app/services/session_runtime.py` | 新建 | 会话运行装配解析（从 `sessions_api` 抽出，供发消息与唤醒共用） |
| `apps/web/backend/app/api/sessions_api.py` | 修改 | 改用 `session_runtime.resolve_session_runtime`（消除重复逻辑） |
| `apps/web/backend/app/services/agent_service.py` | 修改 | 加 `is_busy()`、`wake()`，run 结束回调 |
| `apps/web/backend/app/api/jobs_api.py` | 新建 | `GET /api/v1/jobs`、`GET /api/v1/jobs/{job_id}` |
| `apps/web/backend/app/main.py` | 修改 | 装配 JobService / Poller / job_handler / 唤醒回调 |
| `apps/web/backend/tests/test_job_service.py` | 新建 | JobService 单测 |
| `apps/web/backend/tests/test_job_poller.py` | 新建 | 轮询与唤醒集成测 |
| `apps/web/backend/tests/test_jobs_api.py` | 新建 | API 测 |
| `apps/web/frontend/src/components/chat/UserMessage.tsx` | 修改 | 系统注入的 `job_completed` 消息渲染为提示条 |
| `apps/web/frontend/src/types.ts` | 修改 | 事件 payload 类型加 `kind`/`job_id` |

---

## 关键契约（先读，后续任务都依赖）

**1. `job_handler` 协议**（harness 工具 → 宿主，仿 `ask_user_handler`）

```python
async def job_handler(payload: dict) -> ToolResult
# payload = {"action": "submit"|"status"|"list"|"cancel", "tool_call_id": str, ...}
```

**2. `JobConnector` 协议**（宿主 → 子平台；由插件实现并在启动时注册）

```python
class JobConnector(Protocol):
    kind: str          # 任务类型，如 "spec.nmr.forward"
    plugin_id: str     # 归属插件 id（决定轮询时按哪个插件解析配置）

    async def submit(self, params: dict, ctx: dict) -> str: ...   # 返回外部系统 id
    async def poll(self, external_id: str, ctx: dict) -> str: ...  # 返回外部状态原文
    async def cancel(self, external_id: str, ctx: dict) -> bool: ...
```

`ctx` 由宿主填充：`{"config": {插件配置}, "ai4ms_token": "..."}`。

**3. 状态映射**：`JobConnectorRegistry` 持有 `kind → (connector, status_map)`；`status_map` 把外部状态原文映射为 `JobStatus`，映射不到时保持原状态不变（并记日志）。

**4. 唤醒契约**：任务进入终态 → `JobService` 调 `agent_service.is_busy(session_id)`；空闲则立即 `wake()`，忙则进 pending 队列，由 `AgentService` 在 run 结束时回调 `job_service.drain_pending(session_id)`。

**5. 单实例前提**：`JobPoller` 是进程内 asyncio task，依赖既有的 `workers=1` 单实例约束（见 README 部署硬约束）；多副本会导致同一任务被多次轮询与多条唤醒。

---

## Task 1: harness 任务状态机

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/jobs.py`
- Modify: `packages/synlys-harness/src/synlys_harness/__init__.py`
- Test: `packages/synlys-harness/tests/test_jobs.py`

- [ ] **Step 1: 写失败测试**

创建 `packages/synlys-harness/tests/test_jobs.py`：

```python
"""后台任务状态机单测。"""
import json

from synlys_harness.jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    _ALLOWED_TRANSITIONS,
    can_transition,
    is_terminal,
)


def test_terminal_and_active_partition():
    """终态与活跃态互补且不重叠（覆盖全部状态）。"""
    assert TERMINAL_STATUSES | ACTIVE_STATUSES == set(JobStatus)
    assert not (TERMINAL_STATUSES & ACTIVE_STATUSES)


def test_terminal_predicate():
    """三个终态的 is_terminal 为真，两个活跃态为假。"""
    assert is_terminal(JobStatus.COMPLETED)
    assert is_terminal(JobStatus.FAILED)
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.PENDING)
    assert not is_terminal(JobStatus.RUNNING)


def test_active_to_terminal_allowed():
    """活跃态可以走向任一终态。"""
    for src in (JobStatus.PENDING, JobStatus.RUNNING):
        for dst in TERMINAL_STATUSES:
            assert can_transition(src, dst), f"{src} -> {dst} 应允许"


def test_pending_to_running_allowed():
    """排队 → 执行中允许。"""
    assert can_transition(JobStatus.PENDING, JobStatus.RUNNING)


def test_terminal_is_frozen():
    """终态不可再流转（重复置同一终态视为幂等合法）。"""
    for src in TERMINAL_STATUSES:
        for dst in JobStatus:
            if dst == src:
                assert can_transition(src, dst), "同状态应幂等允许"
            else:
                assert not can_transition(src, dst), f"{src} -> {dst} 不应允许"


def test_running_cannot_go_back_to_pending():
    """执行中不可回退到排队（防轮询抖动导致状态倒退）。"""
    assert not can_transition(JobStatus.RUNNING, JobStatus.PENDING)


def test_status_value_is_string():
    """状态值即持久化字符串（全小写、与成员名一致）。"""
    for status in JobStatus:
        assert status.value == status.name.lower()
        assert isinstance(status.value, str)


def test_accepts_plain_strings_from_persistence():
    """持久层读回的是裸字符串：同状态幂等与流转判断都要正常工作。

    用 json 往返造两个内容相同的**不同对象**——直接写字面量会被 CPython
    interning 成同一对象，`is` 的缺陷就抓不到了。
    """
    src = json.loads('"pending"')
    dst = json.loads('"pending"')
    assert src is not dst
    assert can_transition(src, dst) is True
    assert can_transition("pending", "running") is True
    assert can_transition("running", "pending") is False
    assert can_transition("completed", "failed") is False


def test_transition_table_covers_all_states():
    """流转表必须覆盖全部状态（漏配会静默 fail-closed）。"""
    assert set(_ALLOWED_TRANSITIONS) == set(JobStatus)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'synlys_harness.jobs'`

- [ ] **Step 3: 实现状态机**

创建 `packages/synlys-harness/src/synlys_harness/jobs.py`：

```python
"""后台任务（Job）状态机：异步长任务的统一生命周期定义。

机制归 harness、存储归宿主：本模块只定义状态集合与合法流转；任务文档的
持久化、外部系统对接与完成唤醒由宿主实现。
"""
from __future__ import annotations

import enum


class JobStatus(str, enum.Enum):
    """任务生命周期状态（Connector 负责把外部系统状态映射到其中一种）。"""

    PENDING = "pending"      # 已提交、等待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 成功结束
    FAILED = "failed"        # 失败结束
    CANCELLED = "cancelled"  # 已取消


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
})
ACTIVE_STATUSES = frozenset({JobStatus.PENDING, JobStatus.RUNNING})

# 合法流转表：终态无出边；同状态在 can_transition 里单独放行（幂等）
_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, *TERMINAL_STATUSES}),
    JobStatus.RUNNING: frozenset(TERMINAL_STATUSES),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def can_transition(src: JobStatus, dst: JobStatus) -> bool:
    """判断状态流转是否合法（同状态幂等合法；终态不可迁移到任何其他状态）。

    Args:
        src: 当前状态（JobStatus 成员，或从持久层读回的等值字符串）。
        dst: 目标状态（同上）。

    Returns:
        是否允许该流转；未知/非法值一律 False（fail-closed）。

    查询失败/超时导致的"状态未知"不得写入本表——调用方应保持原状态，
    避免把一次网络抖动变成状态倒退。

    比较用 `==` 而非 `is`：JobStatus 是 str 混入枚举，"持久层读回的裸字符串"
    与枚举成员值相等但不是同一对象，用 `is` 会让同状态幂等判断静默失效。
    """
    if src == dst:
        return True
    return dst in _ALLOWED_TRANSITIONS.get(src, frozenset())


def is_terminal(status: JobStatus) -> bool:
    """是否为终态状态（终态在流转表中无出边，即不再变化）。"""
    return status in TERMINAL_STATUSES
```

- [ ] **Step 4: 导出符号**

修改 `packages/synlys-harness/src/synlys_harness/__init__.py`，在既有 import 段落中加入（保持文件现有排序风格）：

```python
from .jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    can_transition,
    is_terminal,
)
```

并在 `__all__`（若存在）中加入对应名字；若该文件不使用 `__all__`，则跳过这一步。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs.py -v
```
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
git add packages/synlys-harness/src/synlys_harness/jobs.py packages/synlys-harness/src/synlys_harness/__init__.py packages/synlys-harness/tests/test_jobs.py
git commit -m "harness 新增后台任务状态机

- JobStatus 五态 + 合法流转校验（终态冻结、幂等同态）
- 终态/活跃态常量供轮询与唤醒过滤"
```

---

## Task 2: harness 侧四个 job 工具

**Files:**
- Modify: `packages/synlys-harness/src/synlys_harness/tools/builtin.py`
- Test: `packages/synlys-harness/tests/test_builtin.py`

- [ ] **Step 1: 写失败测试**

在 `packages/synlys-harness/tests/test_builtin.py` 中：
1. 把顶部 import 改为 `from synlys_harness.types import ToolContext, ToolResult`（新增 ToolResult）。
2. 修改 `EXPECTED` 常量，加入 4 个 job 工具：

```python
EXPECTED = [
    "file.read", "file.write", "file.list", "python.run", "file.read_image",
    "knowledge.list", "knowledge.search", "web.search", "web.fetch", "http.request",
    "ask_user", "file.send", "skill.list", "skill.read",
    "job.submit", "job.status", "job.list", "job.cancel",
]
```

在同一文件末尾追加测试：

```python
async def test_job_submit_requires_handler(tmp_path):
    """无 job_handler 时四个 job 工具都报 no_handler（fail-closed）。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    for name, args in (
        ("job.submit", {"kind": "spec.nmr.forward", "params": {}}),
        ("job.status", {"job_id": "j1"}),
        ("job.list", {}),
        ("job.cancel", {"job_id": "j1"}),
    ):
        result = await pipe.run(name, ctx, args)
        assert result.ok is False
        assert result.error == "no_handler"


async def test_job_submit_validates_arguments(tmp_path):
    """kind 为空或 params 非对象时不调 handler。"""
    calls: list[dict] = []

    async def handler(payload: dict):
        calls.append(payload)
        return ToolResult(ok=True, content="ok")

    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"job_handler": handler})
    bad_kind = await pipe.run("job.submit", ctx, {"kind": "  ", "params": {}})
    assert bad_kind.ok is False and bad_kind.error == "invalid_arguments"
    bad_params = await pipe.run("job.submit", ctx, {"kind": "k", "params": "oops"})
    assert bad_params.ok is False and bad_params.error == "invalid_arguments"
    assert calls == []


async def test_job_submit_forwards_payload(tmp_path):
    """submit 把 action/kind/params/label/tool_call_id 原样交给宿主 handler。"""
    seen: list[dict] = []

    async def handler(payload: dict):
        seen.append(payload)
        return ToolResult(ok=True, content="已提交，任务 ID: j-1")

    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"job_handler": handler, "tool_call_id": "tc-9"})
    result = await pipe.run("job.submit", ctx, {
        "kind": "spec.nmr.forward", "params": {"smiles_input": "CCO"}, "label": "乙醇预测"})
    assert result.ok is True
    assert seen[0]["action"] == "submit"
    assert seen[0]["kind"] == "spec.nmr.forward"
    assert seen[0]["params"] == {"smiles_input": "CCO"}
    assert seen[0]["label"] == "乙醇预测"
    assert seen[0]["tool_call_id"] == "tc-9"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest tests/test_builtin.py -v
```
Expected: FAIL — `test_all_registered` 断言不符（缺 4 个 job 工具），3 个新测试报工具不存在

- [ ] **Step 3: 实现四个工具**

先在 `packages/synlys-harness/src/synlys_harness/tools/builtin.py` 顶部做两处准备：

1. 模块 docstring 目前是枚举式清单（`"""内置工具集：file.* / python.run / ... / skill.*。"""`），早已漏掉 `ask_user` / `file.send`，本次又会漏 `job.*`——枚举注定跟不上新增工具。改成不枚举的描述：

```python
"""内置工具声明集合：文件读写、受限 Python 执行、知识检索、联网访问、
技能读取、用户交互（问答/交付）与后台任务。

工具通过 ctx.extra 取宿主注入的通道（ask_user_handler / send_file_handler /
job_handler 等）；缺少对应通道时工具 fail-closed 返回 no_handler。
"""
```

2. 若文件顶部尚未导入 `Callable`，补上（与包内其他模块统一用 `from typing import Callable`）。

然后在 `skill_read` 之后、`register_builtin_tools` 之前插入：

```python
def _job_handler(ctx: ToolContext) -> Callable | None:
    """取宿主注入的任务处理器。

    Args:
        ctx: 工具上下文。

    Returns:
        宿主注入的 handler；缺失或非可调用时返回 None（调用方据此 fail-closed）。
    """
    handler = ctx.extra.get("job_handler")
    return handler if callable(handler) else None


def _no_job_handler() -> ToolResult:
    """无任务处理器时的统一失败结果。"""
    return ToolResult(ok=False, content="当前运行环境不支持后台任务", error="no_handler")


@tool(
    name="job.submit",
    description=(
        "提交一个后台长任务（谱图解析、批量计算等耗时数分钟以上的作业）。\n"
        "注意：提交后立即返回任务 ID，任务完成时系统会自动通知你继续处理。"
        "不要重复提交同一请求，也不要在提交后反复调用 job.status 轮询——那样只会浪费步骤。\n"
        "任务类型与参数格式先用 skill.list / skill.read 查对应技能说明。"
    ),
    parameters={"type": "object", "properties": {
        "kind": {"type": "string",
                 "description": "任务类型，如 spec.nmr.forward。不确定时先用 skill.list 找相关技能、"
                                "再用 skill.read 读其说明，拿到准确的 kind 与参数格式"},
        "params": {"type": "object",
                   "description": "任务参数对象，字段随 kind 而定（格式见对应技能的说明）"},
        "label": {"type": "string", "description": "任务简述，用于向用户展示（可选）"},
    }, "required": ["kind", "params"]},
    timeout_s=30,  # 只覆盖"提交"这一次请求；任务本身在后台跑
)
async def job_submit(ctx: ToolContext, args: dict) -> ToolResult:
    """提交后台任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_job_handler()
    kind = str(args.get("kind") or "").strip()
    if not kind:
        return ToolResult(ok=False, content="kind 不能为空", error="invalid_arguments")
    params = args.get("params")
    if not isinstance(params, dict):
        return ToolResult(ok=False, content="params 必须是 JSON 对象", error="invalid_arguments")
    return await handler({
        "action": "submit",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "kind": kind,
        "params": params,
        "label": str(args.get("label") or "")[:200],
    })


@tool(
    name="job.status",
    description=(
        "查询一个后台任务的当前状态与结果。仅在用户主动询问进度、"
        "或任务完成通知里缺少必要信息时使用——不要在提交后反复轮询。"
    ),
    parameters={"type": "object", "properties": {
        "job_id": {"type": "string", "description": "任务 ID（job.submit 返回的）"},
    }, "required": ["job_id"]},
    timeout_s=60,
)
async def job_status(ctx: ToolContext, args: dict) -> ToolResult:
    """查询单个任务状态（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_job_handler()
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return ToolResult(ok=False, content="job_id 不能为空", error="invalid_arguments")
    return await handler({
        "action": "status",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "job_id": job_id,
    })


@tool(
    name="job.list",
    description="列出本会话提交过的后台任务（含状态与结果摘要），用于汇报整体进度。",
    parameters={"type": "object", "properties": {}, "required": []},
    timeout_s=30,
)
async def job_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出本会话的任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_job_handler()
    return await handler({
        "action": "list",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
    })


@tool(
    name="job.cancel",
    description="取消一个尚未完成的后台任务（已结束的任务取消无效果）。",
    parameters={"type": "object", "properties": {
        "job_id": {"type": "string", "description": "任务 ID"},
    }, "required": ["job_id"]},
    timeout_s=60,
)
async def job_cancel(ctx: ToolContext, args: dict) -> ToolResult:
    """取消任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_job_handler()
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return ToolResult(ok=False, content="job_id 不能为空", error="invalid_arguments")
    return await handler({
        "action": "cancel",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "job_id": job_id,
    })
```

- [ ] **Step 4: 注册到内置工具表**

修改同文件末尾的 `register_builtin_tools`：

```python
def register_builtin_tools(registry: ToolRegistry) -> None:
    """把全部内置工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    for fn in (
        file_read, file_write, file_list, python_run, file_read_image,
        knowledge_list, knowledge_search, web_search, web_fetch, http_request,
        ask_user, file_send, skill_list, skill_read,
        job_submit, job_status, job_list, job_cancel,
    ):
        registry.register(fn)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest tests/test_builtin.py -v
```
Expected: 全绿（含 3 个新测试）

- [ ] **Step 6: 全量回归**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v
```
Expected: 全绿（新增工具不应影响既有用例）

- [ ] **Step 7: 提交**

```bash
git add packages/synlys-harness/src/synlys_harness/tools/builtin.py packages/synlys-harness/tests/test_builtin.py
git commit -m "harness 新增 job.submit/status/list/cancel 四个内置工具

- 经 ctx.extra[job_handler] 转发宿主，缺处理器时 fail-closed
- submit 前置校验 kind/params，避免空参数误提交"
```

---

## Task 3: 宿主 jobs 集合与 JobRepo

**Files:**
- Modify: `apps/web/backend/app/db/store.py`
- Modify: `apps/web/backend/app/db/repos.py`
- Test: `apps/web/backend/tests/test_repos.py`

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_repos.py` 末尾追加：

```python
async def test_job_repo_create_and_list_by_session(store):
    """JobRepo 建档补 _id/created_at，且能按 session_id 过滤列举。"""
    repo = JobRepo(store)
    a = await repo.create({
        "kind": "spec.nmr.forward", "status": "pending",
        "session_id": "s1", "user_id": "u1", "external_id": "e1",
    })
    assert a["_id"]
    assert a["created_at"] > 0
    await repo.create({
        "kind": "spec.nmr.forward", "status": "pending",
        "session_id": "s2", "user_id": "u1", "external_id": "e2",
    })
    only_s1 = await repo.list(filters={"session_id": "s1"})
    assert [d["_id"] for d in only_s1] == [a["_id"]]


async def test_job_repo_list_by_user_includes_all_statuses(store):
    """按 user_id 过滤不受状态影响（服务层不做服务端状态筛选）。"""
    repo = JobRepo(store)
    await repo.create({"kind": "k", "status": "running", "session_id": "s1",
                       "user_id": "u9", "external_id": "e1"})
    await repo.create({"kind": "k", "status": "completed", "session_id": "s1",
                       "user_id": "u9", "external_id": "e2"})
    docs = await repo.list(filters={"user_id": "u9"})
    assert {d["external_id"] for d in docs} == {"e1", "e2"}


async def test_job_repo_update_status(store):
    """状态更新走 BaseRepo.update，自动刷新 updated_at。"""
    repo = JobRepo(store)
    job = await repo.create({"kind": "k", "status": "pending", "session_id": "s1",
                             "user_id": "u1", "external_id": "e1"})
    updated = await repo.update(job["_id"], {"status": "running"})
    assert updated["status"] == "running"
    assert updated["updated_at"] >= job["updated_at"]
```

> 用 `>=` 而非严格 `>`：与同文件既有 `test_repo_autofill_and_update_touch` 的范式一致，
> 不依赖墙钟前进（严格 `>` 在时钟粒度粗的平台上会 flaky）。
> 同时确保已 import `JobRepo`（加到既有 `from app.db.repos import ...` 行）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_repos.py -v
```
Expected: FAIL — `ImportError: cannot import name 'JobRepo'`

- [ ] **Step 3: 加集合与索引**

修改 `apps/web/backend/app/db/store.py` 的 `COLLECTION_INDEXES`，在 `user_capabilities` 之后加一行：

```python
    "jobs": ["session_id", "user_id"],  # 后台任务：按会话/用户检索（状态筛选在服务层，不走 store）
```

> 为什么不提 `status`：全链路没有任何一处按状态过滤（`list_active()` 是全表拉取后
> 在 Python 侧判活跃态——sqlite 的 `filters` 只支持单值等值，多状态查询走不通），
> 而索引列一旦有存量数据就只能加不能改（`CREATE TABLE IF NOT EXISTS` 对已存在的表
> 是 no-op），所以趁集合还空着就不加。同理不提 `created_at`/`updated_at`：sqlite 的
> 索引列一律以 TEXT 落地，按时间排序会退化成字典序，排序只能在 Python 侧做。

- [ ] **Step 4: 加 JobRepo**

在 `apps/web/backend/app/db/repos.py` 末尾追加：

```python
class JobRepo(BaseRepo):
    """后台任务记录（kind/plugin_id/status/session_id/user_id/external_id）。"""

    collection = "jobs"
```

> 说明：状态过滤不走 store 的 `filters`（sqlite 后端为单值等值匹配），
> 活跃态筛选在 `JobService.list_active()` 里用 Python 侧判定，保持双后端一致。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_repos.py -v
```
Expected: 加上 3 个新用例后全绿

- [ ] **Step 6: 提交**

```bash
git add apps/web/backend/app/db/store.py apps/web/backend/app/db/repos.py apps/web/backend/tests/test_repos.py
git commit -m "宿主新增 jobs 集合与 JobRepo

- jobs 索引列 session_id/user_id（状态筛选在服务层，不走 store）
- 活跃态筛选交给服务层做，保持 sqlite/mongodb 口径一致"
```

---

## Task 4: JobConnector 协议、注册表与测试用连接器

**Files:**
- Create: `apps/web/backend/app/services/job_connectors.py`
- Test: `apps/web/backend/tests/test_job_service.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_job_service.py`：

```python
"""JobService / JobConnector 单测。"""
import pytest

from app.services.job_connectors import (
    JobConnectorRegistry,
    JobConnectorError,
    JobSubmitFailed,
    make_fake_connector,
)
from synlys_harness import JobStatus


async def test_registry_register_and_get():
    """注册后可按 kind 取回连接器与状态映射。"""
    reg = JobConnectorRegistry()
    conn = make_fake_connector("spec.nmr.forward", plugin_id="spec_agent")
    reg.register(conn, status_map={"queued": JobStatus.PENDING,
                                   "doing": JobStatus.RUNNING,
                                   "done": JobStatus.COMPLETED})
    got = reg.get("spec.nmr.forward")
    assert got is not None
    assert got.connector.plugin_id == "spec_agent"
    assert got.map_status("doing") is JobStatus.RUNNING


async def test_registry_duplicate_kind_rejected():
    """同一 kind 重复注册抛错（防两个插件抢同名任务类型）。"""
    reg = JobConnectorRegistry()
    reg.register(make_fake_connector("k", plugin_id="p1"), status_map={})
    with pytest.raises(ValueError):
        reg.register(make_fake_connector("k", plugin_id="p2"), status_map={})


async def test_registry_unknown_status_keeps_none():
    """未映射的外部状态返回 None（调用方保持原状态，不倒退）。"""
    reg = JobConnectorRegistry()
    reg.register(make_fake_connector("k", plugin_id="p1"),
                 status_map={"done": JobStatus.COMPLETED})
    assert reg.get("k").map_status("weird") is None


async def test_fake_connector_lifecycle():
    """测试连接器：submit 返回外部 id，poll 按脚本推进状态，cancel 生效。"""
    conn = make_fake_connector("k", plugin_id="p1", script=["queued", "doing", "done"])
    external_id = await conn.submit({"x": 1}, ctx={})
    assert external_id
    assert await conn.poll(external_id, ctx={}) == "queued"
    assert await conn.poll(external_id, ctx={}) == "doing"
    assert await conn.poll(external_id, ctx={}) == "done"
    # 脚本走完后保持末态（幂等）
    assert await conn.poll(external_id, ctx={}) == "done"


async def test_fake_connector_submit_failure():
    """测试连接器可被配置为提交失败（验证服务层错误归一化）。"""
    conn = make_fake_connector("k", plugin_id="p1", fail_submit="上游拒绝")
    with pytest.raises(JobSubmitFailed):
        await conn.submit({}, ctx={})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.job_connectors'`

- [ ] **Step 3: 实现连接器协议与注册表**

创建 `apps/web/backend/app/services/job_connectors.py`：

```python
"""后台任务连接器：宿主与各子平台（Spec_Agent 等）之间的适配协议。

分工：宿主 JobService 管状态机与持久化；连接器只管"怎么跟某个外部系统
说话"——提交、查状态、取消，以及把外部状态原文翻译成 harness 的统一状态。
插件在自己的包内实现连接器并在启动时注册（见 PluginService 扩展点）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from synlys_harness import JobStatus


class JobConnectorError(Exception):
    """连接器错误基类（宿主据此把任务标记为 failed 并向用户提示）。"""


class JobSubmitFailed(JobConnectorError):
    """提交被外部系统拒绝（参数非法、权限不足、服务不可用等）。"""


class JobPollFailed(JobConnectorError):
    """查询状态失败（网络/鉴权）。宿主保持原状态、下轮重试，不判失败。"""


@runtime_checkable
class JobConnector(Protocol):
    """子平台任务适配器。

    Attributes:
        kind: 任务类型（如 spec.nmr.forward），全局唯一。
        plugin_id: 归属插件 id（宿主据此在轮询时解析该插件配置）。
    """

    kind: str
    plugin_id: str

    async def submit(self, params: dict, ctx: dict) -> str:
        """提交任务。

        Args:
            params: 工具传入的参数对象。
            ctx: 宿主填充的调用上下文 {"config": 插件配置, "ai4ms_token": 用户凭证}。

        Returns:
            外部系统的任务 id。

        Raises:
            JobSubmitFailed: 提交失败。
        """
        ...

    async def poll(self, external_id: str, ctx: dict) -> str:
        """查询任务状态。

        Args:
            external_id: submit 返回的外部 id。
            ctx: 同 submit。

        Returns:
            外部系统的状态原文（由 status_map 翻译）。

        Raises:
            JobPollFailed: 查询失败（宿主保持原状态、下轮重试）。
        """
        ...

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """请求取消任务。

        Returns:
            外部系统是否受理取消；失败返回 False（宿主仍标记为 cancelled 由
            用户语义决定，见 JobService.cancel）。
        """
        ...


@dataclass
class RegisteredConnector:
    """注册表条目：连接器 + 状态映射表。"""

    connector: JobConnector
    status_map: dict[str, JobStatus] = field(default_factory=dict)

    def map_status(self, raw: str) -> JobStatus | None:
        """把外部状态原文映射为统一状态。

        Args:
            raw: 外部系统返回的状态字符串。

        Returns:
            统一状态；无映射（含 None/未预期值）返回 None，
            调用方应保持任务原状态，避免状态倒退。
        """
        if not isinstance(raw, str):
            return None
        return self.status_map.get(raw.strip().lower())


class JobConnectorRegistry:
    """kind → 连接器 的注册表（进程内单例，随应用启动装配）。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._items: dict[str, RegisteredConnector] = {}

    def register(self, connector: JobConnector,
                 status_map: dict[str, JobStatus] | None = None) -> None:
        """注册一个连接器。

        Args:
            connector: 连接器实例。
            status_map: 外部状态原文（小写）→ 统一状态。

        Raises:
            ValueError: kind 为空或已被注册。
        """
        kind = str(getattr(connector, "kind", "")).strip()
        if not kind:
            raise ValueError("连接器 kind 不能为空")
        if kind in self._items:
            raise ValueError(f"任务类型已注册: {kind}")
        normalized = {str(k).strip().lower(): v
                      for k, v in (status_map or {}).items()}
        self._items[kind] = RegisteredConnector(connector, normalized)

    def get(self, kind: str) -> RegisteredConnector | None:
        """按 kind 取注册条目（未注册返回 None，由调用方给出可读错误）。"""
        return self._items.get(kind)

    @property
    def kinds(self) -> list[str]:
        """已注册的任务类型（排序）。"""
        return sorted(self._items)


def _new_external_id() -> str:
    """生成测试用外部 id。"""
    return "fake-" + uuid.uuid4().hex[:8]


class FakeConnector:
    """测试/演示用连接器：纯内存，不依赖任何外部系统。

    状态按 script 顺序每次 poll 推进一步，推进到末位后保持不变。
    """

    def __init__(self, kind: str, plugin_id: str = "fake",
                 script: list[str] | None = None,
                 fail_submit: str = "") -> None:
        """初始化。

        Args:
            kind: 任务类型。
            plugin_id: 归属插件 id。
            script: 每次 poll 依次返回的状态原文；默认立即 done。
            fail_submit: 非空时 submit 抛 JobSubmitFailed（附该文案）。
        """
        self.kind = kind
        self.plugin_id = plugin_id
        self._script = list(script or ["done"])
        self._fail_submit = fail_submit
        self._cursor: dict[str, int] = {}
        self._cancelled: set[str] = set()
        self.submitted: list[dict] = []  # 断言用：记录收到的提交参数

    async def submit(self, params: dict, ctx: dict) -> str:
        """记录参数并返回假 external_id。"""
        if self._fail_submit:
            raise JobSubmitFailed(self._fail_submit)
        external_id = _new_external_id()
        self._cursor[external_id] = 0
        self.submitted.append({"params": dict(params), "ctx": dict(ctx)})
        return external_id

    async def poll(self, external_id: str, ctx: dict) -> str:
        """按下标推进并返回当前状态原文。"""
        del ctx  # 测试连接器不读上下文
        if external_id in self._cancelled:
            return "cancelled"
        index = self._cursor.get(external_id, len(self._script) - 1)
        raw = self._script[min(index, len(self._script) - 1)]
        self._cursor[external_id] = index + 1
        return raw

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """标记取消（poll 随即返回 cancelled）。"""
        del ctx
        self._cancelled.add(external_id)
        return True


def make_fake_connector(kind: str, plugin_id: str = "fake",
                        script: list[str] | None = None,
                        fail_submit: str = "") -> FakeConnector:
    """构造测试用连接器（供测试与本地演示）。

    Args:
        kind: 任务类型。
        plugin_id: 归属插件 id。
        script: poll 状态脚本。
        fail_submit: 非空时提交必失败。

    Returns:
        FakeConnector 实例。
    """
    return FakeConnector(kind, plugin_id=plugin_id, script=script,
                         fail_submit=fail_submit)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/job_connectors.py apps/web/backend/tests/test_job_service.py
git commit -m "新增任务连接器协议与注册表

- JobConnector 协议：submit/poll/cancel + 状态原文映射
- 注册表按 kind 唯一，未映射状态返回 None（保持原状态不倒退）
- FakeConnector 供测试与本地演示，C1 可独立端到端验收"
```

---

## Task 5: JobService 骨架（提交与查询）

**Files:**
- Create: `apps/web/backend/app/services/job_service.py`
- Test: `apps/web/backend/tests/test_job_service.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_job_service.py` 末尾追加：

```python
from app.db.repos import JobRepo
from app.services.job_service import JobService


def _job_service(store, **kwargs):
    """构造最小可用的 JobService（无插件/身份服务，走 FakeConnector）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg, **kwargs)
    return service, reg


async def test_submit_creates_pending_job(store):
    """提交后落库一条 pending 任务，内容含外部 id 与摘要。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map={"queued": JobStatus.PENDING, "done": JobStatus.COMPLETED})
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {"a": 1}, "label": "试算"},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is True
    assert "任务 ID" in result.content
    job_id = result.data["job_id"]
    doc = await service.get(job_id)
    assert doc["status"] == "pending"
    assert doc["session_id"] == "s1"
    assert doc["user_id"] == "u1"
    assert doc["external_id"].startswith("fake-")


async def test_submit_unknown_kind_is_readable_error(store):
    """未注册的任务类型给出可读错误，不抛异常。"""
    service, _ = _job_service(store)
    result = await service.handle(
        {"action": "submit", "kind": "nope", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "unknown_job_kind"
    assert "nope" in result.content


async def test_submit_passes_plugin_config_and_token(store):
    """提交时把该插件的配置与用户代签 token 交给连接器。"""
    service, reg = _job_service(store)
    conn = make_fake_connector("k", plugin_id="spec_agent", script=["queued"])
    reg.register(conn, status_map={"queued": JobStatus.PENDING})
    await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"plugins": {"spec_agent": {"base_url": "http://x"}},
                   "ai4ms_token": "tok-1"})
    assert conn.submitted[0]["ctx"]["config"] == {"base_url": "http://x"}
    assert conn.submitted[0]["ctx"]["ai4ms_token"] == "tok-1"


async def test_submit_failure_maps_to_tool_error(store):
    """连接器提交失败 → ok=False 且错误码可辨识（不落库半成品）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", fail_submit="上游拒绝"))
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "submit_failed"
    assert "上游拒绝" in result.content
    assert await service.list_for_session("s1") == []


async def test_status_and_list_render_summary(store):
    """status/list 返回人类可读摘要，含任务 ID 与状态。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued", "done"]),
                 status_map={"queued": JobStatus.PENDING, "done": JobStatus.COMPLETED})
    submitted = await service.handle(
        {"action": "submit", "kind": "k", "params": {}, "label": "试算"},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    one = await service.handle({"action": "status", "job_id": job_id},
                               user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert one.ok is True and job_id in one.content
    many = await service.handle({"action": "list"},
                                user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert many.ok is True and "试算" in many.content


async def test_status_of_foreign_job_is_not_visible(store):
    """别人的任务查不到（按 user_id 校验，不泄露存在性）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]))
    submitted = await service.handle(
        {"action": "submit", "kind": "k", "params": {}},
        user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    other = await service.handle({"action": "status", "job_id": job_id},
                                 user={"sub": "u2"}, session_id="s2", ctx_extra={})
    assert other.ok is False and other.error == "not_found"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.job_service'`

- [ ] **Step 3: 实现 JobService 骨架**

创建 `apps/web/backend/app/services/job_service.py`：

```python
"""后台任务服务：提交、查询、取消、状态流转与完成唤醒。

职责边界：本服务只认 JobConnector 协议（怎么跟外部系统说话）与 JobRepo
（任务文档在哪），不认识任何具体子平台。任务完成后的"唤醒 agent 继续处理"
通过构造期注入的 wake 回调完成（见 set_wake_callback）。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from synlys_harness import (
    ACTIVE_STATUSES,
    JobStatus,
    ToolResult,
    can_transition,
    is_terminal,
)

from app.services.job_connectors import (
    JobConnectorRegistry,
    JobPollFailed,
    JobSubmitFailed,
)

_LOGGER = logging.getLogger(__name__)

# 摘要文本里注入给 LLM 的结果上限（超出截断，完整结果可由外部系统/后续工具取）
RESULT_PREVIEW_CHARS = 4000

WakeCallback = Callable[[str, str, str], Awaitable[None]]
"""唤醒回调签名：(session_id, text, job_id) -> None。"""


def _new_job_id() -> str:
    """生成任务 id。"""
    return "job-" + uuid.uuid4().hex[:12]


def _clip(text: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    """截断长文本并标注。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字）"


class JobService:
    """后台任务编排（单例，挂 app.state.job_service）。"""

    def __init__(self, repo: Any, connectors: JobConnectorRegistry,
                 plugin_config_store: Any = None,
                 ai4ms_identity: Any = None) -> None:
        """保存依赖。

        Args:
            repo: JobRepo（任务文档读写）。
            connectors: 连接器注册表（kind → 连接器）。
            plugin_config_store: 插件配置存储（轮询时按 job 的 user_id 重新解析
                配置；None 时轮询只用空配置）。
            ai4ms_identity: AI⁴MS 代签服务（轮询时重建用户凭证；None 时为空）。
        """
        self._repo = repo
        self._connectors = connectors
        self._plugin_config_store = plugin_config_store
        self._ai4ms_identity = ai4ms_identity
        self._wake: WakeCallback | None = None
        # 待唤醒会话队列：会话忙时任务先在这里排队，run 结束后 drain
        self._pending_wake: dict[str, list[str]] = {}

    def set_wake_callback(self, callback: WakeCallback) -> None:
        """注入唤醒回调（由应用装配阶段调用，构建期无法拿到 AgentService）。"""
        self._wake = callback

    # ---------- 查询 ----------

    async def get(self, job_id: str) -> dict | None:
        """按 id 取任务文档。"""
        return await self._repo.get(job_id)

    async def list_for_session(self, session_id: str) -> list[dict]:
        """列出某会话的全部任务（创建时间升序）。"""
        docs = await self._repo.list(filters={"session_id": session_id})
        return sorted(docs, key=lambda d: float(d.get("created_at") or 0))

    async def list_active(self) -> list[dict]:
        """列出全部未完成任务（轮询入口；终态任务不再纳入）。"""
        active_values = {s.value for s in ACTIVE_STATUSES}
        docs = await self._repo.list()
        return [d for d in docs if d.get("status") in active_values]

    # ---------- 配置解析（提交/轮询共用） ----------

    async def _ctx_for(self, user_id: str, plugin_id: str,
                       ctx_extra: dict | None = None) -> dict:
        """构造连接器调用上下文。

        Args:
            user_id: 任务归属用户（轮询路径用它重新解析配置）。
            plugin_id: 归属插件 id。
            ctx_extra: 提交路径可直接给出的运行上下文（含 plugins/ai4ms_token）；
                为 None 时（轮询路径）从插件配置存储与代签服务重建。

        Returns:
            {"config": {插件配置}, "ai4ms_token": "<token 或空串>"}。
        """
        if ctx_extra is not None:
            plugins = ctx_extra.get("plugins") or {}
            return {
                "config": dict(plugins.get(plugin_id) or {}),
                "ai4ms_token": str(ctx_extra.get("ai4ms_token") or ""),
            }
        config: dict = {}
        if self._plugin_config_store is not None:
            try:
                config = await self._plugin_config_store.resolved_for_user(
                    user_id, plugin_id)
            except Exception:  # noqa: BLE001 解密失败等：按无配置处理，任务照常轮询
                _LOGGER.warning("轮询时解析插件配置失败 plugin=%s user=%s",
                                plugin_id, user_id, exc_info=True)
        return {"config": config, "ai4ms_token": ""}

    # ---------- 工具入口 ----------

    async def handle(self, payload: dict, *, user: dict, session_id: str,
                     ctx_extra: dict) -> ToolResult:
        """job_handler 实现：按 action 分发（宿主装配时注入 ctx.extra）。

        Args:
            payload: 工具传来的负载（action/kind/params/label/job_id...）。
            user: 当前用户 payload。
            session_id: 当前会话 id。
            ctx_extra: 本轮运行的工具上下文 extra（取插件配置与代签 token）。

        Returns:
            工具结果。
        """
        action = str(payload.get("action", ""))
        if action == "submit":
            return await self.submit(
                session_id=session_id, user_id=str(user["sub"]),
                kind=str(payload.get("kind", "")),
                params=payload.get("params") or {},
                label=str(payload.get("label", "")), ctx_extra=ctx_extra)
        if action == "status":
            return await self.describe(str(payload.get("job_id", "")),
                                       user_id=str(user["sub"]), refresh=True)
        if action == "list":
            return await self.render_list(session_id, user_id=str(user["sub"]))
        if action == "cancel":
            return await self.cancel(str(payload.get("job_id", "")),
                                     user_id=str(user["sub"]))
        return ToolResult(ok=False, content=f"未知的任务操作: {action}",
                          error="invalid_arguments")

    async def submit(self, *, session_id: str, user_id: str, kind: str,
                     params: dict, label: str, ctx_extra: dict) -> ToolResult:
        """提交任务：调连接器 → 落库 pending → 立即返回（不等待任务完成）。

        Args:
            session_id: 所属会话。
            user_id: 所属用户。
            kind: 任务类型。
            params: 任务参数。
            label: 任务简述（给用户看）。
            ctx_extra: 本轮运行上下文（取插件配置与代签 token）。

        Returns:
            ok=True 且 data["job_id"]；失败时错误码为 unknown_job_kind /
            submit_failed。
        """
        registered = self._connectors.get(kind)
        if registered is None:
            kinds = "、".join(self._connectors.kinds) or "（当前无可用的任务类型）"
            return ToolResult(
                ok=False,
                content=f"未注册的任务类型: {kind}。可用类型：{kinds}",
                error="unknown_job_kind")
        connector = registered.connector
        ctx = await self._ctx_for(user_id, connector.plugin_id, ctx_extra)
        try:
            external_id = await connector.submit(params, ctx)
        except JobSubmitFailed as exc:
            return ToolResult(ok=False, content=f"任务提交失败: {exc}",
                              error="submit_failed")
        except Exception as exc:  # noqa: BLE001 连接器未归一化的异常也要对 LLM 可见
            _LOGGER.warning("任务提交异常 kind=%s", kind, exc_info=True)
            return ToolResult(ok=False, content=f"任务提交出错: {exc}",
                              error="submit_failed")
        doc = await self._repo.create({
            "_id": _new_job_id(),
            "kind": kind,
            "plugin_id": connector.plugin_id,
            "status": JobStatus.PENDING.value,
            "session_id": session_id,
            "user_id": user_id,
            "external_id": str(external_id),
            "label": label,
            "params": params,
            "result": "",
            "error": "",
        })
        return ToolResult(
            ok=True,
            content=(f"已提交后台任务「{label or kind}」，任务 ID: {doc['_id']}。"
                     "任务在后台执行，完成时系统会自动通知你继续处理；"
                     "现在不要重复提交，也不必轮询状态。"),
            data={"job_id": doc["_id"], "status": doc["status"]})

    async def describe(self, job_id: str, *, user_id: str,
                       refresh: bool = False) -> ToolResult:
        """查单个任务（可选先同步刷新一次状态，让用户看到最新进度）。

        Args:
            job_id: 任务 id。
            user_id: 调用者（非本人一律 not_found，不泄露存在性）。
            refresh: 是否先向外部系统拉一次最新状态。

        Returns:
            工具结果；content 为任务摘要。
        """
        doc = await self._repo.get(job_id)
        if doc is None or str(doc.get("user_id")) != user_id:
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        if refresh and not is_terminal(JobStatus(doc["status"])):
            doc = await self.refresh(doc) or doc
        return ToolResult(ok=True, content=self._render(doc),
                          data={"job_id": doc["_id"], "status": doc["status"]})

    async def render_list(self, session_id: str, *, user_id: str) -> ToolResult:
        """列出本会话任务（供模型汇报整体进度）。"""
        docs = [d for d in await self.list_for_session(session_id)
                if str(d.get("user_id")) == user_id]
        if not docs:
            return ToolResult(ok=True, content="本会话还没有提交过后台任务。",
                              data={"count": 0})
        return ToolResult(ok=True, content="\n".join(self._render(d) for d in docs),
                          data={"count": len(docs)})

    @staticmethod
    def _render(doc: dict) -> str:
        """任务文档 → 一行摘要（给 LLM 与用户看）。"""
        parts = [f"[{doc['_id']}] {doc.get('label') or doc.get('kind')}",
                 f"状态: {doc.get('status')}"]
        if doc.get("error"):
            parts.append(f"错误: {_clip(str(doc['error']), 400)}")
        if doc.get("result"):
            parts.append(f"结果: {_clip(str(doc['result']))}")
        return " | ".join(parts)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: 11 passed（Task 4 的 5 个 + 本任务的 6 个）

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/job_service.py apps/web/backend/tests/test_job_service.py
git commit -m "新增 JobService：任务提交与查询

- submit 调连接器后落库 pending 并立即返回 job_id（不阻塞 step）
- status/list 输出人类可读摘要；跨用户查询一律 not_found
- 连接器异常归一化为可读工具错误，不落半成品记录"
```

---

## Task 6: JobService 状态流转、取消与刷新

**Files:**
- Modify: `apps/web/backend/app/services/job_service.py`
- Test: `apps/web/backend/tests/test_job_service.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_job_service.py`：

```python
async def test_refresh_advances_status_via_status_map(store):
    """刷新把外部状态原文映射为统一状态并落库。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued", "doing", "done"]),
                 status_map={"queued": JobStatus.PENDING, "doing": JobStatus.RUNNING,
                             "done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    doc = await service.get(job_id)
    doc = await service.refresh(doc)
    assert doc["status"] in ("pending", "running", "completed")
    # 连推三次必然到 completed
    for _ in range(3):
        doc = await service.refresh(await service.get(job_id))
    assert doc["status"] == "completed"


async def test_refresh_keeps_status_on_poll_failure(store):
    """查询失败时保持原状态并累计失败计数（不把抖动判成失败）。"""
    class FlakyConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            raise JobPollFailed("网络不可达")

    service, reg = _job_service(store)
    reg.register(FlakyConnector("k", plugin_id="p1", script=["queued"]),
                 status_map={"queued": JobStatus.PENDING, "done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    before = await service.get(submitted.data["job_id"])
    after = await service.refresh(before)
    assert after["status"] == before["status"]
    assert int(after.get("poll_failures") or 0) == 1


async def test_refresh_ignores_unmapped_status(store):
    """未映射的外部状态不改变任务状态（防状态倒退）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["weird"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "pending"


async def test_terminal_job_is_not_refreshed(store):
    """终态任务不再请求外部系统（避免无谓调用）。"""
    calls: list[str] = []

    class CountingConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            calls.append(external_id)
            return await super().poll(external_id, ctx)

    service, reg = _job_service(store)
    reg.register(CountingConnector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    doc = await service.refresh(await service.get(submitted.data["job_id"]))
    assert doc["status"] == "completed"
    calls.clear()
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert calls == []


async def test_cancel_marks_cancelled(store):
    """取消：调连接器 cancel 并把状态置为 cancelled。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["queued"]),
                 status_map={"queued": JobStatus.PENDING, "done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    result = await service.handle({"action": "cancel",
                                   "job_id": submitted.data["job_id"]},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is True
    doc = await service.get(submitted.data["job_id"])
    assert doc["status"] == "cancelled"


async def test_cancel_terminal_job_is_rejected(store):
    """已结束的任务不能再取消（给可读提示）。"""
    service, reg = _job_service(store)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    result = await service.handle({"action": "cancel",
                                   "job_id": submitted.data["job_id"]},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    assert result.ok is False
    assert result.error == "already_finished"
```

> 测试里用到 `FakeConnector`，请在文件头补 `from app.services.job_connectors import FakeConnector`。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: FAIL — `AttributeError: 'JobService' object has no attribute 'refresh'`

- [ ] **Step 3: 实现 refresh / cancel**

在 `apps/web/backend/app/services/job_service.py` 的 `_render` 之前插入：

```python
    async def refresh(self, doc: dict) -> dict | None:
        """向外部系统拉一次最新状态并落库（轮询与手工查询共用）。

        Args:
            doc: 任务文档。

        Returns:
            更新后的文档；任务不存在返回 None。

        Note:
            查询失败或状态未映射时**保持原状态**并累计 poll_failures——
            一次网络抖动不得把任务判为失败，也不得让状态倒退。
        """
        try:
            status = JobStatus(doc["status"])
        except (KeyError, ValueError):
            return doc
        if is_terminal(status):
            return doc
        registered = self._connectors.get(str(doc.get("kind", "")))
        if registered is None:
            # 连接器消失（插件被卸载）：任务无法继续跟踪，标记失败并说明原因
            return await self._repo.update(doc["_id"], {
                "status": JobStatus.FAILED.value,
                "error": f"任务类型已不可用: {doc.get('kind')}",
            })
        ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                  registered.connector.plugin_id)
        try:
            raw = await registered.connector.poll(str(doc.get("external_id", "")), ctx)
        except JobPollFailed as exc:
            return await self._repo.update(doc["_id"], {
                "poll_failures": int(doc.get("poll_failures") or 0) + 1,
                "last_poll_error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001 未归一化异常同样只记不改状态
            _LOGGER.warning("任务轮询异常 job=%s", doc.get("_id"), exc_info=True)
            return await self._repo.update(doc["_id"], {
                "poll_failures": int(doc.get("poll_failures") or 0) + 1,
                "last_poll_error": str(exc),
            })
        mapped = registered.map_status(raw)
        if mapped is None:
            # 外部状态不在映射表内：保持原状态（并记录原文，便于补映射）
            return await self._repo.update(doc["_id"], {"last_raw_status": str(raw)})
        if not can_transition(status, mapped):
            _LOGGER.warning("任务状态非法流转 job=%s %s -> %s",
                            doc.get("_id"), status, mapped)
            return doc
        if mapped is status:
            return await self._repo.update(doc["_id"], {"last_raw_status": str(raw)})
        return await self._repo.update(doc["_id"], {
            "status": mapped.value, "last_raw_status": str(raw)})

    async def cancel(self, job_id: str, *, user_id: str) -> ToolResult:
        """取消任务（已结束的任务拒绝并说明）。

        Args:
            job_id: 任务 id。
            user_id: 调用者（非本人 not_found）。

        Returns:
            工具结果。
        """
        doc = await self._repo.get(job_id)
        if doc is None or str(doc.get("user_id")) != user_id:
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        status = JobStatus(doc["status"])
        if is_terminal(status):
            return ToolResult(
                ok=False,
                content=f"任务已结束（{status.value}），无需取消。",
                error="already_finished")
        registered = self._connectors.get(str(doc.get("kind", "")))
        accepted = False
        if registered is not None:
            ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                      registered.connector.plugin_id)
            try:
                accepted = bool(await registered.connector.cancel(
                    str(doc.get("external_id", "")), ctx))
            except Exception:  # noqa: BLE001 取消失败不阻断本地状态收敛
                _LOGGER.warning("任务取消失败 job=%s", job_id, exc_info=True)
        await self._repo.update(job_id, {
            "status": JobStatus.CANCELLED.value,
            "cancel_accepted": accepted,
            "ended_at": time.time(),
        })
        note = "已请求取消" if accepted else "已标记取消（外部系统未确认）"
        return ToolResult(ok=True, content=f"任务 {job_id} {note}。",
                          data={"job_id": job_id, "status": "cancelled"})
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: 17 passed

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/job_service.py apps/web/backend/tests/test_job_service.py
git commit -m "JobService 补状态流转与取消

- refresh 映射外部状态并校验合法流转，终态不再轮询
- 查询失败只累计计数不改状态（防网络抖动误判失败）
- cancel 对终态任务返回 already_finished；连接器取消失败仍收敛本地状态"
```

---

## Task 7: 会话运行装配解析抽取（为唤醒铺路）

**Files:**
- Create: `apps/web/backend/app/services/session_runtime.py`
- Modify: `apps/web/backend/app/api/sessions_api.py:398-447`
- Test: `apps/web/backend/tests/test_session_runtime.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_session_runtime.py`：

```python
"""会话运行装配解析单测（发消息与任务唤醒共用同一口径）。"""
import pytest
from fastapi import HTTPException

from app.services import workspace
from app.services.session_runtime import resolve_session_runtime


class _State:
    """最小 app.state 替身：只带解析用得到的服务。"""

    def __init__(self, settings, project_service):
        self.settings = settings
        self.project_service = project_service


async def test_unbound_session_uses_session_workspace(store, settings, project_service):
    """未绑定项目的会话：工作根 = sessions/{sid}，归属为 session_id。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    await repos.provider.create({"name": "p", "base_url": "http://x",
                                 "model_id": "m", "enabled": True})
    provider_id = (await repos.provider.list())[0]["_id"]
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider_id, "project_id": None,
    })
    runtime = await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert runtime.workspace_root == workspace.session_root(
        settings.data_root, "u1", doc["_id"])
    assert runtime.assistant is None


async def test_stale_project_binding_is_cleared(store, settings, project_service):
    """绑定已失效（项目被删）时回落会话工作根，并清掉脏 project_id。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    await repos.provider.create({"name": "p", "base_url": "http://x",
                                 "model_id": "m", "enabled": True})
    provider_id = (await repos.provider.list())[0]["_id"]
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": provider_id, "project_id": "gone",
    })
    runtime = await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert runtime.ownership == {"session_id": doc["_id"]}
    assert (await repos.session.get(doc["_id"]))["project_id"] is None


async def test_missing_provider_raises_422(store, settings, project_service):
    """无可用模型服务时抛 422（唤醒路径据此放弃本轮并告警）。"""
    state = _State(settings, project_service)
    repos = _make_repos(store)
    doc = await repos.session.create({
        "user_id": "u1", "assistant_id": None, "title": "t",
        "model_provider_id": None, "project_id": None,
    })
    with pytest.raises(HTTPException) as exc:
        await resolve_session_runtime(state, repos, doc, {"sub": "u1"})
    assert exc.value.status_code == 422
```

> `store` fixture 由 `conftest.py` 提供（参数化 sqlite/mongodb）；`settings` 与
> `project_service` 在本文件自建。`_make_repos` 直接复用 `deps` 的 `Repos` 结构，
> 与生产代码同形——写成 `SimpleNamespace` 会让"解析函数依赖哪些 repo"这件事失真：

```python
from types import SimpleNamespace

import pytest

from app.api.deps import Repos
from app.db.repos import (
    AssistantRepo,
    EventRepo,
    FileRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
)
from app.services.project_service import ProjectService


def _make_repos(store):
    """构造解析函数需要的 repo 组合（与 deps.get_repos 同结构）。"""
    return Repos(
        provider=ProviderRepo(store, fernet_key=""),
        assistant=AssistantRepo(store),
        session=SessionRepo(store),
        run=RunRepo(store),
        file=FileRepo(store),
        event=EventRepo(store),
    )


@pytest.fixture
def settings(tmp_path):
    """最小 Settings 替身（解析函数只用到 data_root）。"""
    return SimpleNamespace(data_root=str(tmp_path))


@pytest.fixture
def project_service(store, tmp_path):
    """项目服务（同一 store 与临时数据根）。"""
    return ProjectService(store, str(tmp_path))
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_session_runtime.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.session_runtime'`

- [ ] **Step 3: 实现解析函数**

创建 `apps/web/backend/app/services/session_runtime.py`：

```python
"""会话运行装配解析：把会话文档解析成"跑一轮对话"所需的全部参数。

发消息（sessions_api.send_message）与任务完成唤醒（AgentService.wake）必须
用同一份解析口径——否则两条路径会跑在不同的工作根、或挑到不同的模型。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from synlys_harness import ModelProviderConfig

from app.services import workspace

_LOGGER = logging.getLogger(__name__)


@dataclass
class SessionRuntime:
    """一轮对话的运行装配结果。

    Attributes:
        assistant: 助手文档（未选/已删为 None，走无 persona 路径）。
        provider_cfg: 已解密的模型服务配置。
        workspace_root: 工作根（绑定项目 = 项目目录；否则会话目录）。
        ownership: 文件记录归属（{"project_id": pid} 或 {"session_id": sid}）。
    """

    assistant: dict | None
    provider_cfg: ModelProviderConfig
    workspace_root: Path
    ownership: dict


async def _resolve_provider(provider_id: str | None, owner_desc: str,
                            repos: Any) -> ModelProviderConfig:
    """解析模型服务 id 为后端配置（解密 api_key）。

    Args:
        provider_id: 模型服务 id。
        owner_desc: 归属描述（未指定 id 时的 422 提示用）。
        repos: repo 集中访问对象。

    Returns:
        ModelProviderConfig。

    Raises:
        HTTPException: 未指定/不存在/已停用/解密失败（422）。
    """
    if not provider_id:
        raise HTTPException(422, f"未指定模型服务: {owner_desc}")
    try:
        decrypted = await repos.provider.get_decrypted(provider_id)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    if decrypted is None:
        raise HTTPException(422, f"模型服务不存在: {provider_id}")
    if not decrypted.get("enabled"):
        raise HTTPException(422, f"模型服务已停用: {decrypted.get('name', provider_id)}")
    return ModelProviderConfig(
        name=str(decrypted.get("name", "")),
        base_url=str(decrypted.get("base_url", "")),
        api_key=str(decrypted.get("api_key", "")),
        model_id=str(decrypted.get("model_id", "")),
        multimodal=bool(decrypted.get("multimodal")),
    )


async def resolve_session_runtime(state: Any, repos: Any, doc: dict,
                                  user: dict) -> SessionRuntime:
    """解析会话的运行装配（模型优先级：会话级覆盖 > 助手绑定 > 首个启用模型）。

    Args:
        state: app.state（取 settings 与 project_service）。
        repos: repo 集中访问对象。
        doc: 会话文档（调用方已校验归属）。
        user: 当前用户 payload。

    Returns:
        SessionRuntime。

    Raises:
        HTTPException: 无可用模型服务（422）。
    """
    assistant = await repos.assistant.get(doc.get("assistant_id") or "")
    pid = doc.get("model_provider_id") or (assistant or {}).get("model_provider_id")
    if not pid:
        enabled = [p for p in await repos.provider.list() if p.get("enabled")]
        if enabled:
            pid = enabled[0]["_id"]
    owner = doc.get("title") or (assistant or {}).get("name") or doc.get("_id", "")
    provider_cfg = await _resolve_provider(pid, str(owner), repos)

    project_service = state.project_service
    project = None
    if doc.get("project_id"):
        project = await project_service.get(user["sub"], str(doc["project_id"]))
    if project is not None:
        return SessionRuntime(
            assistant=assistant, provider_cfg=provider_cfg,
            workspace_root=project_service.root_for(project),
            ownership={"project_id": project["_id"]})
    workspace_root = workspace.session_root(
        state.settings.data_root, user["sub"], str(doc["_id"]))
    # 绑定失效（项目已删）：清掉脏 project_id，前端据此把它归入未分组区
    if doc.get("project_id"):
        await repos.session.update(doc["_id"], {"project_id": None})
    return SessionRuntime(
        assistant=assistant, provider_cfg=provider_cfg,
        workspace_root=workspace_root, ownership={"session_id": str(doc["_id"])})
```

- [ ] **Step 4: 让 sessions_api 复用它**

修改 `apps/web/backend/app/api/sessions_api.py`：
1. 删除本文件内的 `_resolve_provider` 函数（第 53-84 行整段）。
2. 在 `send_message` 中，把「模型优先级解析 + 工作根解析 + 失效绑定清理」整段（原第 398-431 行，即从 `doc = await _own_session(...)` 之后的 assistant/pid/cfg 计算起，到 `workspace_root`/`ownership` 确定为止）替换为：

```python
    runtime = await resolve_session_runtime(request.app.state, repos, doc, user)
    assistant = runtime.assistant
    cfg = runtime.provider_cfg
    workspace_root = runtime.workspace_root
    ownership = runtime.ownership
    service = _agent_service(request)
```

3. 在文件头补 import：

```python
from app.services.session_runtime import resolve_session_runtime
```

并删除不再使用的 import（若 `ModelProviderConfig` 与 `_validate_provider` 在本文件其他地方仍有使用则保留；`_validate_provider` 仍被 `create_session`/`update_session` 使用，保留）。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_session_runtime.py tests/test_chat_api.py tests/test_sessions_api.py -v
```
Expected: 全绿（含既有发消息链路回归）

- [ ] **Step 6: 提交**

```bash
git add apps/web/backend/app/services/session_runtime.py apps/web/backend/app/api/sessions_api.py apps/web/backend/tests/test_session_runtime.py
git commit -m "抽出会话运行装配解析（发消息与任务唤醒共用）

- resolve_session_runtime 统一模型优先级与工作根口径
- sessions_api 删掉本地重复实现，行为不变"
```

---

## Task 8: AgentService 空闲判定、唤醒与运行结束钩子

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Test: `apps/web/backend/tests/test_agent_wake.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_agent_wake.py`：

```python
"""AgentService 空闲判定 / 唤醒 / 运行结束回调单测。"""
import asyncio

from app.services.agent_service import AgentService


async def test_is_busy_reflects_active_sessions(agent_service, session_doc):
    """无 run 时空闲；占位后忙。"""
    service = agent_service
    assert service.is_busy(session_doc["_id"]) is False
    service._active_by_session.setdefault(session_doc["_id"], set()).add("r1")  # noqa: SLF001
    assert service.is_busy(session_doc["_id"]) is True


async def test_on_run_finished_hook_is_awaited(agent_service, session_doc):
    """运行结束钩子在 _drive 收尾时被调用（唤醒队列靠它 drain）。"""
    seen: list[str] = []

    async def hook(session_id: str) -> None:
        seen.append(session_id)

    agent_service.set_run_finished_hook(hook)
    # 直接驱动一次收尾路径：构造最小 run 并调用内部清理
    agent_service._active_by_session.setdefault(session_doc["_id"], set()).add("r9")  # noqa: SLF001
    await agent_service._notify_run_finished(session_doc["_id"])  # noqa: SLF001
    assert seen == [session_doc["_id"]]


async def test_wake_starts_run_with_system_text(agent_service, session_doc,
                                                store, tmp_path, monkeypatch):
    """wake 以系统通知文本起一轮新 run，并把 job_id 作为唤醒来源透传。"""
    captured: dict = {}

    async def fake_chat(session_id, user, assistant, cfg, text, **kwargs):
        captured.update({"session_id": session_id, "text": text, "kwargs": kwargs})
        return "run-1"

    async def fake_resolve(state, repos, doc, user):
        return SimpleNamespace(assistant=None, provider_cfg=object(),
                               workspace_root=tmp_path,
                               ownership={"session_id": doc["_id"]})

    monkeypatch.setattr(agent_service, "chat", fake_chat)
    monkeypatch.setattr("app.services.agent_service.resolve_session_runtime",
                        fake_resolve)
    agent_service.set_repos(_repos(store))
    agent_service._app_state = SimpleNamespace()  # noqa: SLF001 解析已被替换

    await agent_service.wake(session_doc["_id"], "任务完成通知", "job-1")
    assert captured["session_id"] == session_doc["_id"]
    assert captured["text"] == "任务完成通知"
    assert captured["kwargs"]["wake_source"] == {"job_id": "job-1"}
```

> 本文件自建 fixture（`conftest.py` 未提供 `agent_service` / `session_doc`；`store` 用现成的）：

```python
from types import SimpleNamespace

import pytest

from app.api.deps import Repos
from app.db.repos import (
    AssistantRepo,
    EventRepo,
    FileRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
)
from app.services.agent_service import AgentService
from app.services.skill_service import SkillService


def _repos(store):
    """与 deps.get_repos 同结构的 repo 组合。"""
    return Repos(
        provider=ProviderRepo(store, fernet_key=""),
        assistant=AssistantRepo(store),
        session=SessionRepo(store),
        run=RunRepo(store),
        file=FileRepo(store),
        event=EventRepo(store),
    )


@pytest.fixture
def agent_service(store, tmp_path):
    """最小 AgentService（不发真实 LLM：只测空闲判定 / 唤醒接线 / 结束回调）。"""
    settings = SimpleNamespace(data_root=str(tmp_path), allowed_hosts=[])
    return AgentService(store, settings, EventRepo(store), SkillService(str(tmp_path)))


@pytest.fixture
async def session_doc(store):
    """一条属于 u1 的会话（唤醒路径读它取 user_id）。"""
    return await SessionRepo(store).create({
        "user_id": "u1", "assistant_id": None, "title": "t", "project_id": None,
    })
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v
```
Expected: FAIL — `AttributeError: 'AgentService' object has no attribute 'is_busy'`

- [ ] **Step 3: 实现三处改动**

在 `apps/web/backend/app/services/agent_service.py` 中：

**(a) 顶部 import 补 Repos 依赖**（用于唤醒时解析装配）：

```python
from app.services.session_runtime import resolve_session_runtime
```

**(b) `__init__` 中新增两个字段**（放在 `self._executor` 之后）：

```python
        # 运行结束回调（任务完成唤醒靠它 drain 待唤醒队列）
        self._on_run_finished: Any = None
        # 唤醒所需的 repo 组合（由装配阶段注入；为 None 时 wake 不可用）
        self._repos: Any = None
```

**(c) 新增公开方法**（放在 `cancel` 之前）：

```python
    def set_run_finished_hook(self, hook: Any) -> None:
        """注入运行结束回调（签名 async (session_id) -> None）。"""
        self._on_run_finished = hook

    def set_repos(self, repos: Any) -> None:
        """注入 repo 组合（唤醒路径解析会话装配用）。"""
        self._repos = repos

    def is_busy(self, session_id: str) -> bool:
        """该会话当前是否有进行中的 run。

        Args:
            session_id: 会话 id。

        Returns:
            有活跃 run 为 True。
        """
        return bool(self._active_by_session.get(session_id))

    async def _notify_run_finished(self, session_id: str) -> None:
        """运行收尾通知（内部；失败不向上抛，避免影响 run 清理）。

        Args:
            session_id: 会话 id。
        """
        hook = self._on_run_finished
        if hook is None:
            return
        try:
            await hook(session_id)
        except Exception:  # noqa: BLE001 回调失败不得影响 run 终态清理
            _LOGGER.warning("运行结束回调失败 session=%s", session_id, exc_info=True)

    async def wake(self, session_id: str, text: str, job_id: str = "") -> str:
        """以系统通知文本启动一轮新 run（后台任务完成后唤醒 agent）。

        Args:
            session_id: 会话 id。
            text: 注入的用户消息文本（系统生成的通知）。
            job_id: 关联任务 id（写入事件 payload 供前端渲染提示条）。

        Returns:
            新 run 的 run_id。

        Raises:
            TooManyRuns: 会话已有进行中的 run（调用方应改为排队）。
            HTTPException: 会话不存在/无可用模型（由解析函数抛出）。
        """
        if self._repos is None:
            raise RuntimeError("唤醒不可用：未注入 repos")
        doc = await self._repos.session.get(session_id)
        if doc is None:
            raise RuntimeError(f"唤醒失败：会话不存在 {session_id}")
        user = {"sub": str(doc["user_id"])}
        runtime = await resolve_session_runtime(
            self._app_state, self._repos, doc, user)
        return await self.chat(
            session_id, user, runtime.assistant, runtime.provider_cfg, text,
            workspace_root=runtime.workspace_root,
            file_ownership=runtime.ownership,
            enabled_plugins=doc.get("enabled_plugins"),
            wake_source={"job_id": job_id} if job_id else None)
```

**(d) `__init__` 增加 `app_state` 参数与 `self._app_state`**（唤醒解析需要 `settings` 与 `project_service`）：

签名改为：

```python
    def __init__(self, store: Any, settings: Any, event_repo: Any,
                 skill_service: SkillService, file_repo: Any = None,
                 plugin_service: Any = None, capability_service: Any = None,
                 plugin_config_store: Any = None, ai4ms_identity: Any = None,
                 app_state: Any = None) -> None:
```

并在 docstring 的 Args 末尾追加：

```
            app_state: 应用状态对象（唤醒路径解析会话装配时取 settings /
                project_service）；None 时 wake 不可用。
```

函数体内赋值：

```python
        self._app_state = app_state
```

**(e) `chat` 增加 `wake_source` 参数并透传到 user/message 事件**：

签名末尾加：

```python
                   wake_source: dict | None = None) -> str:
```

docstring 追加：

```
            wake_source: 系统唤醒来源（{"job_id": ...}）；非空时本轮首条
                user/message 事件带 kind=job_completed 标记，供前端渲染成
                系统提示条而非用户气泡。
```

并在 `RunSession` 构造处把 `wake_source` 传入 `context_extra`：

```python
                context_extra={
                    "http_allowed_hosts": self._settings.allowed_hosts,
                    "wake_source": wake_source or {},
                    # ...其余保持原样...
```

**(f) 在 `_drive` 的 `finally` 末尾（`session_runs.discard(run_id)` 之后）追加**：

```python
            await self._notify_run_finished(session_id)
```

**(g) harness 侧承接 `wake_source`**：在 `packages/synlys-harness/src/synlys_harness/agent.py` 的 `run()` 中，`user_payload` 构造处改为：

```python
        user_payload: dict = {"text": user_text}
        wake_source = (self._context_extra or {}).get("wake_source") or {}
        if wake_source:
            user_payload.update({"kind": job_wake_kind(wake_source), **wake_source})
        if attachments:
            user_payload["attachments"] = attachments
        yield await self._emit(EventType.USER_MESSAGE, user_payload)
```

并在 `agent.py` 顶部 import 处补：

```python
from .jobs import job_wake_kind
```

在 `jobs.py` 中新增：

```python
def job_wake_kind(wake_source: dict) -> str:
    """系统唤醒消息的 kind 标记（前端据此渲染提示条）。

    Args:
        wake_source: 宿主注入的唤醒来源（{"job_id": ...}）。

    Returns:
        "job_completed"；来源非任务唤醒时返回空串。
    """
    return "job_completed" if wake_source.get("job_id") else ""
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v
```
```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v
```
Expected: 两个仓库全绿

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py packages/synlys-harness/src/synlys_harness/agent.py packages/synlys-harness/src/synlys_harness/jobs.py apps/web/backend/tests/test_agent_wake.py
git commit -m "AgentService 支持空闲判定、系统唤醒与运行结束回调

- is_busy / wake（复用 session_runtime 解析装配）
- _drive 收尾回调 run_finished_hook，供任务唤醒队列 drain
- 唤醒消息带 kind=job_completed 标记，供前端渲染提示条"
```

---

## Task 9: 完成通知与待唤醒队列

**Files:**
- Modify: `apps/web/backend/app/services/job_service.py`
- Test: `apps/web/backend/tests/test_job_service.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_job_service.py`：

```python
async def test_terminal_status_triggers_wake(store):
    """任务进入终态时调用唤醒回调，文本含任务与结果摘要。"""
    woken: list[tuple[str, str, str]] = []

    async def wake(session_id, text, job_id):
        woken.append((session_id, text, job_id))

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k",
                                      "params": {}, "label": "试算"},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert len(woken) == 1
    assert woken[0][0] == "s1"
    assert woken[0][2] == submitted.data["job_id"]
    assert "试算" in woken[0][1]


async def test_busy_session_queues_wake_until_drain(store):
    """会话忙时先排队，drain 后补发（不丢唤醒）。"""
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: True)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert woken == []
    assert service.pending_wake_count("s1") == 1
    await service.drain_pending("s1")
    assert woken == [submitted.data["job_id"]]
    assert service.pending_wake_count("s1") == 0


async def test_only_notified_once_per_job(store):
    """同一任务不重复唤醒（避免刷新抖动导致多轮注入）。"""
    woken: list[str] = []

    async def wake(session_id, text, job_id):
        woken.append(job_id)

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    job_id = submitted.data["job_id"]
    await service.refresh(await service.get(job_id))
    await service.refresh(await service.get(job_id))
    await service.refresh(await service.get(job_id))
    assert woken == [job_id]


async def test_wake_composes_result_text(store):
    """唤醒文本包含任务类型、状态与结果正文（供模型直接整合）。"""
    seen: list[str] = []

    async def wake(session_id, text, job_id):
        seen.append(text)

    class ResultConnector(FakeConnector):
        async def poll(self, external_id, ctx):
            return "done"

    service, reg = _job_service(store)
    service.set_wake_callback(wake)
    service.set_busy_check(lambda sid: False)
    reg.register(ResultConnector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    submitted = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                     user={"sub": "u1"}, session_id="s1", ctx_extra={})
    await service.refresh(await service.get(submitted.data["job_id"]))
    assert "任务 ID" in seen[0]
    assert "completed" in seen[0]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: FAIL — `AttributeError: 'JobService' object has no attribute 'set_busy_check'`

- [ ] **Step 3: 实现唤醒与队列**

在 `apps/web/backend/app/services/job_service.py` 的 `__init__` 中追加字段：

```python
        # 会话忙判定（装配阶段注入 AgentService.is_busy；缺省视为空闲）
        self._busy_check: Callable[[str], bool] = lambda _sid: False
```

并新增方法（放在 `set_wake_callback` 之后）：

```python
    def set_busy_check(self, check: Callable[[str], bool]) -> None:
        """注入会话忙判定（装配阶段传 AgentService.is_busy）。

        Args:
            check: (session_id) -> bool。
        """
        self._busy_check = check

    def pending_wake_count(self, session_id: str) -> int:
        """该会话待唤醒的任务数（可观测/测试用）。"""
        return len(self._pending_wake.get(session_id, []))

    async def _notify_wake(self, doc: dict) -> None:
        """任务终态后通知会话（空闲立即唤醒，忙则排队）。

        Args:
            doc: 已进入终态的任务文档。
        """
        job_id = str(doc.get("_id", ""))
        session_id = str(doc.get("session_id", ""))
        if not job_id or not session_id:
            return
        if self._busy_check(session_id):
            self._pending_wake.setdefault(session_id, []).append(job_id)
            return
        await self._wake_now(doc)

    async def _wake_now(self, doc: dict) -> None:
        """立即唤醒（回调缺失或失败时记日志、不抛出）。

        Args:
            doc: 任务文档。
        """
        if self._wake is None:
            _LOGGER.warning("未注入唤醒回调，任务完成通知被丢弃 job=%s", doc.get("_id"))
            return
        session_id = str(doc.get("session_id", ""))
        try:
            await self._wake(session_id, self.compose_wake_text(doc),
                             str(doc.get("_id", "")))
        except Exception:  # noqa: BLE001 唤醒失败不得影响轮询循环
            _LOGGER.warning("任务完成唤醒失败 session=%s job=%s",
                            session_id, doc.get("_id"), exc_info=True)

    async def drain_pending(self, session_id: str) -> None:
        """会话空闲后补发待唤醒任务（由 AgentService 在 run 结束时回调）。

        Args:
            session_id: 会话 id。
        """
        pending = self._pending_wake.pop(session_id, [])
        for job_id in pending:
            doc = await self._repo.get(job_id)
            if doc is None:
                continue
            await self._wake_now(doc)

    @staticmethod
    def compose_wake_text(doc: dict) -> str:
        """组装唤醒文本（模型据此继续处理任务结果）。

        Args:
            doc: 任务文档。

        Returns:
            系统通知文本。
        """
        lines = [
            "【系统通知】你之前提交的后台任务已结束，请据此继续完成任务。",
            f"任务 ID：{doc.get('_id')}",
            f"任务类型：{doc.get('kind')}",
            f"任务说明：{doc.get('label') or '（无）'}",
            f"最终状态：{doc.get('status')}",
        ]
        if doc.get("error"):
            lines.append(f"错误信息：{_clip(str(doc['error']), 1000)}")
        if doc.get("result"):
            lines.append(f"任务结果：\n{_clip(str(doc['result']))}")
        lines.append("若结果已足够，直接向用户汇报结论；若还需补充计算，可继续调用工具。")
        return "\n".join(lines)
```

并在 `refresh` 内，**状态确实发生变化且新状态是终态**时追加通知与结束时间。把 `refresh` 末尾的 return 改为：

```python
        updated = await self._repo.update(doc["_id"], {
            "status": mapped.value, "last_raw_status": str(raw)})
        if is_terminal(mapped):
            updated = await self._repo.update(doc["_id"], {"ended_at": time.time()})
            await self._notify_wake(updated or doc)
        return updated
```

同样，在 `refresh` 中「连接器消失被标记失败」的分支末尾追加通知：

```python
            failed = await self._repo.update(doc["_id"], {
                "status": JobStatus.FAILED.value,
                "error": f"任务类型已不可用: {doc.get('kind')}",
                "ended_at": time.time(),
            })
            await self._notify_wake(failed or doc)
            return failed
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_service.py -v
```
Expected: 21 passed

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/job_service.py apps/web/backend/tests/test_job_service.py
git commit -m "任务终态唤醒会话：空闲立即注入，忙则排队

- compose_wake_text 组装系统通知（含状态与结果，供模型直接整合）
- 会话忙时进 pending 队列，由 drain_pending 在 run 结束后补发
- 唤醒失败只记日志，不影响轮询循环"
```

---

## Task 10: JobPoller 后台轮询

**Files:**
- Create: `apps/web/backend/app/services/job_poller.py`
- Test: `apps/web/backend/tests/test_job_poller.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_job_poller.py`：

```python
"""任务轮询器单测。"""
import asyncio

from app.db.repos import JobRepo
from app.services.job_connectors import JobConnectorRegistry, make_fake_connector
from app.services.job_poller import JobPoller
from app.services.job_service import JobService
from synlys_harness import JobStatus


async def test_tick_refreshes_active_jobs_only(store):
    """一轮 tick 只刷新未完成任务；终态任务不再请求外部系统。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    reg.register(make_fake_connector("k", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    done = await service.handle({"action": "submit", "kind": "k", "params": {}},
                                user={"sub": "u1"}, session_id="s1", ctx_extra={})
    reg.register(make_fake_connector("slow", plugin_id="p1", script=["queued", "queued"]),
                 status_map={"queued": JobStatus.PENDING})
    pending = await service.handle({"action": "submit", "kind": "slow", "params": {}},
                                   user={"sub": "u1"}, session_id="s1", ctx_extra={})
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(done.data["job_id"]))["status"] == "completed"
    assert (await service.get(pending.data["job_id"]))["status"] == "pending"


async def test_tick_survives_single_job_failure(store):
    """单个任务刷新抛异常不影响同轮其他任务（轮询循环不被打挂）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)

    class BoomConnector:
        kind = "boom"
        plugin_id = "p1"

        async def submit(self, params, ctx):
            return "e-boom"

        async def poll(self, external_id, ctx):
            raise RuntimeError("炸了")

        async def cancel(self, external_id, ctx):
            return True

    reg.register(BoomConnector(), status_map={"x": JobStatus.RUNNING})
    reg.register(make_fake_connector("ok", plugin_id="p1", script=["done"]),
                 status_map={"done": JobStatus.COMPLETED})
    await service.handle({"action": "submit", "kind": "boom", "params": {}},
                         user={"sub": "u1"}, session_id="s1", ctx_extra={})
    ok_job = await service.handle({"action": "submit", "kind": "ok", "params": {}},
                                  user={"sub": "u1"}, session_id="s1", ctx_extra={})
    poller = JobPoller(service)
    await poller.tick()
    assert (await service.get(ok_job.data["job_id"]))["status"] == "completed"


async def test_start_stop_loop(store):
    """start 起后台循环，stop 能干净收尾（无残留 task）。"""
    reg = JobConnectorRegistry()
    service = JobService(repo=JobRepo(store), connectors=reg)
    poller = JobPoller(service, interval_s=0.01)
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert poller.running is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_poller.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.job_poller'`

- [ ] **Step 3: 实现轮询器**

创建 `apps/web/backend/app/services/job_poller.py`：

```python
"""后台任务轮询器：定时把未完成任务的状态从外部系统同步回来。

设计取舍：agent 侧"免轮询"（提交后不占 step），但进程内必须有人定期问外部
系统——本类就是这个角色。轮询间隔取秒级而非事件驱动，是因为多数子平台
（Spec_Agent 等）不提供完成回调；将来若有平台支持 webhook，可另加事件入口，
本循环仍作为兜底。

单实例前提：本循环是进程内 asyncio task，依赖部署侧的 workers=1 约束。
"""
from __future__ import annotations

import asyncio
import logging

from app.services.job_service import JobService

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 5.0


class JobPoller:
    """未完成任务的状态轮询循环。"""

    def __init__(self, service: JobService,
                 interval_s: float = DEFAULT_INTERVAL_S) -> None:
        """初始化。

        Args:
            service: 任务服务（提供 list_active / refresh）。
            interval_s: 轮询间隔（秒）。
        """
        self._service = service
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        """循环是否在跑。"""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """启动后台循环（重复调用无副作用）。"""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop())
        _LOGGER.info("任务轮询器已启动（间隔 %.1fs）", self._interval_s)

    async def stop(self) -> None:
        """停止后台循环并等待收尾。"""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _LOGGER.info("任务轮询器已停止")

    async def _loop(self) -> None:
        """定时 tick；单轮异常只记日志，不退出循环。"""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 存储故障等不得打挂轮询
                _LOGGER.warning("任务轮询失败，下轮重试", exc_info=True)
            await asyncio.sleep(self._interval_s)

    async def tick(self) -> None:
        """执行一轮：逐个刷新未完成任务（单个失败不影响其余）。"""
        for doc in await self._service.list_active():
            try:
                await self._service.refresh(doc)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 同上
                _LOGGER.warning("刷新任务失败 job=%s", doc.get("_id"), exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_job_poller.py -v
```
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/job_poller.py apps/web/backend/tests/test_job_poller.py
git commit -m "新增任务轮询器：后台同步未完成任务状态

- 定时 tick 逐个刷新，单任务失败不影响其余、单轮异常不退出循环
- start/stop 管理 asyncio task，供 lifespan 启停"
```

---

## Task 11: jobs API 端点

**Files:**
- Create: `apps/web/backend/app/api/jobs_api.py`
- Modify: `apps/web/backend/app/main.py`（注册路由）
- Test: `apps/web/backend/tests/test_jobs_api.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_jobs_api.py`：

```python
"""任务查询 API 测试。"""
import pytest

from app.db.repos import JobRepo


@pytest.fixture
async def seeded_jobs(app):
    """落三条任务：两条属 u-user（分属两会话）、一条属他人。

    注意 conftest 的 user_headers 签发 sub 为 "u-user"。
    """
    repo = JobRepo(app.state.store)
    mine = await repo.create({"kind": "k", "status": "pending", "session_id": "sa",
                              "user_id": "u-user", "external_id": "e1"})
    mine2 = await repo.create({"kind": "k", "status": "completed", "session_id": "sb",
                               "user_id": "u-user", "external_id": "e2"})
    other = await repo.create({"kind": "k", "status": "pending", "session_id": "sa",
                               "user_id": "u-other", "external_id": "e3"})
    return {"session_a": "sa", "mine": mine["_id"],
            "mine2": mine2["_id"], "other": other["_id"]}


async def test_list_jobs_requires_auth(client):
    """未带凭证一律 401。"""
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 401


async def test_list_jobs_scoped_to_current_user(client, user_headers, seeded_jobs):
    """只返回当前用户的任务；可按 session_id 过滤。"""
    resp = await client.get("/api/v1/jobs", headers=user_headers)
    assert resp.status_code == 200
    assert {d["_id"] for d in resp.json()} == {
        seeded_jobs["mine"], seeded_jobs["mine2"]}
    only_one = await client.get(
        f"/api/v1/jobs?session_id={seeded_jobs['session_a']}", headers=user_headers)
    assert [d["_id"] for d in only_one.json()] == [seeded_jobs["mine"]]


async def test_get_job_detail_and_foreign_404(client, user_headers, seeded_jobs):
    """详情可见自己的任务；他人的任务 404。"""
    ok = await client.get(f"/api/v1/jobs/{seeded_jobs['mine']}", headers=user_headers)
    assert ok.status_code == 200
    assert ok.json()["_id"] == seeded_jobs["mine"]
    foreign = await client.get(
        f"/api/v1/jobs/{seeded_jobs['other']}", headers=user_headers)
    assert foreign.status_code == 404


async def test_get_unknown_job_404(client, user_headers):
    """不存在的任务 404。"""
    resp = await client.get("/api/v1/jobs/nope", headers=user_headers)
    assert resp.status_code == 404
```

> `client` 是 `httpx.AsyncClient`（ASGI 直连），故测试函数必须 `async def` 且 `await client.get(...)`；
> `user_headers` / `app` 为 conftest 既有 fixture（`user_headers` 的 sub 固定为 `u-user`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs_api.py -v
```
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 实现 API**

创建 `apps/web/backend/app/api/jobs_api.py`：

```python
"""后台任务查询 API（只读：提交/取消经由对话工具，不单独开口子）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _job_service(request: Request):
    """从 app.state 取任务服务。

    Args:
        request: 当前请求。

    Returns:
        JobService 实例。
    """
    return request.app.state.job_service


@router.get("/jobs")
async def list_jobs(request: Request, session_id: str | None = None,
                    user=Depends(get_current_user)) -> list[dict]:
    """当前用户的后台任务列表（可按会话过滤，创建时间升序）。

    Args:
        request: 当前请求。
        session_id: 可选会话过滤。
        user: 当前用户 payload。

    Returns:
        任务文档列表。
    """
    service = _job_service(request)
    if session_id:
        docs = await service.list_for_session(session_id)
        return [d for d in docs if str(d.get("user_id")) == user["sub"]]
    docs = await service._repo.list(filters={"user_id": user["sub"]})  # noqa: SLF001
    return sorted(docs, key=lambda d: float(d.get("created_at") or 0))


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request,
                  user=Depends(get_current_user)) -> dict:
    """任务详情（非本人 404，不泄露存在性）。

    Args:
        job_id: 任务 id。
        request: 当前请求。
        user: 当前用户 payload。

    Returns:
        任务文档。

    Raises:
        HTTPException: 任务不存在或非本人（404）。
    """
    doc = await _job_service(request).get(job_id)
    if doc is None or str(doc.get("user_id")) != user["sub"]:
        raise HTTPException(404, "任务不存在")
    return doc
```

> 为了让 API 层不必触碰私有字段，在 `JobService` 中补一个公开方法并在上面的 `list_jobs` 里改用它：

```python
    async def list_for_user(self, user_id: str) -> list[dict]:
        """列出某用户的全部任务（创建时间升序）。

        Args:
            user_id: 用户 sub。

        Returns:
            任务文档列表。
        """
        docs = await self._repo.list(filters={"user_id": user_id})
        return sorted(docs, key=lambda d: float(d.get("created_at") or 0))
```

对应把 `list_jobs` 的分支改为：

```python
    docs = await service.list_for_user(user["sub"])
    return docs
```

- [ ] **Step 4: 注册路由**

在 `apps/web/backend/app/main.py` 中：
1. import 段加：

```python
from app.api.jobs_api import router as jobs_router
```

2. `create_app()` 中 `app.include_router(me_router)` 之后加：

```python
    app.include_router(jobs_router)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs_api.py -v
```
Expected: 4 passed

- [ ] **Step 6: 提交**

```bash
git add apps/web/backend/app/api/jobs_api.py apps/web/backend/app/services/job_service.py apps/web/backend/app/main.py apps/web/backend/tests/test_jobs_api.py
git commit -m "新增任务查询 API

- GET /api/v1/jobs（按用户，可选 session_id 过滤）
- GET /api/v1/jobs/{id}（非本人 404）
- 提交/取消不单独开 API：只经对话工具，保持单一入口"
```

---

## Task 12: 应用装配（lifespan 接线）

**Files:**
- Modify: `apps/web/backend/app/main.py`
- Modify: `apps/web/backend/app/services/agent_service.py`（注入 job_handler）
- Test: `apps/web/backend/tests/test_jobs_e2e.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_jobs_e2e.py`：

```python
"""任务机制端到端：提交 → 轮询 → 完成唤醒 → 会话多出一条事件。

说明：本用例只验证"唤醒消息被注入会话"这一事实，不验证模型回复——真实
回复需要可用的模型服务。为此会话绑定的 provider 指向一个必然连不上的
地址，run 会在 LLM 调用处失败，但 turn/start 与 user/message 已经落账，
足以断言唤醒链路打通。
"""
import asyncio

import pytest

from app.db.repos import ProviderRepo, SessionRepo
from app.services.job_connectors import make_fake_connector
from synlys_harness import JobStatus


@pytest.fixture
async def session_id(app):
    """一条属于 u-user 的会话，绑定一个不可达的 provider。"""
    provider = await ProviderRepo(
        app.state.store, fernet_key=app.state.settings.fernet_key).create({
            "name": "测试模型", "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-test", "model_id": "m", "enabled": True})
    doc = await SessionRepo(app.state.store).create({
        "user_id": "u-user", "assistant_id": None, "title": "端到端",
        "project_id": None, "model_provider_id": provider["_id"]})
    return doc["_id"]


async def test_submit_poll_wake_roundtrip(app, session_id):
    """全链路：工具层提交 → poller 推进到终态 → 会话收到系统通知消息。"""
    # 连接器可在运行期注册（registry 与 ToolRegistry 同为可变注册表）
    app.state.job_connectors.register(
        make_fake_connector("k", plugin_id="p1", script=["queued", "done"]),
        status_map={"queued": JobStatus.PENDING, "done": JobStatus.COMPLETED})
    service = app.state.job_service
    result = await service.handle(
        {"action": "submit", "kind": "k", "params": {}, "label": "端到端"},
        user={"sub": "u-user"}, session_id=session_id, ctx_extra={})
    assert result.ok is True

    # 两轮 tick：第一轮 queued（保持 pending），第二轮 done（终态触发唤醒）
    await app.state.job_poller.tick()
    await app.state.job_poller.tick()

    # 唤醒起的 run 是后台 task，等事件落账（最多 2 秒）
    wake: list = []
    for _ in range(40):
        events = await app.state.event_repo.list_events(session_id)
        wake = [e for e in events if e.type.value == "user/message"
                and e.payload.get("kind") == "job_completed"]
        if wake:
            break
        await asyncio.sleep(0.05)
    assert len(wake) == 1
    assert wake[0].payload["job_id"] == result.data["job_id"]


async def test_poller_started_with_app(app):
    """应用启动后轮询器在跑（生命周期由 lifespan 管）。"""
    assert app.state.job_poller.running is True
```

> `app` 用 conftest 既有 fixture（已跑 lifespan、sqlite store、真实 Fernet key）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs_e2e.py -v
```
Expected: FAIL — `AttributeError: 'State' object has no attribute 'job_service'`

- [ ] **Step 3: 装配 JobService / Poller / 回调**

修改 `apps/web/backend/app/main.py` 的 `lifespan`，在 `app.state.agent_service = AgentService(...)` **之后**追加：

```python
    # 后台任务：连接器注册表（插件在 startup 时注册各自的 kind）→ 任务服务 →
    # 轮询器。唤醒回调双向接线：JobService → AgentService.wake；
    # AgentService run 结束 → JobService.drain_pending
    app.state.job_connectors = JobConnectorRegistry()
    # 装配期注册测试/内置连接器（真实子平台连接器由插件在 startup 注册）
    app.state.job_service = JobService(
        repo=JobRepo(store), connectors=app.state.job_connectors,
        plugin_config_store=plugin_config_store,
        ai4ms_identity=app.state.ai4ms_identity)
    # repo 聚合（唤醒路径解析会话装配用）：deps.get_repos 也是从这几个 repo
    # 组装，此处显式落一份到 app.state 供非请求上下文（轮询器/唤醒）使用
    app.state.repos = Repos(
        provider=app.state.provider_repo, assistant=app.state.assistant_repo,
        session=app.state.session_repo, run=app.state.run_repo,
        file=app.state.file_repo, event=app.state.event_repo)
    app.state.agent_service.set_repos(app.state.repos)
    app.state.agent_service.set_run_finished_hook(
        app.state.job_service.drain_pending)

    async def _wake_session(session_id: str, text: str, job_id: str) -> None:
        """任务完成唤醒：起一轮新对话把结果交回模型。"""
        try:
            await app.state.agent_service.wake(session_id, text, job_id)
        except Exception:  # noqa: BLE001 唤醒失败不得影响轮询循环
            logger.warning("任务唤醒失败 session=%s job=%s", session_id, job_id,
                           exc_info=True)

    app.state.job_service.set_wake_callback(_wake_session)
    app.state.job_service.set_busy_check(app.state.agent_service.is_busy)
    app.state.job_poller = JobPoller(app.state.job_service)
    await app.state.job_poller.start()
```

并在 `yield` 之后（`await store.close()` 之前）追加：

```python
    await app.state.job_poller.stop()
```

顶部 import 补：

```python
from app.api.deps import Repos
from app.db.repos import JobRepo
from app.services.job_connectors import JobConnectorRegistry
from app.services.job_poller import JobPoller
from app.services.job_service import JobService
```

**(b) AgentService 传入 app_state、注入 job_handler**

同文件 `AgentService(...)` 构造处，追加参数：

```python
        app_state=app.state)
```

修改 `apps/web/backend/app/services/agent_service.py`：

1. `__init__` 中新增字段（放在 `self._repos` 之后）：

```python
        # 后台任务服务（装配阶段经 set_job_service 注入；None 时 job.* 工具报不可用）
        self._job_service: Any = None
```

2. 新增 setter（放在 `set_repos` 之后）：

```python
    def set_job_service(self, service: Any) -> None:
        """注入后台任务服务（job.* 工具的宿主实现）。"""
        self._job_service = service
```

3. 在 `chat()` 内 `send_file_handler` 之后新增：

```python
            async def job_handler(payload: dict) -> ToolResult:
                """job.* 工具的宿主实现（提交/查询/取消后台任务）。"""
                if self._job_service is None:
                    return ToolResult(ok=False, content="后台任务未启用",
                                      error="no_handler")
                return await self._job_service.handle(
                    payload, user=user, session_id=session_id,
                    ctx_extra=ctx_extra_snapshot)
```

其中 `ctx_extra_snapshot` 需在构造 handler 前先算好（避免与 477 行的 `context_extra` 重复解析）。做法：把 `RunSession` 构造中 `context_extra={...}` 那段**先赋给局部变量**：

```python
            ctx_extra_snapshot = {
                "http_allowed_hosts": self._settings.allowed_hosts,
                # ...其余键保持不变...
                "plugins": (await self._visible_plugin_configs(
                                user["sub"], enabled_plugins)
                            if self._plugin_service is not None else {}),
                **await self._ai4ms_token_extra(user),
            }
            ctx_extra_snapshot["job_handler"] = job_handler
```

然后 `RunSession(..., context_extra=ctx_extra_snapshot)`。**注意别把 `job_handler` 放进 job_service 自己读的 `ctx_extra`（会形成自引用）**——`JobService.handle` 只读其中的 `plugins` 与 `ai4ms_token`，多余键无害。

**(h) 把 job.* 加入平台工具白名单（否则有白名单的专家拿不到后台任务）**

`agent_service.py:53` 的常量：

```python
SKILL_TOOLS = ("skill.list", "skill.read", "ask_user", "file.send")
```

它的语义是「平台交互工具：无条件追加到助手白名单，平台能力不依赖助手自行声明」——后台任务正是同一类宿主注入通道。不改的话，`chat()` 里 `tool_names = [*whitelist, *SKILL_TOOLS]` 会让**所有配了工具白名单的专家拿不到 `job.*`**，而 `catalog/plugins/spec_agent/plugin.json` 的「谱图解析专家」恰恰是显式白名单——异步谱图任务这个旗舰能力会对它静默不可见。

改为：

```python
# 技能与平台交互工具：无条件追加到助手白名单（平台能力，不依赖助手自行声明；
# ask_user=问答回路、file.send=产物交付、job.*=后台任务通道，是宿主注入的
# 交互通道，任何助手都可用）
SKILL_TOOLS = ("skill.list", "skill.read", "ask_user", "file.send",
               "job.submit", "job.status", "job.list", "job.cancel")
```

4. `main.py` 中 JobService 构造之后（`set_wake_callback` 附近）追加回填：

```python
    # 循环依赖：AgentService 先于 JobService 构造，构造完再回填（用 setter
    # 而非直接赋私有字段，与 set_repos 同风格）
    app.state.agent_service.set_job_service(app.state.job_service)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_jobs_e2e.py -v
```
Expected: 2 passed

- [ ] **Step 5: 全量回归**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -v
```
Expected: 全绿（含既有 411+ 用例）

- [ ] **Step 6: 提交**

```bash
git add apps/web/backend/app/main.py apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_jobs_e2e.py
git commit -m "装配后台任务链路：连接器注册表 + 任务服务 + 轮询器

- lifespan 启停轮询器，双向接线唤醒回调与 drain
- AgentService 注入 job_handler，job.* 工具接入对话
- 加 set_job_service/set_repos setter 避免跨模块触私有字段"
```

---

## Task 13: 前端把任务完成消息渲染为系统提示条

**Files:**
- Modify: `apps/web/frontend/src/stores/chat.ts:25`、`apps/web/frontend/src/stores/chat.ts:212-219`
- Modify: `apps/web/frontend/src/components/chat/UserMessage.tsx`
- Modify: `apps/web/frontend/src/components/chat/MessageList.tsx:214`
- Modify: `apps/web/frontend/src/components/chat/toolLabels.ts`

**新增一个 Step 0（先做，属于本任务的前置）：给四个 job 工具补中文标签**

`toolLabels.ts` 顶部的 `TOOL_LABELS` 注释写明「与后端 ToolRegistry 注册项一一对应」，其 key 驱动两处：`RunInfoPanel` 的工具中文标签（缺失会回退显示 `job.submit` 原名），以及 `AssistantsAdmin` 的 `TOOL_NAMES = Object.keys(TOOL_LABELS)`（管理员配工具白名单的勾选框——缺标签就**根本列不出**这四个工具）。Task 2 已在后端注册这四个工具，这里补齐前端映射：

```ts
  // 后台任务
  'job.submit': '提交后台任务',
  'job.status': '查询任务状态',
  'job.list': '列出任务',
  'job.cancel': '取消任务',
```

按文件既有的分组与书写风格插入（先读该文件的前 20 行确认格式）。改完随本任务的构建校验一起验证。

**背景（照抄对象）:** 系统注入的唤醒消息在事件流里就是一条 `user/message`，若直接按用户气泡渲染，用户会看到"自己"发了一条莫名其妙的指令。因此按 `payload.kind === 'job_completed'` 分流，渲染成居中的浅色提示条——与 `session/compaction` 的压缩提示同属"系统提示"族。

- [ ] **Step 1: 给视图模型加标记**

修改 `apps/web/frontend/src/stores/chat.ts` 第 25 行：

```ts
  | { kind: 'user'; text: string; attachments?: MessageAttachment[]; jobId?: string }
```

- [ ] **Step 2: 投影时识别唤醒消息**

修改同文件 `reduceEvent` 的 `user/message` 分支（第 212-219 行）：

```ts
    case 'user/message': {
      // 用户消息（含 steering 插话）逐条展示；附件（已上传文件引用）随事件展示。
      // 后台任务完成唤醒（payload.kind=job_completed）是系统注入消息，带 jobId
      // 供渲染层分流成提示条，不显示成用户气泡
      const attachments = Array.isArray(payload.attachments)
        ? (payload.attachments as MessageAttachment[])
        : undefined
      const wakeJobId =
        payload.kind === 'job_completed' && typeof payload.job_id === 'string'
          ? payload.job_id
          : undefined
      const item: ChatItem = {
        kind: 'user',
        text: String(payload.text ?? ''),
        attachments,
        ...(wakeJobId ? { jobId: wakeJobId } : {}),
      }
      return { ...state, items: [...state.items, item] }
    }
```

- [ ] **Step 3: 渲染提示条**

修改 `apps/web/frontend/src/components/chat/UserMessage.tsx`，把组件改为按 `jobId` 分流（新增一个内部展示分支，沿用既有 token）：

```tsx
interface UserMessageProps {
  /** 消息文本。 */
  text: string
  /** 随消息发送的附件（可选）。 */
  attachments?: MessageAttachment[]
  /** 后台任务完成唤醒消息的关联任务 id（有值 = 系统提示条，不是用户气泡）。 */
  jobId?: string
}

/** 用户消息组件（轻量气泡 + 附件行；系统唤醒消息渲染为居中提示条）。 */
export default function UserMessage({ text, attachments, jobId }: UserMessageProps) {
  if (jobId) {
    return (
      <div className="flex justify-center">
        <div className="flex max-w-[85%] items-center gap-2 rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-1.5 text-[13px] text-[var(--sa-alias-label-tertiary)]">
          <svg
            width="13"
            height="13"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="shrink-0 opacity-70"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="7.2" />
            <path d="M10 6.4v4.2l2.6 1.6" />
          </svg>
          <span className="min-w-0 truncate">后台任务已完成，正在汇总结果…</span>
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-end justify-end">
      {/* ……以下保持原实现不变…… */}
    </div>
  )
}
```

> 提示条**只显示固定文案**，不回显 `text`（唤醒文本是给模型的指令，对用户没有意义）；
> 完整的任务结论由紧随其后的助手回答承载。

- [ ] **Step 4: 传递标记**

修改 `apps/web/frontend/src/components/chat/MessageList.tsx` 第 214 行：

```tsx
      {turn.user && (
        <UserMessage
          text={turn.user.text}
          attachments={turn.user.attachments}
          jobId={turn.user.jobId}
        />
      )}
```

- [ ] **Step 5: 类型检查与构建**

```bash
cd apps/web/frontend
npm run build
```
Expected: 构建通过（`tsc -b` 无类型错误）

- [ ] **Step 6: 提交**

```bash
git add apps/web/frontend/src/stores/chat.ts apps/web/frontend/src/components/chat/UserMessage.tsx apps/web/frontend/src/components/chat/MessageList.tsx
git commit -m "后台任务完成消息渲染为系统提示条

- user/message 带 kind=job_completed 时投影出 jobId 标记
- 提示条只给固定文案，任务结论由助手回答承载"
```

---

## Task 14: 文档同步

**Files:**
- Modify: `README.md`
- Modify: `apps/web/backend/README.md`
- Modify: `docs/superpowers/plans/2026-09-16-synlysagent-13-next-roadmap.md`
- Modify: `apps/web/backend/app/version.py`、`apps/web/frontend/package.json`

- [ ] **Step 1: 后端 README 补任务机制章节**

在 `apps/web/backend/README.md` 的「AI⁴MS 子平台接入（插件机制）」章节之后新增一节：

```markdown
## 后台任务（Job 注册表）

长耗时作业（谱图解析等）走统一的异步任务机制，避免占满对话 step：

- **提交**：模型调用 `job.submit(kind, params, label)`，宿主经连接器提交到子平台后**立即返回 job_id**，不阻塞本轮对话。
- **轮询**：进程内 `JobPoller` 每 5 秒把未完成任务的状态从子平台同步回来（`app/services/job_poller.py`）。agent 侧不轮询，避免浪费 step。
- **唤醒**：任务进入终态（completed/failed/cancelled）时自动向所属会话注入一条系统消息并起新一轮对话，模型据此整合结果并向用户汇报；会话正忙则先排队，等本轮结束后补发。
- **状态机**：`pending → running → completed/failed/cancelled`（harness `jobs.py`）；子平台状态由连接器映射，**未映射或查询失败一律保持原状态**，不倒退、不误判失败。
- **连接器**：新增一个子平台的异步任务 = 在插件内实现 `JobConnector`（`submit`/`poll`/`cancel` + 状态映射）并注册到 `JobConnectorRegistry`，宿主零改动。
- **查询**：`GET /api/v1/jobs`（当前用户，可按 `session_id` 过滤）、`GET /api/v1/jobs/{job_id}`；提交与取消不单独开 API，只经对话工具（单一入口）。

**单实例前提**：轮询器是进程内任务，与既有的 `workers=1` 约束一致；多副本会导致同一任务被重复轮询与重复唤醒。
```

- [ ] **Step 2: 根 README 更新路线状态**

在 `README.md` 的「后续路线」三阶段表格下方补一行进展说明：

```markdown
**进展**：统一 Job 注册表（C1）✅ 已实现——后台任务提交即返回、轮询器同步状态、终态自动唤醒 agent；下一步 Spec_Agent 异步谱图任务（C2）建在其上。
```

同时把首页功能概述段落（第 7 行那段）末尾追加：

```markdown
后台长任务走统一 Job 注册表（提交即返回、完成自动唤醒 agent）。
```

- [ ] **Step 3: 更新 13 号文档的勾选状态**

在 `docs/superpowers/plans/2026-09-16-synlysagent-13-next-roadmap.md` 的 C1 章节，把标题行改为带完成标记的形式，并勾选全部子项：

```markdown
### C1. 统一 Job 注册表 ★ 最高优先级 ✅ 已完成
```

（子项 checkbox 逐个改为 `- [x]`。）

- [ ] **Step 4: 升版本号**

C1 是向下兼容的新功能（新增能力，不破坏既有行为）→ 按语义化版本规则升**次版本**：

- `apps/web/backend/app/version.py`：`APP_VERSION = "0.11.0-beta.1"`
- `apps/web/frontend/package.json`：`"version": "0.11.0-beta.1"`

两处必须一致（前端经 `GET /api/health` 读取后端版本展示，`version.py` 头部注释已声明该约束）。

- [ ] **Step 5: 验证版本口径一致**

```bash
cd E:/agent_projects/Synlora
grep -n "0.11.0-beta.1" apps/web/backend/app/version.py apps/web/frontend/package.json
```
Expected: 两个文件各命中一行

- [ ] **Step 6: 提交**

```bash
git add README.md apps/web/backend/README.md docs/superpowers/plans/2026-09-16-synlysagent-13-next-roadmap.md apps/web/backend/app/version.py apps/web/frontend/package.json
git commit -m "文档同步后台任务机制并升至 0.11.0-beta.1

- 后端 README 新增「后台任务（Job 注册表）」章节
- 根 README 路线表补 C1 完成进展
- 版本号次版本递增（新增能力、向下兼容）"
```

---

## 验收清单（全部任务完成后逐条核对）

- [ ] `cd packages/synlys-harness && conda run -n synlysagent python -m pytest -v` 全绿
- [ ] `cd apps/web/backend && conda run -n synlysagent python -m pytest -v` 全绿
- [ ] `cd apps/web/frontend && npm run build` 通过
- [ ] 本地起服务后，用 `FakeConnector` 临时注册一个演示 kind（可写在 `main.py` 装配处的调试分支或测试中），走通：对话里让 agent 提交任务 → 30 秒内（两个 tick 周期）会话中自动出现一条"后台任务已完成"提示条 → agent 自动整合并给出回答
- [ ] 任务中断验证：提交后立刻 `Ctrl+C` 重启服务 → 重启后轮询器继续跟踪该任务 → 完成时仍能唤醒（会话不卡死）
- [ ] 跨用户隔离：用户 B 无法通过 `GET /api/v1/jobs/{id}` 看到用户 A 的任务（404）
- [ ] 版本口径：`GET /api/health` 返回 `0.11.0-beta.1`，与前端展示一致

---

## 后续（本计划不含，留给 C2）

- 真实 `Spec_AgentConnector`：对接 `/api/v1/tasks/{nmr,gpc,...}`，用 `mint_token.py` 签的服务凭证或按用户代签凭证；5 种谱图任务逐个注册 kind
- 任务进度百分比与阶段文案（当前只同步状态，不含中间进度）
- 前端任务面板（当前只在对话流里提示，无独立的任务列表页）
- 任务结果落工作区文件（结果体量大时经 `file.send` 交付，而不是塞进事件）

