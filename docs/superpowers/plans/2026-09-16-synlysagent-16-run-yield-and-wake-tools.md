# 会话让位与唤醒轮收窄实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉"会话被 run 永久占住、用户什么都做不了"的死锁——用户的新消息永远能推进（可让位的 run 自动让位），后台唤醒轮不再能反过来堵住用户，卡在 ask 上的 run 有超时兜底，重启后不再累积残留 `running` 记录把用户永久封顶。

**Architecture:** 不动架构，只改 `AgentService` 的三处语义：① **准入**——`chat()` 撞上占位时，若占位者全是"可让位"的 run（后台唤醒轮，或停在 ask 上等回答的轮）则先抢占再放行，让不掉才维持 `TooManyRuns`；② **唤醒轮收窄**——后台唤醒轮属于无人值守的自动轮，不给 `ask_user`、审批一律拒绝（DSH 的"无交互通道则 deny"）；③ **等待有上限**——ask_user 等回答加超时，超时按"跳过"继续，run 不会永久挂起。另加一条启动清理，把重启前残留的 `running` run 收敛为 `aborted`。

**Tech Stack:** Python 3.12 / FastAPI / asyncio / pydantic v2（`DocumentStore` sqlite+mongodb 双后端）/ pytest（`asyncio_mode = "auto"`）；conda 环境 `synlysagent`。**前端零改动。**

---

## 执行记录：落地时对本文档的修正

编码期发现并修正了以下偏差。**重跑本文档时必须按这里为准**：

1. **`_admit_run` 不循环重试，只抢占一次**（Task 2 的 `while True` 版本已废弃）。原因：取消是协作式的，run 若正卡在模型的网络调用里，要等下一个检查点才注意到旗标；`while True` + 每轮 5s 会把用户消息阻塞成**多个 5s 段**（实测 25s，比原来的 429 更糟）。改为「抢占一次 → 若占位仍在则抛 `TooManyRuns("会话正忙，请稍候重试")`」，并有测试 `test_admit_run_gives_up_after_bounded_wait` 守边界。
2. **`_preempt_runs` 的等待预算在多个 run 间共享**（一个 `deadline`），不是每个 run 各等 `PREEMPT_TIMEOUT_S`。
3. **`ASK_TIMEOUT_S` 定为 300.0（5 分钟）**，初期稿是 600.0。
4. 侧效应（已知、可接受）：管线强制审批复用 `ask_handler`，因此**审批 5 分钟无人应答也走超时分支**；`pipeline.py` 判定 `reply.strip() != "允许"` 即拒绝，故超时等价于 fail-closed 拒绝，不会误放行。

### 实测结论（2026-09-16）

- 全量 `apps/web/backend` 562 passed / 193 skipped；`packages/synlys-harness` 161 passed。
- 端到端实测（真实 `chat()` 路径起唤醒轮 → 用户消息同时到达）：**用户消息不再被 429 拒**；唤醒轮让位失败时在 5.0s 预算内明确拒绝，不再无限重试。
- 「停在 ask 上的 run 被用户消息抢占」由 `test_preempt_unblocks_run_parked_on_ask` 守住（漏解 future 会因 `done` 不置位而失败）。

---

## 0. 背景：本次要修的真实故障

2026-09-16 实测（会话 `ccd23f0ca787`）：

- 后台任务 `job-ca92df06968f` 完成 → `JobService` 唤醒 → 服务端起了一轮 run `8bef98b095af`。
- 该轮 run 整合完结果后调了 `ask_user` 弹问答卡，**run 停在等回答上**（事件流末尾：`tool/call` → `ask/user`，无 answer）。
- 会话因此被占住：用户打字 → `chat()` 的会话互斥抛 `TooManyRuns` → API 429 → 前端弹"该会话已有进行中的消息，请稍候或停止后重试"。
- **而用户点不了那张卡**：`AskUserCard.tsx:68` 是 `interactive = streaming && !answered`，`pickPendingAsk`（`stores/chat.ts:85`）首行是 `if (!streaming) return null`；而 `streaming` 只在前端自己发起的 `send()` 里置真（`chat.ts:541`），后台唤醒轮全程 `streaming=false`。
- 唯一出路是重启后端（`_active_by_session` 是内存态）。
- 残留风险：`MAX_RUNS_PER_USER = 2`（`agent_service.py:56`），每次进程被杀都会在 `runs` 里留一条永久 `status: running`，`chat()` 用 DB 计数判上限 → **再撞一次就永久封顶，重启也救不回**。

### 根因

`JobService` 把"后台任务完成"实现成了"起一轮模拟用户消息的 run"，于是它继承了 `chat()` 的**会话级互斥**（`_active_by_session`）。而那条互斥的设计前提是"两条消息都来自用户"——后台唤醒轮不满足这个前提，却承担了同样的约束。两处放大：

1. 唤醒轮的工具集没有收窄，`ask_user` 在里面（`chat()` 的 `tool_names` 只按专家白名单/可见性过滤，`wake_source` 不参与）。
2. 工具执行处 `await self._pipeline.run(...)`（harness `agent.py:299`）内部 await ask future，**沿途没有任何取消检查点**——`session.cancel()` 只置旗标（`agent.py:87-89`），叫不醒停在 ask 上的 run。任何"抢占"实现若不显式解掉这个 future，都会在 `await done` 处一起挂死。

### 参考依据（本机参考项目实查）

| | JiuwenSwarm | DSH（本项目架构参考源） |
|---|---|---|
| 用户 vs 后台 run | 用户消息**抢占**后台 run：`coordinator.py:750-775` 直接 `mark_terminal(CANCELLED)`，源码注释即设计声明 *"an arriving user must be able to preempt this background execution."* | run 进行中 `send/inject/steer/followup` **始终接受**，driver 在 turn/step 边界 `inbox.claim()`（`agent-loop/src/inbox.ts:111-116`） |
| 后台 run 弹 ask | `has_pending_interaction` 阻止新心跳进入有未答交互的 session；session 忙超 60s `skip_and_reschedule`；心跳自身有硬超时 | approval seam 在工具管线内 await；**无 seam 则降级为 deny，不挂起**（`tools/src/index.ts:1669-1693`） |

两个项目都**没有**"起一轮与用户消息互斥的新 run"这种做法；也都不允许后台轮把用户堵住。本计划按"用户优先 + 唤醒轮不交互"对齐，**不**做 DSH 的 inbox/driver 重构（见文末「明确不做」）。

---

## 1. 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `apps/web/backend/app/services/agent_service.py` | 修改 | 唤醒轮工具收窄；run 来源标记；可让位判定与抢占；ask 等待超时 |
| `apps/web/backend/app/db/repos.py` | 修改 | `RunRepo.abort_stale()`：把残留 `running` 收敛为 `aborted` |
| `apps/web/backend/app/main.py` | 修改 | lifespan 启动时调用 `abort_stale` |
| `apps/web/backend/tests/test_agent_wake.py` | 修改 | 让位/抢占/超时/收窄的单测 |

---

## 2. 关键契约（后续任务都依赖）

**1. 可让位（yieldable）**：一个 run 在下列任一情形下让位给用户的新消息——

- 它是**后台唤醒轮**（`_run_kind[run_id] == "wake"`）；或
- 它**停在 ask 上等回答**（`active.ask_future` 非 None 且未 done）。

其余情形（用户自己的前台轮正在跑）维持 `TooManyRuns`——前端 `send()` 已有 `if (get().streaming) return` 拦住重复发送，这条只剩多标签页场景。

**2. 抢占必须显式解 future**：抢占 = 「解掉待答 future → 置取消旗标 → 等收尾（带超时）」，三步顺序不可省，理由见 §0 根因第 2 条。

**3. 唤醒轮不交互**：`wake_source` 非空的轮次不给 `ask_user` 工具；该轮的审批一律返回"拒绝"（不打断、不挂起）。

**4. 常量**：

```python
PREEMPT_TIMEOUT_S = 5.0    # 抢占后等待 run 收尾的上限
ASK_TIMEOUT_S = 300.0      # ask_user 等待回答上限
WAKE_BLOCKED_TOOLS = frozenset({"ask_user"})   # 唤醒轮不提供的工具
```

---

## Task 1: 唤醒轮工具收窄

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Test: `apps/web/backend/tests/test_agent_wake.py`

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_agent_wake.py` 末尾：

```python
def test_narrow_tools_for_wake_drops_interactive():
    """唤醒轮工具收窄：去掉需要用户在场的交互工具，其余原样保留。"""
    from app.services.agent_service import narrow_tools_for_wake

    assert narrow_tools_for_wake(["file.read", "ask_user", "job.submit"]) == [
        "file.read", "job.submit"]
    # 不含交互工具时原样返回（不得误删）
    assert narrow_tools_for_wake(["file.read"]) == ["file.read"]
    assert narrow_tools_for_wake([]) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v -k narrow_tools
```

Expected: FAIL — `ImportError: cannot import name 'narrow_tools_for_wake'`

- [ ] **Step 3: 实现收窄函数与常量**

在 `apps/web/backend/app/services/agent_service.py` 的 `MAX_RUNS_PER_USER` 常量附近（第 56 行）改为：

```python
MAX_RUNS_PER_USER = 2  # 每用户并发运行上限（超出 API 层转 429）

# 抢占后等待 run 收尾的上限（秒）：解掉 future + 置旗标后正常应在毫秒级收尾，
# 给 5s 只是防"收尾路径自身有问题"时把抢占方一起拖住
PREEMPT_TIMEOUT_S = 5.0

# ask_user 等待回答的上限（秒）：没有上限时，一个停在 ask 上的 run 会永久
# 占住会话（用户关掉页面就再也没人来回答它）
ASK_TIMEOUT_S = 300.0

# 唤醒轮（后台任务完成触发、前台无人值守）不提供的工具：唤醒轮再弹问答卡
# 只会把会话锁死——它不属于用户当前的注意力，用户也不一定正在看这个页面
WAKE_BLOCKED_TOOLS = frozenset({"ask_user"})


def narrow_tools_for_wake(tool_names: list[str]) -> list[str]:
    """收窄后台唤醒轮的工具集（去掉需要用户在场的交互工具）。

    Args:
        tool_names: 按专家白名单与可见性算出的工具名列表。

    Returns:
        去掉 `WAKE_BLOCKED_TOOLS` 后的列表；顺序与输入一致。

    对齐 DSH 的"工具没有交互通道就 deny"：本平台对应"压根不给这个工具"，
    比拿到工具再拒绝更省一次 LLM 往返。
    """
    return [t for t in tool_names if t not in WAKE_BLOCKED_TOOLS]
```

- [ ] **Step 4: 在 chat() 里应用**

在 `chat()` 中，`tool_names` 完成可见性过滤之后（第 423 行 `]` 收尾、`# ask_user：发 ask/user 事件` 注释之前）插入：

```python
            if wake_source:
                # 后台唤醒轮无人值守：不给交互工具（详见 narrow_tools_for_wake）
                tool_names = narrow_tools_for_wake(tool_names)
```

- [ ] **Step 5: 唤醒轮审批直接拒绝**

修改同文件 `approval_handler`（第 438 行起），在函数体最前面加一段：

```python
            async def approval_handler(payload: dict) -> str:
                if wake_source:
                    # 唤醒轮无人值守，不能停下来等审批（同 ask_user 的理由）；
                    # fail-closed 拒绝，让模型换条不需要审批的路径或直接说明
                    return "拒绝"
                tool = str(payload.get("tool", ""))
                preview = json.dumps(payload.get("args", {}), ensure_ascii=False)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v
```

Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_agent_wake.py
git commit -m "唤醒轮收窄为无人值守：不给交互工具、审批直接拒绝

- 后台唤醒轮弹问答卡会把会话锁死（用户不在场、前端也不认为 run 在跑）
- 对齐 DSH「工具无交互通道则 deny」，此处改为压根不给该工具"
```

---

## Task 2: run 来源标记、可让位判定与抢占

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Test: `apps/web/backend/tests/test_agent_wake.py`

> **本任务的核心是顺序**：抢占必须**先解 future、再置旗标、最后才等收尾**。harness 的工具执行处（`agent.py:299` 的 `await self._pipeline.run(...)`）内部 await ask future，沿途没有取消检查点——只置旗标的话 run 会卡在那个 await 上永不结束，抢占方的 `await done` 一起挂死。下面的测试专门守这一条。

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_agent_wake.py` 末尾：

```python
def _register_run(service, session_id, run_id, *, kind="chat", ask_pending=False):
    """在内存注册表里造一个 run（免去跑真实 LLM）。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    service._runs[run_id] = active          # noqa: SLF001
    service._run_kind[run_id] = kind        # noqa: SLF001
    service._active_by_session.setdefault(session_id, set()).add(run_id)  # noqa: SLF001
    if ask_pending:
        active.ask_future = asyncio.get_running_loop().create_future()
    return active


async def test_yieldable_covers_wake_and_parked_ask(agent_service, session_doc):
    """可让位 = 后台唤醒轮（对谁都让）；停在 ask 上的轮只对用户消息让。"""
    service, sid = agent_service, session_doc["_id"]
    _register_run(service, sid, "wake-1", kind="wake")
    _register_run(service, sid, "parked-1", ask_pending=True)
    _register_run(service, sid, "busy-1")

    assert service._yieldable("wake-1", for_user=True) is True     # noqa: SLF001
    assert service._yieldable("wake-1", for_user=False) is True    # noqa: SLF001
    assert service._yieldable("parked-1", for_user=True) is True   # noqa: SLF001
    # 后台轮不得抢用户正等着回答的问题
    assert service._yieldable("parked-1", for_user=False) is False  # noqa: SLF001
    assert service._yieldable("busy-1", for_user=True) is False    # noqa: SLF001
    assert service._yieldable("ghost", for_user=True) is False     # noqa: SLF001


class _StubSession:
    """停在 ask 上的假会话：只有待答 future 被解掉才会收尾。

    同时按 `_drive` 的 finally 口径**同步**摘除注册表占位——`_drive` 里
    `done.set()` 与 `discard(run_id)` 之间没有 await，抢占方恢复执行时占位
    必然已释放；桩若用独立 task 做摘除，会造出生产里不存在的竞态。
    """

    def __init__(self, service, session_id, run_id, active):
        """保存宿主与所属 run。"""
        self._service = service
        self._session_id = session_id
        self._run_id = run_id
        self._active = active
        self.cancelled = False

    def cancel(self):
        """置取消旗标（真实实现只置旗标，叫不醒 await）+ 同步摘除占位。"""
        self.cancelled = True
        self._active.done.set()
        self._service._runs.pop(self._run_id, None)        # noqa: SLF001
        self._service._run_kind.pop(self._run_id, None)    # noqa: SLF001
        runs = self._service._active_by_session.get(self._session_id)  # noqa: SLF001
        if runs is not None:
            runs.discard(self._run_id)

    async def await_ask_then_finish(self):
        """模拟工具在管线内 await future（沿途无取消检查点）。"""
        await self._active.ask_future


async def test_preempt_unblocks_run_parked_on_ask(agent_service, session_doc):
    """抢占停在 ask 上的 run：必须解掉 future，否则 run 收不了尾、抢占一起挂死。

    tripwire：桩 session 只有 `ask_future` 被解掉才会返回，返回前 `cancel`
    已完成收尾并置位 `active.done`。实现若漏掉 `set_result`，`_preempt_runs`
    会一直等到 `PREEMPT_TIMEOUT_S` 超时，`active.done` 始终不置位，
    下面的断言即失败（而不是"跑得慢但通过"）。
    """
    service, sid = agent_service, session_doc["_id"]
    active = _register_run(service, sid, "parked-1", ask_pending=True)
    stub = _StubSession(service, sid, "parked-1", active)
    active.session = stub
    finisher = asyncio.create_task(stub.await_ask_then_finish())

    await service._preempt_runs(["parked-1"])  # noqa: SLF001

    assert stub.cancelled is True
    assert active.ask_future.done()
    assert active.ask_future.result() != ""
    assert active.done.is_set()          # run 真的收尾了（没被 await 卡住）
    assert not service._active_by_session.get(sid)  # noqa: SLF001
    await finisher
    assert finisher.done() and not finisher.cancelled()


async def test_admit_run_preempts_yieldable_then_allows(agent_service, session_doc):
    """准入：占位者全可让位时先抢占再放行，让不掉才抛 TooManyRuns。"""
    from app.services.agent_service import TooManyRuns

    service, sid = agent_service, session_doc["_id"]

    # 情形一：占位者是可让位的唤醒轮 → 用户消息抢占后放行
    active = _register_run(service, sid, "wake-1", kind="wake")
    active.session = _StubSession(service, sid, "wake-1", active)
    await service._admit_run(sid, wake=False)     # noqa: SLF001
    assert not service._active_by_session.get(sid)  # noqa: SLF001

    # 情形二：占位者是不可让位的前台轮 → 用户消息被拒
    _register_run(service, sid, "busy-1")
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid, wake=False)  # noqa: SLF001
    service._active_by_session.get(sid, set()).discard("busy-1")  # noqa: SLF001
    service._runs.pop("busy-1", None)             # noqa: SLF001
    service._run_kind.pop("busy-1", None)         # noqa: SLF001

    # 情形三：用户的前台轮停在 ask 上 → 唤醒轮不得抢占（会让用户的问题消失）
    parked = _register_run(service, sid, "parked-1", ask_pending=True)
    parked.session = _StubSession(service, sid, "parked-1", parked)
    with pytest.raises(TooManyRuns):
        await service._admit_run(sid, wake=True)   # noqa: SLF001
    assert parked.ask_future is not None and not parked.ask_future.done()
```

在文件顶部的 import 区补 `import asyncio`（若尚无）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v -k "yieldable or preempt or admit"
```

Expected: FAIL — `AttributeError: 'AgentService' object has no attribute '_yieldable'`

- [ ] **Step 3: 加 run 来源登记**

在 `AgentService.__init__` 的 `self._active_by_session` 之后加：

```python
        # run 来源：run_id → "chat"（用户发起）| "wake"（后台任务唤醒）。
        # 唤醒轮是可让位的自动轮，与用户消息不同权，见 _yieldable
        self._run_kind: dict[str, str] = {}
```

- [ ] **Step 4: 实现准入 / 可让位 / 抢占**

在 `AgentService` 的 `is_busy` 之后插入三个方法：

```python
    def _yieldable(self, run_id: str, *, for_user: bool) -> bool:
        """该 run 是否该让位给新来的这一轮。

        Args:
            run_id: 运行 id。
            for_user: 新来的是否为用户消息（False = 后台唤醒轮）。

        Returns:
            True = 可让位。后台唤醒轮一律让位（它不属于用户当前注意力）；
            停在 ask 上等回答的轮**只在用户消息来时才让位**——它本就在等
            用户输入，用户改用打字表达意愿应当让路，但后台轮不得抢走用户的
            问题（否则任务一完成就把用户正等着回答的卡干掉）。用户自己的
            前台轮正在干活时不让位。
        """
        if self._run_kind.get(run_id) == "wake":
            return True
        if not for_user:
            return False
        active = self._runs.get(run_id)
        return bool(active is not None
                    and active.ask_future is not None
                    and not active.ask_future.done())

    async def _preempt_runs(self, run_ids: list[str]) -> None:
        """抢占指定 run（让位给用户新消息），等其收尾。

        Args:
            run_ids: 待抢占的 run id 列表（调用方传入快照——本方法会触发
                注册表变更，边迭代边改会漏项）。

        Note:
            **必须先解掉停在 ask 上的 future**：harness 的工具执行处在
            `await self._pipeline.run(...)` 里等这个 future，沿途没有取消
            检查点（`_cancel` 只在 step 边界与 LLM 流内检查）。只置取消旗标
            的话 run 卡在那个 await 上永不收尾，本方法 `await done` 会一起挂死。
            解掉 future 后工具返回，循环走到下一个 step 边界即按取消退出。
        """
        actives = [self._runs.get(rid) for rid in run_ids]
        for active in actives:
            if active is None:
                continue
            if active.ask_future is not None and not active.ask_future.done():
                active.ask_future.set_result("（本轮已被新的用户消息中止）")
            if active.session is not None:
                active.session.cancel()
        for active in actives:
            if active is None:
                continue
            try:
                await asyncio.wait_for(active.done.wait(), PREEMPT_TIMEOUT_S)
            except TimeoutError:
                # 收尾超时不阻塞用户的新消息：占位会在 _drive 的 finally 里
                # 自然释放，此处只告警（真要卡住也已被 wait_for 断开）
                _LOGGER.warning("抢占 run 收尾超时，按已抢占继续")

    async def _admit_run(self, session_id: str, *, wake: bool) -> None:
        """会话准入：有占位时先尝试让位，让不掉才拒绝。

        Args:
            session_id: 会话 id。
            wake: 本次是否为后台唤醒轮。

        Raises:
            TooManyRuns: 会话已有不可让位的进行中 run。

        Note:
            **调用方必须在返回后同步完成占位**（`session_runs.add(run_id)`，
            中间不得插入 await）——本方法只在"无占位"时同步返回，占位的原子性
            由"检查与占位之间无 await"保证（原实现的前提，不能因引入抢占而丢）。
            循环重查是因为抢占含 await，期间可能有别的调用方抢先占位。
        """
        for_user = not wake
        while True:
            session_runs = self._active_by_session.setdefault(session_id, set())
            if not session_runs:
                return
            if not all(self._yieldable(rid, for_user=for_user)
                       for rid in session_runs):
                raise TooManyRuns("该会话已有进行中的消息")
            await self._preempt_runs(list(session_runs))
```

- [ ] **Step 5: 让 chat() 走准入并登记来源**

在 `chat()` 里，把这段（行号以 Task 1 编辑前为准；实际按代码块内容匹配）：

```python
        # 会话级互斥：检查与占位在同一同步段完成（中间无 await，并发请求
        # 串行执行到此即被拒）。同会话两个并发 run 会各自 seed 同一份历史
        # 快照、从相同 seq 起号，DB _id=f"{sid}:{seq}" 碰撞写入被 db_sink
        # 静默吞掉 → 事件拼接错乱/丢失，必须前置拒绝。
        session_runs = self._active_by_session.setdefault(session_id, set())
        if session_runs:
            raise TooManyRuns("该会话已有进行中的消息")
        run_id = uuid.uuid4().hex[:12]
        session_runs.add(run_id)
```

改为：

```python
        # 会话级互斥：同会话两个并发 run 会各自 seed 同一份历史快照、从相同
        # seq 起号，DB _id=f"{sid}:{seq}" 碰撞写入被 db_sink 静默吞掉 → 事件
        # 拼接错乱/丢失，必须前置拒绝。但"可让位的占位者"（后台唤醒轮、停在
        # ask 上的轮）先让位给用户消息再放行——让用户被它们挡住是最糟的选择。
        await self._admit_run(session_id, wake=bool(wake_source))
        run_id = uuid.uuid4().hex[:12]
        session_runs = self._active_by_session.setdefault(session_id, set())
        session_runs.add(run_id)
```

并在 `self._runs[run_id] = active`（Task 1 编辑前第 294 行）之后加一行：

```python
            self._run_kind[run_id] = "wake" if wake_source else "chat"
```

两处回滚点补摘除：

1. `_drive` 的 `finally`（第 586 行 `active = self._runs.pop(run_id, None)` 之后）：

```python
            self._run_kind.pop(run_id, None)
```

2. `chat()` 的失败回滚（第 532-533 行）：

```python
            self._runs.pop(run_id, None)
            self._run_kind.pop(run_id, None)
            session_runs.discard(run_id)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py tests/test_chat_api.py tests/test_jobs_e2e.py -v
```

Expected: 全绿（含既有发消息链路与唤醒链路回归）

- [ ] **Step 7: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_agent_wake.py
git commit -m "用户消息让可让位的 run 先让位，不再被后台轮堵死

- 可让位 = 后台唤醒轮 / 停在 ask 上等回答的轮；用户前台轮干活时仍拒绝
- 抢占顺序：先解 ask future 再置取消旗标再等收尾——工具 await 沿途无
  取消检查点，漏解 future 会让抢占方与 run 一起挂死"
```

---

## Task 3: ask_user 等待回答超时兜底

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Test: `apps/web/backend/tests/test_agent_wake.py`

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_agent_wake.py` 末尾：

```python
async def test_wait_for_answer_returns_text_on_timeout(agent_service):
    """等回答超时返回兜底文本（run 继续跑，而不是永久占住会话）。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    active.ask_future = asyncio.get_running_loop().create_future()
    text = await agent_service._wait_for_answer(active, 0.01)  # noqa: SLF001
    assert text and "跳过" in text


async def test_wait_for_answer_returns_user_text(agent_service):
    """正常路径原样返回用户回答。"""
    from app.services.agent_service import ActiveRun

    active = ActiveRun()
    active.ask_future = asyncio.get_running_loop().create_future()
    active.ask_future.set_result("选 B")
    assert await agent_service._wait_for_answer(active, 5) == "选 B"  # noqa: SLF001


async def test_wait_for_answer_without_future(agent_service):
    """无待答 future 时返回空串（防御：调用点不该出现，但不能抛）。"""
    from app.services.agent_service import ActiveRun

    assert await agent_service._wait_for_answer(ActiveRun(), 1) == ""  # noqa: SLF001
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v -k wait_for_answer
```

Expected: FAIL — `AttributeError: 'AgentService' object has no attribute '_wait_for_answer'`

- [ ] **Step 3: 实现并接入**

在 `answer()` 之后插入：

```python
    async def _wait_for_answer(self, active: "ActiveRun", timeout_s: float) -> str:
        """等待 ask_user 的回答，超时返回可继续的兜底文本。

        Args:
            active: 该 run 的 ActiveRun（调用前 ask_future 已置位）。
            timeout_s: 等待上限（秒）。

        Returns:
            用户回答文本；超时返回提示文本，让模型按已知信息继续——没有上限
            时，一个停在 ask 上的 run 会永久占住会话（用户关掉页面就再没人
            来回答它，只能靠重启后端）。

        Note:
            超时由 `asyncio.wait_for` 取消 future；`ask_handler` 的 finally
            会把 `active.ask_future` 置 None，`answer()` 也有 `not done()` 守卫，
            故随后到达的迟到回答不会撞上已取消的 future。
        """
        future = active.ask_future
        if future is None:
            return ""
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            _LOGGER.warning("ask_user 等待回答超时（%.0fs），本轮按跳过继续", timeout_s)
            return "（用户长时间未回答，已跳过此问题，请按已知信息继续）"
```

修改 `ask_handler`（第 426-433 行）为：

```python
            async def ask_handler(payload: dict) -> str:
                future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                active.ask_future = future
                try:
                    await log.append(EventType.ASK_USER, payload)
                    return await self._wait_for_answer(active, ASK_TIMEOUT_S)
                finally:
                    active.ask_future = None
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_agent_wake.py -v
```

Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_agent_wake.py
git commit -m "ask_user 等待回答加超时兜底

- 超时按「跳过」继续跑，run 不再可能永久停在 ask 上占住会话
- 配合让位逻辑：卡住的 run 最迟 5 分钟后自动收敛"
```

---

## Task 4: 启动清理残留 running run

**Files:**
- Modify: `apps/web/backend/app/db/repos.py`
- Modify: `apps/web/backend/app/main.py`
- Test: `apps/web/backend/tests/test_repos.py`

- [ ] **Step 1: 写失败测试**

追加到 `apps/web/backend/tests/test_repos.py` 末尾（并确认已 import `RunRepo`）：

```python
async def test_run_repo_abort_stale(store):
    """把残留的 status=running 收敛为 aborted，已完成的不动。"""
    repo = RunRepo(store)
    stale = await repo.create({"_id": "r-stale", "session_id": "s1",
                               "user_id": "u1", "status": "running",
                               "started_at": 1.0})
    done = await repo.create({"_id": "r-done", "session_id": "s1",
                              "user_id": "u1", "status": "completed",
                              "started_at": 1.0})
    n = await repo.abort_stale(ended_at=99.0)
    assert n == 1
    assert (await repo.get("r-stale"))["status"] == "aborted"
    assert (await repo.get("r-stale"))["ended_at"] == 99.0
    assert (await repo.get("r-done"))["status"] == "completed"   # 不误伤
    assert stale["_id"] and done["_id"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_repos.py -v -k abort_stale
```

Expected: FAIL — `AttributeError: 'RunRepo' object has no attribute 'abort_stale'`

- [ ] **Step 3: 实现 `RunRepo.abort_stale`**

在 `apps/web/backend/app/db/repos.py` 的 `RunRepo` 中追加方法：

```python
    async def abort_stale(self, ended_at: float) -> int:
        """把残留的 status=running 记录收敛为 aborted。

        进程重启后这些 run 必死（run 是进程内 asyncio task），但记录会永远
        停在 running；而 `chat()` 用 DB 计数判 `MAX_RUNS_PER_USER`，残留累积
        会把用户永久顶在"该用户已有 N 个运行中的对话"上，连重启都救不回。

        Args:
            ended_at: 结束时间戳（调用方传 `time.time()`）。

        Returns:
            被清理的记录数。
        """
        docs = await self.list(filters={"status": "running"})
        for doc in docs:
            await self.update(doc["_id"], {"status": "aborted", "ended_at": ended_at})
        return len(docs)
```

- [ ] **Step 4: 在 lifespan 启动时调用**

在 `apps/web/backend/app/main.py` 顶部 import 区补 `import time`（放在 `import logging` 之前，保持字母序）。

在 lifespan 里 `app.state.run_repo = RunRepo(store)` 之后插入：

```python
    # 进程重启后 run 记录会残留 running（run 是进程内 task，重启即死），
    # 而并发上限按 DB 计数判——不清理会让用户被"运行中的对话"永久封顶
    stale = await app.state.run_repo.abort_stale(time.time())
    if stale:
        logger.info("已清理重启前残留的进行中 run: %d 条", stale)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest tests/test_repos.py -v
```

Expected: 全绿

- [ ] **Step 6: 全量回归**

```bash
cd apps/web/backend
conda run -n synlysagent --no-capture-output python -m pytest -q
```

Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add apps/web/backend/app/db/repos.py apps/web/backend/app/main.py apps/web/backend/tests/test_repos.py
git commit -m "启动时清理重启前残留的 running run

- run 是进程内 task，重启即死但记录永远停在 running
- 并发上限按 DB 计数判，残留累积会把用户永久封顶（重启也救不回）"
```

---

## 验收清单（全部任务完成后逐条核对）

- [ ] `cd apps/web/backend && conda run -n synlysagent python -m pytest -q` 全绿
- [ ] `cd packages/synlys-harness && conda run -n synlysagent python -m pytest -q` 全绿（本计划不改 harness，作回归）
- [ ] `cd apps/web/frontend && npm run build` 通过（本计划不改前端，作回归）
- [ ] **端到端复现原故障并确认已修**：起后端 → 提交一个谱图后台任务 → 等它完成触发唤醒轮 → 在唤醒轮进行中向该会话发消息：**不再 429**，用户消息正常开跑，唤醒轮被中止
- [ ] **残留清理生效**：起后端看日志有无「已清理重启前残留的进行中 run: N 条」；`runs` 集合里不再有 `status: running` 的陈旧记录

## 明确不做（留给后续）

- **前端问答卡在刷新/后台轮下不可点**：`pickPendingAsk` / `AskUserCard.interactive` 依赖 `streaming`（`chat.ts:85`、`AskUserCard.tsx:68`），而 `streaming` 只在前端自己发起的 `send()` 里置真。本计划靠"可让位 + 超时"让用户**改用打字**就能推进，卡片本身仍点不了。要修需要：`GET /api/v1/sessions/{sid}/run-state`（暴露 busy/run_id/pending_ask）+ 前端改判定 + `answerAsk` 不再要求 `activeRunId`（刷新后前端根本拿不到 run_id，因为 run 控制接口全是 `/runs/{run_id}/…`）
- **后台轮的实时可见**：前端唯一的 SSE 连接开在 `send()` 里，服务端发起的唤醒轮全程无感（只在刷新后经 `loadHistory` 看到结果）。彻底解决需要常驻 SSE 订阅或 DSH 式的 inbox/driver 重构
- **inbox/driver 重构**：DSH 把"消息"与"驱动"分开（`inject` 入队、driver 在 turn/step 边界 `claim`），用户消息任何时候都入队、由 `take_queued_turn` 式边界消费，架构性地消灭 429 这个错误类别。本计划只做"用户优先 + 唤醒轮不交互"的等价收敛，不动驱动模型
- **唤醒轮的对话式汇报**：唤醒轮仍会烧一轮 LLM。纯数据获取型任务（把解析结果直接渲染进会话、不烧 LLM）需要另设"结果落会话"通道
