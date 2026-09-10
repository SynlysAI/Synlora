# SynlysAgent Plan 1：项目脚手架 + synlys-harness 核心运行时

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 SynlysAgent monorepo 脚手架，并以 TDD 实现 `synlys-harness` 纯 Python 包——事件会话、工具注册与四段管线、受限沙箱、LLM 路由、agent loop（turn/step、steering、cancel、extension 钩子）。

**Architecture:** harness 是零 Web 依赖的库包（src 布局），web 后端（Plan 2）只是它的宿主。核心抽象：`SessionEvent` 事件流为唯一事实源，`derive_messages()` 把事件投影为 LLM 消息；工具经 `ToolRegistry` 注册、由 `ToolPipeline` 统一执行（权限/超时/截断）；`RunSession.run()` 是产出事件的 async generator。

**Tech Stack:** Python 3.12（conda 环境 `synlysagent`）、pydantic v2、openai SDK（仅 OpenAI 兼容流式）、httpx、pytest + pytest-asyncio。

**设计文档:** `docs/superpowers/specs/2026-09-10-synlysagent-platform-design.md`（第 3、4 节）

**环境注意（Windows/git-bash）:** 所有 Python/pytest 命令统一前缀 `conda run -n synlysagent --no-capture-output`。若 bash 中 `conda` 不可用，改用 `cmd /c "conda run -n synlysagent ..."`。

---

## 文件结构（Plan 1 产出）

```
SynlysAgent/
├── .gitignore
├── packages/synlys-harness/
│   ├── pyproject.toml
│   ├── src/synlys_harness/
│   │   ├── __init__.py          # 公共导出
│   │   ├── types.py             # 核心数据模型（Message/SessionEvent/ToolDefinition/AgentConfig...）
│   │   ├── events.py            # EventLog：seq 分配 + sinks 广播
│   │   ├── session.py           # derive_messages 事件投影
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── registry.py      # ToolRegistry + @tool 装饰器
│   │   │   ├── pipeline.py      # 四段执行管线
│   │   │   ├── sandbox.py       # python.run 受限子进程执行
│   │   │   └── builtin.py       # file.* / knowledge.search / http.request
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── backend.py       # LLMBackend 协议 + OpenAICompatibleBackend + StreamEvent
│   │   ├── extensions.py        # ExtensionHooks（4 钩子）
│   │   └── agent.py             # AgentConfig + RunSession（agent loop）
│   └── tests/
│       ├── conftest.py
│       ├── test_types.py
│       ├── test_events.py
│       ├── test_session.py
│       ├── test_registry.py
│       ├── test_pipeline.py
│       ├── test_sandbox.py
│       ├── test_builtin.py
│       ├── test_backend.py
│       └── test_agent_loop.py
└── docs/superpowers/plans/2026-09-10-synlysagent-01-harness.md   # 本计划
```

---

### Task 1: Monorepo 脚手架与 conda 环境

**Files:**
- Create: `.gitignore`
- Create: `packages/synlys-harness/pyproject.toml`
- Create: `packages/synlys-harness/src/synlys_harness/__init__.py`（空占位）
- Create: `packages/synlys-harness/tests/conftest.py`（空占位）

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p packages/synlys-harness/src/synlys_harness/tools packages/synlys-harness/src/synlys_harness/models packages/synlys-harness/tests
```

- [ ] **Step 2: 写 `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
.pytest_cache/
.venv/

# Node
node_modules/
dist/
.vite/

# 运行时数据（用户工作区/会话事件/本地库）
data/

# 环境与密钥
.env
.env.*

# IDE / OS
.idea/
.vscode/
Thumbs.db
```

- [ ] **Step 3: 写 `packages/synlys-harness/pyproject.toml`**

```toml
[project]
name = "synlys-harness"
version = "0.1.0"
description = "SynlysAgent 核心 Agent 运行时（纯 Python，无 Web 依赖）"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "openai>=1.40",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: 写空占位文件**

`packages/synlys-harness/src/synlys_harness/__init__.py`：

```python
"""SynlysAgent 核心 Agent 运行时。"""
```

`packages/synlys-harness/tests/conftest.py`：

```python
"""harness 单测共享 fixtures。"""
```

同时创建包目录占位（否则 setuptools find 不到子包）：
`src/synlys_harness/tools/__init__.py`、`src/synlys_harness/models/__init__.py`，内容均为一行 docstring（`"""tools 子包。"""` / `"""models 子包。"""`）。

- [ ] **Step 5: 创建 conda 环境并安装**

```bash
conda create -n synlysagent python=3.12 -y
conda run -n synlysagent --no-capture-output pip install -e "packages/synlys-harness[dev]"
```

Expected: 末尾输出 `Successfully installed synlys-harness-0.1.0 ...`

- [ ] **Step 6: 验证测试框架可用**

```bash
conda run -n synlysagent --no-capture-output python -c "import synlys_harness; print(synlys_harness.__doc__)"
```

Expected: 输出 `SynlysAgent 核心 Agent 运行时。`

- [ ] **Step 7: Commit**

```bash
git add .gitignore packages/
git commit -m "搭建 monorepo 脚手架与 synlys-harness 包骨架

- conda 环境 synlysagent（Python 3.12）
- src 布局 + pydantic/openai/httpx 依赖 + pytest 配置"
```

---

### Task 2: 核心数据模型 `types.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/types.py`
- Test: `packages/synlys-harness/tests/test_types.py`

- [ ] **Step 1: 写失败测试 `tests/test_types.py`**

```python
"""types 数据模型单测。"""
from synlys_harness.types import (
    EventType, Message, Role, SessionEvent, ToolCall,
)


def test_message_roundtrip():
    """Message 各字段可构造且默认值正确。"""
    m = Message(role=Role.USER, content="你好")
    assert m.role is Role.USER
    assert m.tool_calls == []
    assert m.tool_call_id is None


def test_tool_call_model():
    """ToolCall 参数为任意 dict。"""
    tc = ToolCall(id="call_1", name="python.run", arguments={"code": "print(1)"})
    assert tc.arguments["code"] == "print(1)"


def test_session_event_seq_enforced():
    """SessionEvent 必须携带 seq 与 ts。"""
    ev = SessionEvent(seq=1, type=EventType.TURN_START, payload={"a": 1}, ts=1_000.0)
    assert ev.seq == 1
    assert ev.type == EventType.TURN_START


def test_event_type_members():
    """事件类型枚举覆盖设计文档全部 V1 事件。"""
    members = {e.value for e in EventType}
    assert members == {
        "turn/start", "user/message", "llm/delta", "assistant/message",
        "tool/call", "tool/result", "turn/end", "turn/aborted", "error",
    }
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_types.py -v
```

Expected: FAIL（`ModuleNotFoundError: No module named 'synlys_harness.types'`）

- [ ] **Step 3: 实现 `src/synlys_harness/types.py`**

```python
"""核心数据模型：消息、事件、工具定义与 Agent 配置。

所有模型均为 pydantic v2；事件类型字符串与设计文档 §4.2 一一对应。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class Role(str, enum.Enum):
    """LLM 消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class EventType(str, enum.Enum):
    """会话事件类型（唯一事实源，见设计文档 §4.2）。"""

    TURN_START = "turn/start"
    USER_MESSAGE = "user/message"
    LLM_DELTA = "llm/delta"
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    TURN_END = "turn/end"
    TURN_ABORTED = "turn/aborted"
    ERROR = "error"


class ToolCall(BaseModel):
    """一次工具调用的描述（LLM function calling 返回值）。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """投喂给 LLM 的消息（由事件投影或用户输入构造）。"""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class SessionEvent(BaseModel):
    """会话事件信封：seq 单调递增，ts 为 unix 秒。"""

    model_config = ConfigDict(frozen=True)

    seq: int
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float


class Permission(str, enum.Enum):
    """pre-execute 管线段的判定结果。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"


class ToolContext(BaseModel):
    """工具执行上下文（由宿主构造：web 层注入用户与工作区信息）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    run_id: str
    workspace_root: Path | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果：content 是给 LLM 看的文本表示。"""

    ok: bool = True
    content: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    error: str | None = None


ToolExecuteFn = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class ToolDefinition:
    """工具定义：声明 schema 与执行函数，权限/超时由管线统一处理。"""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecuteFn
    timeout_s: float = 60.0
    permission: Permission = Permission.ALLOW
    concurrency_safe: bool = True


@dataclass
class ExtensionHooks:
    """V1 最小扩展钩子集（设计文档 §4.5）。"""

    on_session_start: Callable[[Any], Awaitable[None]] | None = None
    before_llm_call: Callable[[list[Message]], Awaitable[list[Message]]] | None = None
    on_tool_event: Callable[[SessionEvent], Awaitable[None]] | None = None
    on_session_end: Callable[[Any], Awaitable[None]] | None = None


class AgentConfig(BaseModel):
    """一次 agent 运行的组装配置（助手 Profile + 运行时上下文合成）。"""

    system_prompt: str
    tool_names: list[str] = Field(default_factory=list)
    max_steps: int = 25
    model_id: str = ""
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_types.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/types.py tests/test_types.py
git commit -m "harness: 核心数据模型 types.py

- Message/ToolCall/SessionEvent/EventType（V1 全部事件）
- ToolDefinition/ToolContext/ToolResult/Permission
- ExtensionHooks 四钩子 + AgentConfig"
```

---

### Task 3: 事件日志 `events.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/events.py`
- Test: `packages/synlys-harness/tests/test_events.py`

职责：分配递增 seq、维护内存事件列表、把事件广播给 sinks（宿主用它写 JSONL/DB/SSE）。**不负责持久化**。

- [ ] **Step 1: 写失败测试 `tests/test_events.py`**

```python
"""EventLog 单测。"""
from synlys_harness.events import EventLog
from synlys_harness.types import EventType


async def test_append_assigns_seq():
    """seq 从 0 开始单调递增。"""
    log = EventLog()
    ev1 = log.append(EventType.TURN_START, {})
    ev2 = log.append(EventType.USER_MESSAGE, {"text": "hi"})
    assert (ev1.seq, ev2.seq) == (0, 1)
    assert ev1.ts > 0


async def test_sinks_receive_events():
    """append 同步广播到所有 sink。"""
    received = []

    async def sink(ev):
        received.append(ev)

    log = EventLog(sinks=[sink])
    log.append(EventType.USER_MESSAGE, {"text": "a"})
    log.append(EventType.LLM_DELTA, {"text": "b"})
    assert [e.type for e in received] == [EventType.USER_MESSAGE, EventType.LLM_DELTA]


async def test_events_view():
    """events 只读视图返回全部事件。"""
    log = EventLog()
    await log.append(EventType.TURN_START, {})
    await log.append(EventType.TURN_END, {})
    assert [e.seq for e in log.events] == [0, 1]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_events.py -v
```

Expected: FAIL（`cannot import name 'EventLog'`）

- [ ] **Step 3: 实现 `src/synlys_harness/events.py`**

```python
"""事件日志：seq 分配与 sinks 广播。"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from .types import EventType, SessionEvent

EventSink = Callable[[SessionEvent], Awaitable[None]]


class EventLog:
    """单会话事件日志（内存态；持久化由宿主通过 sink 实现）。"""

    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        """初始化事件日志。

        Args:
            sinks: 事件广播目标（可为 None，宿主后续也可不接）。
        """
        self._events: list[SessionEvent] = []
        self._sinks = sinks or []

    @property
    def events(self) -> list[SessionEvent]:
        """返回全部事件的只读副本。"""
        return list(self._events)

    async def append(self, type_: EventType, payload: dict[str, Any]) -> SessionEvent:
        """追加事件并广播到所有 sink。

        Args:
            type_: 事件类型。
            payload: 事件负载。

        Returns:
            构造完成的 SessionEvent（seq 已分配）。
        """
        event = SessionEvent(
            seq=len(self._events), type=type_, payload=payload, ts=time.time()
        )
        self._events.append(event)
        for sink in self._sinks:
            await sink(event)
        return event
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_events.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/events.py tests/test_events.py
git commit -m "harness: EventLog 事件日志（seq 分配 + sinks 广播）"
```

---

### Task 4: 事件投影 `session.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/session.py`
- Test: `packages/synlys-harness/tests/test_session.py`

投影规则（写死在测试里）：
- `turn/start` 的 `system_prompt` → 第一条 system 消息（仅当 `include_system=True`）
- `user/message` → user 消息
- `assistant/message` → assistant 纯文本消息
- `tool/call` → assistant 消息（content 可为 None，tool_calls 单元素）
- `tool/result` → tool 消息（content 为结果文本，tool_call_id 关联）
- `llm/delta`、`turn/end`、`turn/aborted`、`error` → 不投影

- [ ] **Step 1: 写失败测试 `tests/test_session.py`**

```python
"""derive_messages 投影单测。"""
from synlys_harness.session import derive_messages
from synlys_harness.types import EventType, SessionEvent


def _ev(seq: int, type_: EventType, payload: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=type_, payload=payload, ts=float(seq))


def test_full_projection():
    """完整一轮 turn 的事件投影为合法消息序列。"""
    events = [
        _ev(0, EventType.TURN_START, {"system_prompt": "你是助手"}),
        _ev(1, EventType.USER_MESSAGE, {"text": "分析数据"}),
        _ev(2, EventType.TOOL_CALL, {
            "tool_call": {"id": "c1", "name": "python.run", "arguments": {"code": "1"}},
            "content": None,
        }),
        _ev(3, EventType.TOOL_RESULT, {
            "tool_call_id": "c1", "name": "python.run", "ok": True, "content": "1",
        }),
        _ev(4, EventType.ASSISTANT_MESSAGE, {"content": "结果是 1"}),
        _ev(5, EventType.TURN_END, {}),
    ]
    msgs = derive_messages(events, include_system=True)
    assert [m.role.value for m in msgs] == ["system", "user", "assistant", "tool", "assistant"]
    assert msgs[0].content == "你是助手"
    assert msgs[2].tool_calls[0].name == "python.run"
    assert msgs[3].tool_call_id == "c1"
    assert msgs[3].content == "1"


def test_delta_not_projected():
    """llm/delta 不进入投影（由 assistant/message 承载最终文本）。"""
    events = [
        _ev(0, EventType.LLM_DELTA, {"text": "部分"}),
        _ev(1, EventType.ASSISTANT_MESSAGE, {"content": "部分"}),
    ]
    msgs = derive_messages(events, include_system=False)
    assert len(msgs) == 1 and msgs[0].content == "部分"


def test_no_system_when_disabled():
    """include_system=False 时不产出 system 消息。"""
    events = [_ev(0, EventType.TURN_START, {"system_prompt": "x"})]
    assert derive_messages(events, include_system=False) == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_session.py -v
```

Expected: FAIL（`cannot import name 'derive_messages'`）

- [ ] **Step 3: 实现 `src/synlys_harness/session.py`**

```python
"""事件流 → LLM 消息投影（DSH foldSurface 思想）。"""
from __future__ import annotations

from .types import EventType, Message, Role, SessionEvent, ToolCall


def derive_messages(events: list[SessionEvent], include_system: bool = True) -> list[Message]:
    """把会话事件流投影为 LLM 消息序列。

    Args:
        events: 会话全部事件（按 seq 升序）。
        include_system: 是否把 turn/start 中的 system_prompt 投影为首条 system 消息。

    Returns:
        可直接投喂 LLM 的 Message 列表。
    """
    messages: list[Message] = []
    system_emitted = False
    for ev in events:
        p = ev.payload
        if ev.type is EventType.TURN_START and include_system and not system_emitted:
            prompt = p.get("system_prompt")
            if prompt:
                messages.append(Message(role=Role.SYSTEM, content=prompt))
                system_emitted = True
        elif ev.type is EventType.USER_MESSAGE:
            messages.append(Message(role=Role.USER, content=p.get("text", "")))
        elif ev.type is EventType.ASSISTANT_MESSAGE:
            messages.append(Message(role=Role.ASSISTANT, content=p.get("content")))
        elif ev.type is EventType.TOOL_CALL:
            tc = p.get("tool_call", {})
            messages.append(Message(
                role=Role.ASSISTANT,
                content=p.get("content"),
                tool_calls=[ToolCall(
                    id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", {}),
                )],
            ))
        elif ev.type is EventType.TOOL_RESULT:
            messages.append(Message(
                role=Role.TOOL,
                content=p.get("content", ""),
                tool_call_id=p.get("tool_call_id", ""),
                name=p.get("name", ""),
            ))
    return messages
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_session.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/session.py tests/test_session.py
git commit -m "harness: derive_messages 事件到消息投影"
```

---

### Task 5: 工具注册表 `tools/registry.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/tools/registry.py`
- Test: `packages/synlys-harness/tests/test_registry.py`

- [ ] **Step 1: 写失败测试 `tests/test_registry.py`**

```python
"""ToolRegistry 单测。"""
import pytest

from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import Permission, ToolResult


@tool(
    name="echo",
    description="回声工具",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    timeout_s=5,
)
async def echo(ctx, args):
    """回声。"""
    return ToolResult(ok=True, content=args["text"])


def test_register_and_get():
    """装饰器注册后可按名获取。"""
    reg = ToolRegistry()
    reg.register(echo)
    assert reg.get("echo").name == "echo"
    assert reg.get("echo").permission is Permission.ALLOW


def test_duplicate_register_raises():
    """重名注册抛 ValueError。"""
    reg = ToolRegistry()
    reg.register(echo)
    with pytest.raises(ValueError):
        reg.register(echo)


def test_llm_schemas_filter():
    """llm_schemas 只包含白名单内工具且为 function calling 格式。"""
    reg = ToolRegistry()
    reg.register(echo)
    schemas = reg.llm_schemas(allowed=["echo"])
    assert schemas == [{
        "type": "function",
        "function": {"name": "echo", "description": "回声工具", "parameters": {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
        }},
    }]
    assert reg.llm_schemas(allowed=["not-registered"]) == []


def test_unregister():
    """注销后 get 抛 KeyError。"""
    reg = ToolRegistry()
    reg.register(echo)
    reg.unregister("echo")
    with pytest.raises(KeyError):
        reg.get("echo")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_registry.py -v
```

Expected: FAIL（`cannot import name 'ToolRegistry'`）

- [ ] **Step 3: 实现 `src/synlys_harness/tools/registry.py`**

```python
"""工具注册表与 @tool 装饰器。"""
from __future__ import annotations

import inspect

from ..types import ToolDefinition, ToolExecuteFn


def tool(
    name: str,
    description: str,
    parameters: dict,
    timeout_s: float = 60.0,
    permission=None,
    concurrency_safe: bool = True,
):
    """把异步函数包装为 ToolDefinition 的装饰器。

    Args:
        name: 工具唯一名（如 python.run）。
        description: 给 LLM 看的能力描述。
        parameters: JSON Schema（function calling 的 parameters 字段）。
        timeout_s: 执行超时（由管线实施）。
        permission: 权限级别，默认 ALLOW。
        concurrency_safe: 是否允许与其他工具并行执行。

    Returns:
        装饰器：函数原样返回，但附加 __tool_definition__ 属性。
    """
    from ..types import Permission
    perm = permission or Permission.ALLOW

    def decorator(fn: ToolExecuteFn) -> ToolExecuteFn:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"工具 {name} 的 execute 必须是 async 函数")
        fn.__tool_definition__ = ToolDefinition(
            name=name, description=description, parameters=parameters,
            execute=fn, timeout_s=timeout_s, permission=perm,
            concurrency_safe=concurrency_safe,
        )
        return fn

    return decorator


class ToolRegistry:
    """工具注册表：注册、查询与 LLM schema 生成。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, fn_or_def) -> None:
        """注册工具（@tool 装饰过的函数或 ToolDefinition）。

        Args:
            fn_or_def: 带 __tool_definition__ 的函数或 ToolDefinition 实例。

        Raises:
            ValueError: 名字重复。
        """
        definition = getattr(fn_or_def, "__tool_definition__", fn_or_def)
        if not isinstance(definition, ToolDefinition):
            raise TypeError("register 需要 @tool 装饰的函数或 ToolDefinition")
        if definition.name in self._tools:
            raise ValueError(f"工具已注册: {definition.name}")
        self._tools[definition.name] = definition

    def unregister(self, name: str) -> None:
        """按名注销工具。

        Args:
            name: 工具名。

        Raises:
            KeyError: 工具不存在。
        """
        del self._tools[name]

    def get(self, name: str) -> ToolDefinition:
        """按名获取工具定义。

        Args:
            name: 工具名。

        Returns:
            ToolDefinition。

        Raises:
            KeyError: 工具不存在。
        """
        return self._tools[name]

    def llm_schemas(self, allowed: list[str] | None = None) -> list[dict]:
        """生成 function calling 的 tools 数组。

        Args:
            allowed: 工具白名单（None 表示全部注册工具）。

        Returns:
            OpenAI tools 格式的 schema 列表。
        """
        names = allowed if allowed is not None else list(self._tools)
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name, "description": d.description, "parameters": d.parameters,
                },
            }
            for n in names if (d := self._tools.get(n)) is not None
        ]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_registry.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/tools/registry.py tests/test_registry.py
git commit -m "harness: ToolRegistry 与 @tool 装饰器"
```

---

### Task 6: 工具执行管线 `tools/pipeline.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/tools/pipeline.py`
- Test: `packages/synlys-harness/tests/test_pipeline.py`

四段：`pre-execute`（存在性/白名单/required 参数校验 → 不通过返回 error result）→ `execute`（`asyncio.timeout` 包裹，异常捕获）→ `post-execute`（content 截断到 `max_output_chars`，默认 65536，置 `truncated`）→ 返回结果（emit 由调用方 agent loop 负责，管线不落事件）。

- [ ] **Step 1: 写失败测试 `tests/test_pipeline.py`**

```python
"""工具管线单测。"""
import asyncio

from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import ToolContext, ToolResult


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path)


@tool(name="add", description="加法", parameters={
    "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
})
async def add(ctx, args):
    """加法。"""
    return ToolResult(ok=True, content=str(args["a"] + args["b"]))


@tool(name="boom", description="抛错", parameters={"type": "object", "properties": {}})
async def boom(ctx, args):
    """抛错。"""
    raise RuntimeError("炸了")


@tool(name="slow", description="慢", parameters={"type": "object", "properties": {}}, timeout_s=0.05)
async def slow(ctx, args):
    """慢。"""
    await asyncio.sleep(5)
    return ToolResult(ok=True, content="never")


@tool(name="big", description="大输出", parameters={"type": "object", "properties": {}})
async def big(ctx, args):
    """大输出。"""
    return ToolResult(ok=True, content="x" * 100)


def _pipeline() -> tuple[ToolPipeline, ToolRegistry]:
    reg = ToolRegistry()
    reg.register(add); reg.register(boom); reg.register(slow); reg.register(big)
    return ToolPipeline(registry=reg), reg


async def test_execute_ok(tmp_path):
    """正常执行返回工具结果。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1, "b": 2})
    assert r.ok and r.content == "3"


async def test_unknown_tool(tmp_path):
    """未注册工具返回 error result（不抛异常）。"""
    pipe, _ = _pipeline()
    r = await pipe.run("nope", _ctx(tmp_path), {})
    assert not r.ok and r.error == "unknown_tool"


async def test_missing_required_arg(tmp_path):
    """缺 required 参数返回 error result。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1})
    assert not r.ok and r.error == "invalid_arguments"


async def test_exception_becomes_error_result(tmp_path):
    """工具内部异常捕获为 error result，不中断 run。"""
    pipe, _ = _pipeline()
    r = await pipe.run("boom", _ctx(tmp_path), {})
    assert not r.ok and "炸了" in r.content


async def test_timeout(tmp_path):
    """超时被杀并返回 timeout error result。"""
    pipe, _ = _pipeline()
    r = await pipe.run("slow", _ctx(tmp_path), {})
    assert not r.ok and r.error == "timeout"


async def test_post_execute_truncation(tmp_path):
    """输出超过 max_output_chars 被截断并标记 truncated。"""
    pipe, _ = _pipeline()
    r = await pipe.run("big", _ctx(tmp_path), {}, max_output_chars=10)
    assert r.ok and r.truncated and len(r.content) == 10


async def test_deny_by_not_allowed(tmp_path):
    """不在 allowed 白名单内直接拒绝。"""
    pipe, _ = _pipeline()
    r = await pipe.run("add", _ctx(tmp_path), {"a": 1, "b": 2}, allowed=["other"])
    assert not r.ok and r.error == "denied"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_pipeline.py -v
```

Expected: FAIL（`cannot import name 'ToolPipeline'`）

- [ ] **Step 3: 实现 `src/synlys_harness/tools/pipeline.py`**

```python
"""工具四段执行管线：pre-execute → execute → post-execute → 返回。"""
from __future__ import annotations

import asyncio

from ..types import ToolContext, ToolResult
from .registry import ToolRegistry


class PipelineError(Exception):
    """管线层错误（已转换为 error result，不向外抛）。"""


class ToolPipeline:
    """统一工具执行入口：校验、超时、异常捕获与输出截断。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """初始化管线。

        Args:
            registry: 工具注册表。
        """
        self._registry = registry

    async def run(
        self,
        name: str,
        ctx: ToolContext,
        args: dict,
        allowed: list[str] | None = None,
        max_output_chars: int = 65_536,
    ) -> ToolResult:
        """执行一次工具调用（四段管线）。

        Args:
            name: 工具名。
            ctx: 执行上下文。
            args: LLM 给出的参数。
            allowed: 本次 run 的工具白名单（None 表示注册表全部）。
            max_output_chars: post-execute 截断阈值。

        Returns:
            ToolResult（任何失败都体现为 ok=False，不抛异常）。
        """
        # --- pre-execute ---
        def err(code: str, message: str = "") -> ToolResult:
            return ToolResult(ok=False, content=message or code, error=code)

        definition = self._registry._tools.get(name)
        if definition is None:
            return err("unknown_tool", f"工具不存在: {name}")
        if allowed is not None and name not in allowed:
            return err("denied", f"工具不在白名单: {name}")
        if not isinstance(args, dict):
            return err("invalid_arguments", "参数必须是 JSON 对象")
        for key in definition.parameters.get("required", []):
            if key not in args:
                return err("invalid_arguments", f"缺少必填参数: {key}")

        # --- execute（around：超时 + 异常捕获）---
        try:
            async with asyncio.timeout(definition.timeout_s):
                result = await definition.execute(ctx, args)
        except TimeoutError:
            return err("timeout", f"工具执行超时（>{definition.timeout_s}s）")
        except Exception as exc:  # noqa: BLE001 工具错误必须对 LLM 可见
            return ToolResult(ok=False, content=f"工具执行出错: {exc}", error="tool_exception")

        # --- post-execute（截断）---
        if result.content is not None and len(result.content) > max_output_chars:
            result.content = result.content[:max_output_chars]
            result.truncated = True
        return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_pipeline.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/tools/pipeline.py tests/test_pipeline.py
git commit -m "harness: 工具四段执行管线（校验/超时/异常捕获/截断）"
```

---

### Task 7: 受限沙箱执行器 `tools/sandbox.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/tools/sandbox.py`
- Test: `packages/synlys-harness/tests/test_sandbox.py`

- [ ] **Step 1: 写失败测试 `tests/test_sandbox.py`**

```python
"""python.run 沙箱执行器单测。"""
import sys

from synlys_harness.tools.sandbox import run_python


async def test_simple_execution(tmp_path):
    """正常执行并捕获输出。"""
    r = await run_python("print('hi')", cwd=tmp_path, timeout_s=10)
    assert r.ok and "hi" in r.content


async def test_timeout_kills(tmp_path):
    """死循环超时被杀。"""
    r = await run_python("while True: pass", cwd=tmp_path, timeout_s=0.5)
    assert not r.ok and r.data["timed_out"] is True


async def test_output_truncation(tmp_path):
    """输出超过上限被截断。"""
    r = await run_python("print('x' * 5000)", cwd=tmp_path, timeout_s=10, max_output_bytes=1000)
    assert r.truncated and len(r.content.encode()) <= 1200


async def test_isolated_env(tmp_path, monkeypatch):
    """-I 隔离模式：进程看不到注入的环境变量。"""
    monkeypatch.setenv("SYNLYS_SECRET", "leak")
    r = await run_python(
        "import os; print(os.environ.get('SYNLYS_SECRET', 'clean'))",
        cwd=tmp_path, timeout_s=10,
    )
    assert "clean" in r.content and "leak" not in r.content


async def test_writes_artifacts_to_cwd(tmp_path):
    """脚本写入的文件落在受限 cwd 中。"""
    r = await run_python(
        "open('out.txt','w').write('data')", cwd=tmp_path, timeout_s=10,
    )
    assert r.ok and (tmp_path / "out.txt").read_text() == "data"


async def test_uses_same_interpreter_family(tmp_path):
    """沙箱解释器与当前环境一致（可用已装依赖）。"""
    r = await run_python("import sys; print(sys.version_info[0])", cwd=tmp_path, timeout_s=10)
    assert r.ok and r.content.strip().startswith("3")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_sandbox.py -v
```

Expected: FAIL（`cannot import name 'run_python'`）

- [ ] **Step 3: 实现 `src/synlys_harness/tools/sandbox.py`**

```python
"""受限 Python 子进程执行器（python.run 的实现核心）。"""
from __future__ import annotations

import asyncio
import sys

from ..types import ToolResult

ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")


async def run_python(
    code: str,
    cwd,
    timeout_s: float = 60.0,
    max_output_bytes: int = 65_536,
) -> ToolResult:
    """在受限子进程中执行 Python 代码。

    限制：解释器 -I（隔离模式，忽略用户 site 与环境变量）；环境变量白名单；
    cwd 锁定；stdout+stderr 合并截断；超时杀进程。

    Args:
        code: 要执行的 Python 源码。
        cwd: 工作目录（调用方保证已限制在用户沙箱内）。
        timeout_s: 超时秒数。
        max_output_bytes: 输出字节上限。

    Returns:
        ToolResult：content 为合并输出；data 含 exit_code/timed_out。
    """
    import os

    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", code,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as exc:
        return ToolResult(ok=False, content=f"无法启动沙箱进程: {exc}", error="spawn_failed")

    timed_out = False
    try:
        async with asyncio.timeout(timeout_s):
            raw, _ = await proc.communicate()
    except TimeoutError:
        timed_out = True
        proc.kill()
        raw, _ = await proc.communicate()

    text = raw.decode("utf-8", errors="replace")
    truncated = len(raw) > max_output_bytes
    if truncated:
        text = text[: max_output_bytes // 4]  # 截断为字符数（UTF-8 宽松）
    if timed_out:
        return ToolResult(
            ok=False, content=f"执行超时（>{timeout_s}s），进程已终止。\n部分输出:\n{text}",
            error="timeout", data={"timed_out": True},
        )
    return ToolResult(
        ok=proc.returncode == 0,
        content=text or "(无输出)",
        truncated=truncated,
        data={"exit_code": proc.returncode, "timed_out": False},
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_sandbox.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/tools/sandbox.py tests/test_sandbox.py
git commit -m "harness: 受限 Python 沙箱（-I 隔离/环境白名单/超时/截断）"
```

---

### Task 8: 内置工具 `tools/builtin.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/tools/builtin.py`
- Test: `packages/synlys-harness/tests/test_builtin.py`

内置 6 工具：`file.read` / `file.write` / `file.list` / `python.run` / `knowledge.search`（mock）/ `http.request`（受限 GET，白名单取 `ctx.extra["http_allowed_hosts"]`）。所有文件操作做路径逃逸防护。

- [ ] **Step 1: 写失败测试 `tests/test_builtin.py`**

```python
"""内置工具单测。"""
import pytest

from synlys_harness.tools.builtin import register_builtin_tools
from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry
from synlys_harness.types import ToolContext

EXPECTED = ["file.read", "file.write", "file.list", "python.run", "knowledge.search", "http.request"]


def _setup() -> tuple[ToolPipeline, ToolContext, pytest.TempPathFactory]:
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return ToolPipeline(registry=reg), None, reg


def _ctx(tmp_path, extra=None) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path, extra=extra or {})


def test_all_registered():
    """六个内置工具全部注册成功。"""
    _, _, reg = _setup()
    assert sorted(reg._tools) == sorted(EXPECTED)


async def test_file_roundtrip_and_escape_guard(tmp_path):
    """写入→读取正常；路径逃逸被拒。"""
    pipe, _, _ = _setup()
    ctx = _ctx(tmp_path)
    w = await pipe.run("file.write", ctx, {"path": "a/b.txt", "content": "hello"})
    assert w.ok and (tmp_path / "a" / "b.txt").read_text() == "hello"
    r = await pipe.run("file.read", ctx, {"path": "a/b.txt"})
    assert r.ok and r.content == "hello"
    esc = await pipe.run("file.read", ctx, {"path": "../../etc/passwd"})
    assert not esc.ok and esc.error == "path_escape"
    esc2 = await pipe.run("file.write", ctx, {"path": "../evil.txt", "content": "x"})
    assert not esc2.ok and esc2.error == "path_escape"


async def test_file_list(tmp_path):
    """file.list 返回相对路径列表。"""
    pipe, _, _ = _setup()
    ctx = _ctx(tmp_path)
    (tmp_path / "x.csv").write_text("1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.txt").write_text("2")
    r = await pipe.run("file.list", ctx, {"path": "."})
    assert r.ok and "x.csv" in r.content and "sub/y.txt" in r.content


async def test_python_run_tool(tmp_path):
    """python.run 工具走沙箱执行。"""
    pipe, _, _ = _setup()
    r = await pipe.run("python.run", _ctx(tmp_path), {"code": "print(6*7)"})
    assert r.ok and "42" in r.content


async def test_knowledge_search_mock(tmp_path):
    """knowledge.search 返回 WeKnora 形状的 mock 结果。"""
    pipe, _, _ = _setup()
    r = await pipe.run("knowledge.search", _ctx(tmp_path), {"query": "聚酰亚胺", "top_k": 3})
    assert r.ok and r.data["mock"] is True
    assert r.data["results"][0]["source"] == "mock"


async def test_http_request_host_guard(tmp_path):
    """非白名单域名被拒；白名单内放行（用本地 httpbin 替身 mock httpx）。"""
    pipe, _, _ = _setup()
    ctx = _ctx(tmp_path, extra={"http_allowed_hosts": ["api.example.com"]})
    denied = await pipe.run("http.request", ctx, {"url": "http://evil.com/x"})
    assert not denied.ok and denied.error == "host_denied"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_builtin.py -v
```

Expected: FAIL（`cannot import name 'register_builtin_tools'`）

- [ ] **Step 3: 实现 `src/synlys_harness/tools/builtin.py`**

```python
"""内置工具集：file.* / python.run / knowledge.search / http.request。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..types import Permission, ToolContext, ToolResult
from .registry import ToolRegistry, tool
from .sandbox import run_python


def _safe_path(root: Path | None, rel: str) -> Path | None:
    """解析相对路径并确保不逃逸出 root。

    Args:
        root: 工作区根目录（None 表示未挂载工作区）。
        rel: 用户/LLM 给出的相对路径。

    Returns:
        绝对 Path；逃逸或未挂载时返回 None。
    """
    if root is None:
        return None
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None
    return target


def _no_workspace() -> ToolResult:
    """当前会话未挂载工作区的统一错误。"""
    return ToolResult(ok=False, content="当前会话未挂载用户工作区", error="no_workspace")


@tool(
    name="file.read",
    description="读取用户工作区中的文本文件内容",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对工作区根的路径"},
    }, "required": ["path"]},
    timeout_s=10,
)
async def file_read(ctx: ToolContext, args: dict) -> ToolResult:
    """读取工作区文件。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    path = _safe_path(ctx.workspace_root, args["path"])
    if path is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not path.is_file():
        return ToolResult(ok=False, content=f"文件不存在: {args['path']}", error="not_found")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(ok=False, content=f"读取失败: {exc}", error="io_error")
    return ToolResult(ok=True, content=text, data={"path": args["path"], "size": len(text)})


@tool(
    name="file.write",
    description="把文本内容写入用户工作区文件（自动创建父目录）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, "required": ["path", "content"]},
    timeout_s=10,
)
async def file_write(ctx: ToolContext, args: dict) -> ToolResult:
    """写入工作区文件。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    path = _safe_path(ctx.workspace_root, args["path"])
    if path is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    return ToolResult(ok=True, content=f"已写入 {args['path']}（{len(args['content'])} 字符）")


@tool(
    name="file.list",
    description="列出用户工作区指定目录下的文件（递归相对路径）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对路径，默认 '.'"},
    }},
    timeout_s=10,
)
async def file_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出工作区文件树。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    base = _safe_path(ctx.workspace_root, args.get("path", "."))
    if base is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not base.exists():
        return ToolResult(ok=False, content=f"目录不存在: {args.get('path')}", error="not_found")
    files = [
        str(p.relative_to(ctx.workspace_root.resolve()))
        for p in base.rglob("*") if p.is_file()
    ]
    return ToolResult(ok=True, content="\n".join(files) or "(空目录)", data={"files": files})


@tool(
    name="python.run",
    description="在用户沙箱中执行 Python 代码（隔离模式，可读写沙箱文件，输出受限）。适合数据分析与绘图。",
    parameters={"type": "object", "properties": {
        "code": {"type": "string", "description": "要执行的 Python 源码"},
    }, "required": ["code"]},
    timeout_s=60,
)
async def python_run(ctx: ToolContext, args: dict) -> ToolResult:
    """在用户沙箱 tmp/ 下执行代码。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    cwd = ctx.workspace_root / "tmp"
    cwd.mkdir(parents=True, exist_ok=True)
    result = await run_python(args["code"], cwd=cwd, timeout_s=60)
    result.data["cwd"] = str(cwd)
    return result


@tool(
    name="knowledge.search",
    description="在科研知识库中检索相关资料（当前为示例实现，正式接入 WeKnora 后语义不变）",
    parameters={"type": "object", "properties": {
        "query": {"type": "string"}, "knowledge_base": {"type": "string"},
        "top_k": {"type": "integer", "default": 5},
    }, "required": ["query"]},
    timeout_s=30,
)
async def knowledge_search(ctx: ToolContext, args: dict) -> ToolResult:
    """mock 知识检索：返回 WeKnora 形状的占位结果。"""
    query = args["query"]
    results = [{
        "content": f"[mock] 关于「{query}」的检索占位结果 #{i + 1}",
        "source": "mock", "page": None, "score": round(0.9 - i * 0.1, 2),
    } for i in range(min(args.get("top_k", 5), 5))]
    return ToolResult(
        ok=True,
        content="\n\n".join(r["content"] for r in results),
        data={"results": results, "mock": True, "knowledge_base": args.get("knowledge_base", "default")},
    )


@tool(
    name="http.request",
    description="发起受限 HTTP GET 请求（仅允许白名单域名，用于访问内部平台 API）",
    parameters={"type": "object", "properties": {
        "url": {"type": "string"},
    }, "required": ["url"]},
    timeout_s=30,
)
async def http_request(ctx: ToolContext, args: dict) -> ToolResult:
    """白名单内的 GET 请求。"""
    allowed_hosts: list[str] = ctx.extra.get("http_allowed_hosts", [])
    host = urlparse(args["url"]).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        return ToolResult(ok=False, content=f"域名不在白名单: {host}", error="host_denied")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            resp = await client.get(args["url"])
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"请求失败: {exc}", error="http_error")
    return ToolResult(
        ok=resp.status_code < 400,
        content=resp.text[:65_536],
        data={"status_code": resp.status_code},
        truncated=len(resp.text) > 65_536,
    )


def register_builtin_tools(registry: ToolRegistry) -> None:
    """把全部内置工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    for fn in (file_read, file_write, file_list, python_run, knowledge_search, http_request):
        registry.register(fn)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_builtin.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/tools/builtin.py tests/test_builtin.py
git commit -m "harness: 内置六工具（file.*/python.run/knowledge.search/http.request）"
```

---

### Task 9: LLM 后端 `models/backend.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/models/backend.py`
- Test: `packages/synlys-harness/tests/test_backend.py`

定义 `LLMBackend` 协议（async iterator 产出 `TextDelta | ToolCallChunk | Usage`）+ `OpenAICompatibleBackend`（openai SDK 流式 + tool call delta 聚合）。单测只测**聚合逻辑**（用注入的 fake stream chunk 列表），不真实联网。

- [ ] **Step 1: 写失败测试 `tests/test_backend.py`**

```python
"""LLM 后端单测：tool-call delta 聚合与流式文本。"""
import pytest

from synlys_harness.models.backend import ModelProviderConfig, aggregate_stream


def _provider() -> ModelProviderConfig:
    return ModelProviderConfig(
        name="test", base_url="http://localhost:9999/v1", api_key="sk-x", model_id="qwen3",
    )


def test_provider_config():
    """配置模型可构造。"""
    p = _provider()
    assert p.model_id == "qwen3"


class _Chunk:
    """模拟 openai 流式 chunk。"""

    def __init__(self, deltas, usage=None):
        self.choices = [type("C", (), {"delta": type("D", (), deltas)})()]
        self.usage = usage


async def _aiter(items):
    for x in items:
        yield x


async def test_aggregate_text_and_usage():
    """文本 delta 直通，usage 透传。"""
    chunks = [
        _Chunk({"content": "你"}), _Chunk({"content": "好"}),
        _Chunk({}, usage=type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    kinds = [(type(e).__name__, getattr(e, "text", None)) for e in events]
    assert kinds == [("TextDelta", "你"), ("TextDelta", "好"), ("Usage", None)]
    assert events[-1].prompt_tokens == 10


async def test_aggregate_tool_call_deltas():
    """分片到达的 tool call 参数被聚合成完整 ToolCallChunk。"""
    chunks = [
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 0, "id": "c1", "function": type("F", (), {"name": "python.run", "arguments": '{"code"'}),
        })()]}),
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 0, "id": None, "function": type("F", (), {"name": None, "arguments": ': "print(1)"}'),
        })()]}),
        _Chunk({"tool_calls": [type("TC", (), {
            "index": 1, "id": "c2", "function": type("F", (), {"name": "file.read", "arguments": '{"path":"a"}'}),
        })()]}),
    ]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    calls = [e for e in events if type(e).__name__ == "ToolCallChunk"]
    assert len(calls) == 2
    assert calls[0].id == "c1" and calls[0].name == "python.run"
    assert calls[0].arguments == {"code": "print(1)"}
    assert calls[1].name == "file.read"


async def test_aggregate_invalid_tool_args_become_error_text():
    """非法 JSON 参数聚合为带 error 的 ToolCallChunk（由 loop 转为错误结果）。"""
    chunks = [_Chunk({"tool_calls": [type("TC", (), {
        "index": 0, "id": "c9", "function": type("F", (), {"name": "x", "arguments": "{oops"}),
    })()]})]
    events = [e async for e in aggregate_stream(_aiter(chunks))]
    call = events[0]
    assert call.arguments_error is not None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_backend.py -v
```

Expected: FAIL（`cannot import name 'ModelProviderConfig'`）

- [ ] **Step 3: 实现 `src/synlys_harness/models/backend.py`**

```python
"""LLM 后端：协议定义、流事件与 OpenAI 兼容实现。"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ..types import Message, Role


class ModelProviderConfig(BaseModel):
    """一个 OpenAI 兼容模型服务的连接配置（宿主从 DB 读出后传入）。"""

    name: str
    base_url: str
    api_key: str
    model_id: str


class TextDelta(BaseModel):
    """流式文本增量。"""

    text: str


class ToolCallChunk(BaseModel):
    """聚合完成的工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_error: str | None = None


class Usage(BaseModel):
    """本次请求 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0


StreamEvent = TextDelta | ToolCallChunk | Usage


@runtime_checkable
class LLMBackend(Protocol):
    """LLM 后端协议：流式产出 StreamEvent。"""

    def stream(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用 LLM。

        Args:
            messages: 完整消息序列。
            tools: function calling schema（None 表示不带工具）。

        Yields:
            TextDelta / ToolCallChunk / Usage。
        """
        ...


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """把内部 Message 转为 OpenAI chat 格式。"""
    out: list[dict] = []
    for m in messages:
        if m.role is Role.TOOL:
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content or ""})
        elif m.role is Role.ASSISTANT and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                } for tc in m.tool_calls],
            })
        else:
            out.append({"role": m.role.value, "content": m.content or ""})
    return out


async def aggregate_stream(raw_stream: AsyncIterator[Any]) -> AsyncIterator[StreamEvent]:
    """聚合 openai SDK 原始流：文本直通、tool-call delta 按索引拼装、usage 透传。

    Args:
        raw_stream: chat.completions.create(stream=True) 的异步迭代器。

    Yields:
        StreamEvent。
    """
    calls: dict[int, dict] = {}
    async for chunk in raw_stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield Usage(prompt_tokens=usage.prompt_tokens or 0, completion_tokens=usage.completion_tokens or 0)
        for choice in getattr(chunk, "choices", []) or []:
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            text = getattr(delta, "content", None)
            if text:
                yield TextDelta(text=text)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if getattr(tc.function, "name", None):
                    slot["name"] += tc.function.name
                if getattr(tc.function, "arguments", None):
                    slot["args"] += tc.function.arguments
    for slot in calls.values():
        error: str | None = None
        try:
            arguments = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError as exc:
            arguments, error = {}, f"工具参数 JSON 解析失败: {exc}"
        yield ToolCallChunk(
            id=slot["id"], name=slot["name"], arguments=arguments, arguments_error=error,
        )


class OpenAICompatibleBackend:
    """OpenAI 兼容流式后端（vLLM/Ollama/云 API 通用）。"""

    def __init__(self, provider: ModelProviderConfig) -> None:
        """初始化后端。

        Args:
            provider: 模型服务连接配置。
        """
        self._provider = provider
        self._client = AsyncOpenAI(base_url=provider.base_url, api_key=provider.api_key)

    async def stream(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用（见 LLMBackend 协议）。"""
        kwargs: dict[str, Any] = {
            "model": self._provider.model_id,
            "messages": _to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        raw = await self._client.chat.completions.create(**kwargs)
        async for event in aggregate_stream(raw):
            yield event
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_backend.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/models/backend.py tests/test_backend.py
git commit -m "harness: LLM 后端协议与 OpenAI 兼容流式实现（tool-call delta 聚合）"
```

---

### Task 10: Agent Loop `agent.py`

**Files:**
- Create: `packages/synlys-harness/src/synlys_harness/agent.py`
- Test: `packages/synlys-harness/tests/test_agent_loop.py`

`RunSession.run(user_text)` 是 async generator：产出全部 SessionEvent（同时写入 EventLog）。step 循环：steer 注入 → `before_llm_call` 钩子 → 流式调 LLM（逐 delta 发 `llm/delta`）→ 文本结束发 `assistant_message` 并 break；有 tool call 则发 `tool/call` → 管线执行 → 发 `tool/result` → 下一 step。`max_steps` 用尽发 `error`。cancel 每步检查（流式每 delta 也检查），发 `turn/aborted`。

- [ ] **Step 1: 写失败测试 `tests/test_agent_loop.py`**

```python
"""agent loop 单测（FakeBackend 脚本化驱动）。"""
import asyncio

from synlys_harness.agent import RunSession
from synlys_harness.events import EventLog
from synlys_harness.models.backend import TextDelta, ToolCallChunk, Usage
from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry, tool
from synlys_harness.types import AgentConfig, EventType, ExtensionHooks, ToolResult


@tool(name="add", description="加法", parameters={
    "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
})
async def add(ctx, args):
    """加法。"""
    return ToolResult(ok=True, content=str(args["a"] + args["b"]))


class FakeBackend:
    """脚本化后端：按预设剧本依次返回事件列表。"""

    def __init__(self, script: list[list]):
        """script: 每次调用返回一个 StreamEvent 列表。"""
        self.script = script
        self.calls: list[list] = []

    async def stream(self, messages, tools=None):
        """按剧本产出。"""
        self.calls.append(messages)
        for ev in self.script.pop(0):
            yield ev


def _session(backend, hooks=None, max_steps=25) -> RunSession:
    reg = ToolRegistry()
    reg.register(add)
    log = EventLog()
    cfg = AgentConfig(system_prompt="你是助手", tool_names=["add"], max_steps=max_steps)
    return RunSession(
        config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
        backend=backend, event_log=log, user_id="u1", run_id="r1", hooks=hooks,
    )


async def _collect(session: RunSession, text: str):
    return [ev async for ev in session.run(text)]


async def test_plain_text_turn():
    """纯文本回复：llm/delta 流出 + assistant_message + turn_end。"""
    backend = FakeBackend([[TextDelta(text="你"), TextDelta(text="好"), Usage(prompt_tokens=3, completion_tokens=2)]])
    events = await _collect(_session(backend), "嗨")
    types = [e.type for e in events]
    assert EventType.TURN_START in types and EventType.USER_MESSAGE in types
    deltas = [e for e in events if e.type is EventType.LLM_DELTA]
    assert "".join(d["text"] for d in (e.payload for e in deltas)) == "你好"
    assert types[-1] is EventType.TURN_END
    assert EventType.ASSISTANT_MESSAGE in types


async def test_tool_call_then_answer():
    """工具调用轮：tool/call + tool/result 后续答 turn 恰好结束。"""
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2})],
        [TextDelta(text="和是 3"), Usage(prompt_tokens=10, completion_tokens=5)],
    ])
    events = await _collect(_session(backend), "1+2=?")
    tc = [e for e in events if e.type is EventType.TOOL_CALL][0]
    tr = [e for e in events if e.type is EventType.TOOL_RESULT][0]
    assert tc.payload["tool_call"]["name"] == "add"
    assert tr.payload["content"] == "3" and tr.payload["ok"] is True
    # 第二次 LLM 调用应看到 tool 结果消息
    second_call = backend.calls[1]
    assert any(m.role.value == "tool" and m.content == "3" for m in second_call)


async def test_max_steps_guard():
    """步数上限：LLM 持续要求工具时最终发 error 并收尾。"""
    endless = [ToolCallChunk(id=f"c{i}", name="add", arguments={"a": 1, "b": 1}) for i in range(1)]
    backend = FakeBackend([list(endless) for _ in range(3)])
    events = await _collect(_session(backend, max_steps=3), "loop")
    assert EventType.ERROR in [e.type for e in events]


async def test_cancel_mid_stream():
    """流式中途取消：发 turn/aborted 并停止。"""
    started = asyncio.Event()

    class SlowBackend:
        async def stream(self, messages, tools=None):
            started.set()
            for i in range(100):
                await asyncio.sleep(0.01)
                yield TextDelta(text="x")

    session = _session(SlowBackend())
    task = asyncio.create_task(_collect(session, "hi"))
    await started.wait()
    session.cancel()
    events = await task
    assert EventType.TURN_ABORTED in [e.type for e in events]
    assert EventType.TURN_END not in [e.type for e in events]


async def test_steering_injected_next_step():
    """中途插话注入下一 step 的 user 消息。"""
    gate = asyncio.Event()

    @tool(name="wait", description="等待", parameters={"type": "object", "properties": {}})
    async def wait_tool(ctx, args):
        """等待 gate。"""
        await gate.wait()
        return ToolResult(ok=True, content="done")

    reg = ToolRegistry(); reg.register(add); reg.register(wait_tool)
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="wait", arguments={})],
        [TextDelta(text="ok")],
    ])
    log = EventLog()
    cfg = AgentConfig(system_prompt="s", tool_names=["add", "wait"])
    session = RunSession(config=cfg, registry=reg, pipeline=ToolPipeline(registry=reg),
                         backend=backend, event_log=log, user_id="u1", run_id="r1")
    task = asyncio.create_task(_collect(session, "开始"))
    await asyncio.sleep(0.05)
    session.steer("插句话")
    gate.set()
    events = await task
    assert EventType.TURN_END in [e.type for e in events]
    # 第二次 LLM 调用消息里含插话 user 消息
    steering_msgs = [m for m in backend.calls[1] if m.role.value == "user" and m.content == "插句话"]
    assert steering_msgs


async def test_extension_hooks_invoked():
    """四钩子中 before_llm_call 可改写消息、on_session_start/end 被调用。"""
    seen = {}

    async def on_start(ctx):
        seen["start"] = True

    async def before_llm(messages):
        seen["count"] = len(messages)
        return messages

    async def on_tool_event(ev):
        seen.setdefault("tool_events", []).append(ev.type)

    async def on_end(ctx):
        seen["end"] = True

    hooks = ExtensionHooks(
        on_session_start=on_start, before_llm_call=before_llm,
        on_tool_event=on_tool_event, on_session_end=on_end,
    )
    backend = FakeBackend([
        [ToolCallChunk(id="c1", name="add", arguments={"a": 1, "b": 2})],
        [TextDelta(text="done")],
    ])
    await _collect(_session(backend, hooks=hooks), "算")
    assert seen["start"] and seen["end"] and seen["count"] >= 2
    assert EventType.TOOL_CALL in seen["tool_events"] and EventType.TOOL_RESULT in seen["tool_events"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_loop.py -v
```

Expected: FAIL（`cannot import name 'RunSession'`）

- [ ] **Step 3: 实现 `src/synlys_harness/agent.py`**

```python
"""Agent loop：turn/step 双层循环（RunSession）。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from .events import EventLog
from .models.backend import LLMBackend, StreamEvent, TextDelta, ToolCallChunk, Usage
from .session import derive_messages
from .tools.pipeline import ToolPipeline
from .tools.registry import ToolRegistry
from .types import (
    AgentConfig, EventType, ExtensionHooks, Message, Role, SessionEvent, ToolCall,
)


class RunSession:
    """一次会话的运行时：消费用户输入，产出全部会话事件。"""

    def __init__(
        self,
        config: AgentConfig,
        registry: ToolRegistry,
        pipeline: ToolPipeline,
        backend: LLMBackend,
        event_log: EventLog,
        user_id: str,
        run_id: str,
        hooks: ExtensionHooks | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        """初始化运行会话。

        Args:
            config: agent 组装配置。
            registry: 工具注册表。
            pipeline: 工具执行管线。
            backend: LLM 后端。
            event_log: 事件日志（seq 分配与 sink 广播）。
            user_id: 用户标识（进入 ToolContext）。
            run_id: 本次运行标识。
            hooks: 扩展钩子（可 None）。
            workspace_root: 用户工作区根目录（文件/沙箱工具依赖；None 表示未挂载）。
        """
        self._config = config
        self._registry = registry
        self._pipeline = pipeline
        self._backend = backend
        self._log = event_log
        self._user_id = user_id
        self._run_id = run_id
        self._hooks = hooks
        self._workspace_root = workspace_root
        self._cancel = asyncio.Event()
        self._steering: asyncio.Queue[str] = asyncio.Queue()
        self._last_usage: Usage | None = None

    def cancel(self) -> None:
        """请求中止（下一个检查点生效）。"""
        self._cancel.set()

    def steer(self, text: str) -> None:
        """中途插话：注入下一个 step 的 user 消息。"""
        self._steering.put_nowait(text)

    async def _emit(self, type_: EventType, payload: dict) -> SessionEvent:
        """追加事件（yield 由 run 转发）。"""
        return await self._log.append(type_, payload)

    async def _fire_tool_hook(self) -> None:
        """触发 on_tool_event 钩子（tool/call 与 tool/result 均触发）。"""
        if self._hooks and self._hooks.on_tool_event:
            await self._hooks.on_tool_event(self._log.events[-1])

    async def run(self, user_text: str) -> AsyncIterator[SessionEvent]:
        """执行一轮 turn：产出全部事件。

        Args:
            user_text: 用户输入文本。

        Yields:
            SessionEvent（同时写入 EventLog 供 sink 持久化）。
        """
        from .types import ToolContext  # 延迟导入避免循环

        if self._hooks and self._hooks.on_session_start:
            await self._hooks.on_session_start(self)

        await self._emit(EventType.TURN_START, {
            "system_prompt": self._config.system_prompt, "user_id": self._user_id, "run_id": self._run_id,
        })
        await self._emit(EventType.USER_MESSAGE, {"text": user_text})
        yield self._log.events[-2]
        yield self._log.events[-1]

        ctx = ToolContext(
            user_id=self._user_id, run_id=self._run_id, workspace_root=self._workspace_root,
        )
        aborted = False
        try:
            for step in range(self._config.max_steps):
                if self._cancel.is_set():
                    aborted = True
                    break
                while not self._steering.empty():
                    steer_text = await self._steering.get()
                    await self._emit(EventType.USER_MESSAGE, {"text": steer_text, "steering": True})
                    yield self._log.events[-1]

                messages = [Message(role=Role.SYSTEM, content=self._config.system_prompt)] + derive_messages(
                    self._log.events, include_system=False
                )
                if self._hooks and self._hooks.before_llm_call:
                    messages = await self._hooks.before_llm_call(messages)

                text_parts: list[str] = []
                tool_calls: list[ToolCallChunk] = []
                async for ev in self._backend.stream(messages, self._registry.llm_schemas(self._config.tool_names)):
                    if isinstance(ev, TextDelta):
                        await self._emit(EventType.LLM_DELTA, {"text": ev.text, "step": step})
                        yield self._log.events[-1]
                        text_parts.append(ev.text)
                    elif isinstance(ev, ToolCallChunk):
                        tool_calls.append(ev)
                    elif isinstance(ev, Usage):
                        self._last_usage = ev

                if self._cancel.is_set():
                    aborted = True
                    break

                if text_parts and not tool_calls:
                    await self._emit(EventType.ASSISTANT_MESSAGE, {"content": "".join(text_parts)})
                    yield self._log.events[-1]
                    break

                if not tool_calls:
                    break

                for tc in tool_calls:
                    if tc.arguments_error is not None:
                        await self._emit(EventType.TOOL_CALL, {
                            "tool_call": {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                            "content": None,
                        })
                        yield self._log.events[-1]
                        await self._fire_tool_hook()
                        result = await self._pipeline.run(tc.name, ctx, {}, allowed=self._config.tool_names)
                        result = result.model_copy(update={
                            "ok": False, "error": "invalid_tool_arguments", "content": tc.arguments_error,
                        })
                    else:
                        await self._emit(EventType.TOOL_CALL, {
                            "tool_call": {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                            "content": "".join(text_parts) or None,
                        })
                        yield self._log.events[-1]
                        await self._fire_tool_hook()
                        result = await self._pipeline.run(
                            tc.name, ctx, tc.arguments, allowed=self._config.tool_names,
                        )
                    await self._emit(EventType.TOOL_RESULT, {
                        "tool_call_id": tc.id, "name": tc.name,
                        "ok": result.ok, "content": result.content,
                        "error": result.error, "truncated": result.truncated,
                    })
                    yield self._log.events[-1]
                    await self._fire_tool_hook()
            else:
                await self._emit(EventType.ERROR, {"code": "max_steps", "message": f"达到步数上限 {self._config.max_steps}"})
                yield self._log.events[-1]
        finally:
            if aborted:
                await self._emit(EventType.TURN_ABORTED, {"step_reached": True})
                yield self._log.events[-1]
            else:
                await self._emit(EventType.TURN_END, {})
                yield self._log.events[-1]
            if self._hooks and self._hooks.on_session_end:
                await self._hooks.on_session_end(self)
```

**实现说明（写代码时注意）**：
1. `ToolContext.workspace_root` 类型为 `Path | None = None`（Task 2 已定义）；`RunSession.__init__` 接受可选 `workspace_root` 参数构造 ctx，文件/沙箱工具在未挂载时返回 `error="no_workspace"`（Task 8 已实现）。
2. for/else：`else` 分支在循环**未被 break** 时触发（max_steps 用尽），此时 `aborted` 仍为 False，会再发 ERROR + TURN_END——符合"事件流完整收尾"的语义。
3. yield 的事件对象直接取 `self._log.events[-1]`（`_emit` 已返回该事件，实现时可改用返回值，效果等同）。

- [ ] **Step 4: 运行全部 harness 测试确认通过**

```bash
conda run -n synlysagent --no-capture-output python -m pytest -v
```

Expected: 全部 passed（约 37 项）

- [ ] **Step 5: Commit**

```bash
git add src/synlys_harness/agent.py tests/test_agent_loop.py src/synlys_harness/types.py
git commit -m "harness: RunSession agent loop（turn/step、steering、cancel、钩子、max_steps）"
```

---

### Task 11: 公共导出与收尾

**Files:**
- Modify: `packages/synlys-harness/src/synlys_harness/__init__.py`
- Modify: `packages/synlys-harness/src/synlys_harness/tools/__init__.py`
- Modify: `packages/synlys-harness/src/synlys_harness/models/__init__.py`
- Create: `packages/synlys-harness/README.md`

- [ ] **Step 1: 写 `__init__.py` 公共导出**

`src/synlys_harness/__init__.py`：

```python
"""SynlysAgent 核心 Agent 运行时。"""
from .agent import RunSession
from .events import EventLog
from .models.backend import (
    LLMBackend, ModelProviderConfig, OpenAICompatibleBackend, TextDelta, ToolCallChunk, Usage,
)
from .session import derive_messages
from .tools.builtin import register_builtin_tools
from .tools.pipeline import ToolPipeline
from .tools.registry import ToolRegistry, tool
from .types import (
    AgentConfig, EventType, ExtensionHooks, Message, Permission, Role,
    SessionEvent, ToolCall, ToolContext, ToolDefinition, ToolResult,
)

__all__ = [
    "RunSession", "EventLog", "derive_messages",
    "ToolRegistry", "tool", "ToolPipeline", "register_builtin_tools",
    "LLMBackend", "OpenAICompatibleBackend", "ModelProviderConfig",
    "TextDelta", "ToolCallChunk", "Usage",
    "AgentConfig", "EventType", "ExtensionHooks", "Message", "Permission",
    "Role", "SessionEvent", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult",
]
```

`src/synlys_harness/tools/__init__.py`：

```python
"""tools 子包。"""
from .builtin import register_builtin_tools
from .pipeline import ToolPipeline
from .registry import ToolRegistry, tool

__all__ = ["ToolRegistry", "tool", "ToolPipeline", "register_builtin_tools"]
```

`src/synlys_harness/models/__init__.py`：

```python
"""models 子包。"""
from .backend import (
    LLMBackend, ModelProviderConfig, OpenAICompatibleBackend, TextDelta, ToolCallChunk, Usage,
)

__all__ = [
    "LLMBackend", "ModelProviderConfig", "OpenAICompatibleBackend",
    "TextDelta", "ToolCallChunk", "Usage",
]
```

- [ ] **Step 2: 写 `packages/synlys-harness/README.md`**

````markdown
# synlys-harness

SynlysAgent 的核心 Agent 运行时（纯 Python，零 Web 依赖）。

## 架构

- `types.py`：核心数据模型（事件、消息、工具定义、Agent 配置）
- `events.py`：`EventLog` 事件日志（seq 分配 + sinks 广播）
- `session.py`：`derive_messages()` 事件 → LLM 消息投影
- `tools/`：注册表、四段执行管线、受限沙箱、内置工具
- `models/`：LLM 后端协议与 OpenAI 兼容流式实现
- `agent.py`：`RunSession`（turn/step 循环、steering、cancel、扩展钩子）

## 最小用法

```python
from synlys_harness import (
    AgentConfig, EventLog, OpenAICompatibleBackend, ModelProviderConfig,
    RunSession, ToolPipeline, ToolRegistry, register_builtin_tools,
)

registry = ToolRegistry()
register_builtin_tools(registry)
log = EventLog(sinks=[...])          # 宿主接持久化/SSE
backend = OpenAICompatibleBackend(ModelProviderConfig(
    name="local", base_url="http://localhost:8000/v1",
    api_key="sk-...", model_id="qwen3-32b",
))
session = RunSession(
    config=AgentConfig(system_prompt="你是科研助手", tool_names=["python.run"]),
    registry=registry, pipeline=ToolPipeline(registry=registry),
    backend=backend, event_log=log, user_id="u1", run_id="r1",
)
async for event in session.run("帮我算 1+1"):
    print(event.type, event.payload)
```

## 测试

```bash
conda run -n synlysagent python -m pytest packages/synlys-harness/tests -v
```
````

- [ ] **Step 3: 全量测试 + 导入冒烟**

```bash
cd packages/synlys-harness
conda run -n synlysagent --no-capture-output python -m pytest -v
conda run -n synlysagent --no-capture-output python -c "import synlys_harness as h; print(sorted(h.__all__))"
cd ../..
```

Expected: 测试全绿；`__all__` 列表完整打印

- [ ] **Step 4: Commit**

```bash
git add packages/
git commit -m "harness: 公共导出与包 README

- synlys_harness 顶层导出全部核心 API
- 最小用法示例与测试命令"
```

---

## 完成标准（Plan 1 DoD）

- [ ] `conda run -n synlysagent python -m pytest packages/synlys-harness/tests -v` 全绿
- [ ] `import synlys_harness` 可用且 `__all__` 完整
- [ ] 六个内置工具 + 管线 + loop + steering/cancel + 钩子均有测试覆盖
- [ ] 每个任务一次提交，共约 10 个提交

## 后续计划

- Plan 2：FastAPI web 后端（DocumentStore 双后端、共享认证、assistants/providers CRUD、SSE 对话、文件 API、运行编排）
- Plan 3：React 前端（DSH 主题、三栏工作台、管理页）+ 集成验收
