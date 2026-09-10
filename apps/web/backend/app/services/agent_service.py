"""AgentService：harness 组装、事件持久化（JSONL + DB 副本）与 SSE 内存队列。

遵守 Plan 2 harness 接入契约：
- sink 不得抛异常（宿主 sink 全包 try/except，持久化失败不杀对话）；
- SSE 推送只 put 内存队列，绝不等待消费者（慢客户端不拖 LLM 流）；
- 消费 run() 用 contextlib.aclosing 包裹；
- SSE 断连不 cancel：run 由 _drive 独立 asyncio task 后台执行完落盘，
  重连经 GET /sessions/{id}/events?after_seq=N 补齐；cancel 仅用户显式触发。
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from pathlib import Path
from typing import Any

from synlys_harness import (
    AgentConfig,
    EventLog,
    ModelProviderConfig,
    OpenAICompatibleBackend,
    RunSession,
    SessionEvent,
    ToolPipeline,
    ToolRegistry,
    register_builtin_tools,
)

MAX_RUNS_PER_USER = 2  # 每用户并发运行上限（超出 API 层转 429）

_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)
_PIPELINE = ToolPipeline(registry=_REGISTRY)


class TooManyRuns(Exception):
    """用户并发运行数超限。"""


class ActiveRun:
    """一次进行中的对话运行（queue 供 SSE 消费，done 标记收尾完成）。"""

    def __init__(self) -> None:
        """初始化队列与事件。"""
        self.queue: asyncio.Queue = asyncio.Queue()
        self.done = asyncio.Event()
        self.session: RunSession | None = None


class AgentService:
    """对话运行编排（单例，挂 app.state.agent_service）。"""

    def __init__(self, store: Any, settings: Any, event_repo: Any) -> None:
        """保存依赖。

        Args:
            store: DocumentStore（runs 直查直写）。
            settings: 应用配置（数据根/白名单）。
            event_repo: 会话事件 repo（DB 副本写入与回放）。
        """
        self._store = store
        self._settings = settings
        self._event_repo = event_repo
        self._runs: dict[str, ActiveRun] = {}

    def _jsonl_path(self, session_id: str) -> Path:
        """会话事件文件路径（父目录自动创建）。

        Args:
            session_id: 会话 id。

        Returns:
            {data_root}/sessions/{session_id}/events.jsonl。
        """
        p = self._settings.data_root / "sessions" / session_id / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_active(self, run_id: str) -> ActiveRun | None:
        """取运行中的 ActiveRun（SSE 生成器消费其 queue）。

        Args:
            run_id: 运行 id。

        Returns:
            ActiveRun；run 已结束被摘除时返回 None。
        """
        return self._runs.get(run_id)

    async def chat(self, session_id: str, user: dict, assistant: dict,
                   provider_cfg: ModelProviderConfig, text: str) -> str:
        """启动一轮对话运行，返回 run_id（事件经 ActiveRun.queue 流出）。

        Args:
            session_id: 会话 id。
            user: 当前用户 payload（sub）。
            assistant: 助手文档（system_prompt/tool_whitelist）。
            provider_cfg: 已解密的模型服务配置。
            text: 用户消息文本。

        Returns:
            run_id。

        Raises:
            TooManyRuns: 该用户运行中的对话已达上限。
        """
        running = await self._store.list("runs", filters={
            "user_id": user["sub"], "status": "running"})
        if len(running) >= MAX_RUNS_PER_USER:
            raise TooManyRuns(f"该用户已有 {MAX_RUNS_PER_USER} 个运行中的对话")
        run_id = uuid.uuid4().hex[:12]
        active = ActiveRun()
        self._runs[run_id] = active

        async def jsonl_sink(event: SessionEvent) -> None:
            """事件追加 JSONL（回放源；契约：不得抛异常）。"""
            try:
                with self._jsonl_path(session_id).open("a", encoding="utf-8") as f:
                    f.write(event.model_dump_json() + "\n")
            except OSError:
                pass

        async def db_sink(event: SessionEvent) -> None:
            """事件写 DB 副本 + SSE 队列只 put（契约：不得抛异常、不等待消费者）。"""
            try:
                await self._event_repo.append(session_id, event)
            except Exception:
                pass
            try:
                active.queue.put_nowait(event)
            except Exception:
                pass

        log = EventLog(sinks=[jsonl_sink, db_sink])
        # 会话级 seq 连续性依赖 seed 恢复：用 DB 历史事件预填充本轮日志，
        # 使 seq 跨轮续号（DB _id=f"{sid}:{seq}" 不碰撞）、derive_messages
        # 能投影出前几轮消息（LLM 对话记忆）；首轮会话历史为空跳过。
        history = await self._event_repo.list_events(session_id)
        if history:
            log.seed(history)
        backend = OpenAICompatibleBackend(provider_cfg)
        workspace = self._settings.data_root / "workspaces" / user["sub"]
        workspace.mkdir(parents=True, exist_ok=True)
        session = RunSession(
            config=AgentConfig(
                system_prompt=assistant["system_prompt"],
                tool_names=assistant.get("tool_whitelist") or [],
            ),
            registry=_REGISTRY, pipeline=_PIPELINE, backend=backend,
            event_log=log, user_id=user["sub"], run_id=run_id,
            workspace_root=workspace,
            context_extra={"http_allowed_hosts": self._settings.allowed_hosts},
        )
        active.session = session
        asyncio.create_task(self._drive(run_id, session, text, user["sub"], session_id))
        return run_id

    async def _drive(self, run_id: str, session: RunSession, text: str,
                     user_id: str, session_id: str) -> None:
        """后台驱动 run 至完成并落盘终态（独立于 SSE 消费者，断连不中断）。

        Args:
            run_id: 运行 id。
            session: harness 运行会话。
            text: 用户消息文本。
            user_id: 用户 sub。
            session_id: 会话 id。
        """
        try:
            await self._store.insert("runs", {
                "_id": run_id, "session_id": session_id, "user_id": user_id,
                "status": "running", "started_at": time.time(),
            })
        except Exception:
            pass  # run 记录失败不阻断对话流（并发限制会暂时失效，属存储故障降级）
        final_status = "completed"
        try:
            stream = session.run(text)
            async with contextlib.aclosing(stream):
                async for _event in stream:
                    pass  # 事件已由 sinks 持久化并入队
        except Exception:
            final_status = "failed"
        # harness 取消旗标（未暴露公共 API，接入契约确认可读）：用户显式 cancel 优先于异常归类
        if session._cancel.is_set():  # noqa: SLF001
            final_status = "aborted"
        try:
            await self._store.update("runs", run_id, {
                "status": final_status, "ended_at": time.time()})
        finally:
            active = self._runs.pop(run_id, None)
            if active:
                active.queue.put_nowait(None)  # SSE 结束哨兵（任何路径都必须放，防 SSE 挂死）
                active.done.set()

    async def cancel(self, run_id: str) -> bool:
        """取消运行（仅用户显式停止；SSE 断连不走此路径）。

        Args:
            run_id: 运行 id。

        Returns:
            是否找到仍在内存注册表中的运行并发出取消。
        """
        active = self._runs.get(run_id)
        if active and active.session is not None:
            active.session.cancel()
            return True
        return False

    async def events_after(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        """取 seq 大于 after_seq 的会话事件（SSE 断连重连增量补齐用）。

        Args:
            session_id: 会话 id。
            after_seq: 客户端已收到的最大 seq。

        Returns:
            seq 升序的事件列表。
        """
        events = await self._event_repo.list_events(session_id)
        return [e for e in events if e.seq > after_seq]
