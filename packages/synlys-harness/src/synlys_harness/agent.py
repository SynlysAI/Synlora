"""Agent loop：turn/step 双层循环（RunSession）。"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import AsyncIterator

from .events import EventLog
from .models.backend import LLMBackend, TextDelta, ToolCallChunk, Usage
from .session import derive_messages
from .tools.pipeline import ToolPipeline
from .tools.registry import ToolRegistry
from .types import (
    AgentConfig,
    EventType,
    ExtensionHooks,
    Message,
    Role,
    SessionEvent,
    ToolContext,
    ToolResult,
)


class RunSession:
    """一次会话的运行时：消费用户输入，驱动 LLM/工具循环，产出全部会话事件。"""

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
            config: agent 组装配置（系统提示、工具白名单、步数上限）。
            registry: 工具注册表。
            pipeline: 工具执行管线。
            backend: LLM 后端（流式）。
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
        self._llm_failed = False

    @property
    def last_usage(self) -> Usage | None:
        """最近一次 LLM 调用的 token 用量（未收到 Usage 时为 None）。"""
        return self._last_usage

    def cancel(self) -> None:
        """请求中止（下一个检查点生效：step 开始或流消费结束）。"""
        self._cancel.set()

    def steer(self, text: str) -> None:
        """中途插话：注入下一个 step 开头的 user 消息。

        Args:
            text: 插话文本。
        """
        self._steering.put_nowait(text)

    async def _emit(self, type_: EventType, payload: dict) -> SessionEvent:
        """追加事件到日志并返回。

        Args:
            type_: 事件类型。
            payload: 事件负载。

        Returns:
            构造完成的 SessionEvent（seq 已分配），供 run 转发 yield。
        """
        return await self._log.append(type_, payload)

    async def _fire_tool_hook(self) -> None:
        """触发 on_tool_event 钩子（tool/call 与 tool/result 均触发）。"""
        if self._hooks and self._hooks.on_tool_event:
            await self._hooks.on_tool_event(self._log.last())

    def _abort_reason(self) -> str:
        """推断中止原因（turn/aborted 的 reason 字段；max_steps 路径不算 aborted 不经过此方法）。"""
        if self._cancel.is_set():
            return "user_cancel"
        if self._llm_failed:
            return "llm_error"
        return "consumer_closed"

    async def run(self, user_text: str) -> AsyncIterator[SessionEvent]:
        """执行一轮 turn：用户输入 → step 循环（steering/钩子/LLM 流/工具管线）→ 收尾事件。

        Args:
            user_text: 用户输入文本。

        Yields:
            SessionEvent（本 turn 全部事件，同时写入 EventLog 供 sink 持久化）。
        """
        # 每轮 turn 重置运行态：同一 RunSession 取消/失败后可再次 run
        self._cancel.clear()
        self._llm_failed = False

        if self._hooks and self._hooks.on_session_start:
            await self._hooks.on_session_start(self)

        yield await self._emit(EventType.TURN_START, {
            "system_prompt": self._config.system_prompt,
            "user_id": self._user_id,
            "run_id": self._run_id,
        })
        yield await self._emit(EventType.USER_MESSAGE, {"text": user_text})

        ctx = ToolContext(
            user_id=self._user_id, run_id=self._run_id,
            workspace_root=self._workspace_root,
        )
        aborted = False
        closer: SessionEvent | None = None
        try:
            for step in range(self._config.max_steps):
                if self._cancel.is_set():
                    aborted = True
                    break
                # steering 注入：drain 插话队列，下一次 LLM 调用即可看到
                while not self._steering.empty():
                    steer_text = await self._steering.get()
                    yield await self._emit(
                        EventType.USER_MESSAGE, {"text": steer_text, "steering": True},
                    )

                messages = (
                    [Message(role=Role.SYSTEM, content=self._config.system_prompt)]
                    + derive_messages(self._log.events, include_system=False)
                )
                if self._hooks and self._hooks.before_llm_call:
                    messages = await self._hooks.before_llm_call(messages)

                text_parts: list[str] = []
                tool_calls: list[ToolCallChunk] = []
                try:
                    # aclosing：消费中断/异常时确定性地关闭后端流（否则流要等 GC 才收尾）
                    async with contextlib.aclosing(self._backend.stream(
                        messages,
                        self._registry.llm_schemas(self._config.tool_names),
                    )) as stream:
                        async for ev in stream:
                            if self._cancel.is_set():
                                # 流内取消检查点：不等到流耗尽，立刻断开
                                aborted = True
                                break
                            if isinstance(ev, TextDelta):
                                yield await self._emit(
                                    EventType.LLM_DELTA, {"text": ev.text, "step": step},
                                )
                                text_parts.append(ev.text)
                            elif isinstance(ev, ToolCallChunk):
                                tool_calls.append(ev)
                            elif isinstance(ev, Usage):
                                self._last_usage = ev  # last-wins：最后一次 Usage 覆盖
                except Exception as exc:  # noqa: BLE001 LLM 错误（含 openai.APIError 的连接/限流/5xx）统一映射为事件，不穿透 run
                    yield await self._emit(EventType.ERROR, {
                        "code": "llm_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                    self._llm_failed = True

                if self._cancel.is_set():
                    aborted = True
                    break
                if self._llm_failed:
                    aborted = True
                    break

                turn_text = "".join(text_parts)
                if text_parts and not tool_calls:
                    yield await self._emit(
                        EventType.ASSISTANT_MESSAGE, {"content": turn_text},
                    )
                    break
                if not tool_calls:
                    break

                for idx, tc in enumerate(tool_calls):
                    # 组内首个携带 turn_text、其余 content=None，配合 session.py 的连续 call 聚合投影
                    call_content = (turn_text or None) if idx == 0 else None
                    yield await self._emit(EventType.TOOL_CALL, {
                        "tool_call": {
                            "id": tc.id, "name": tc.name, "arguments": tc.arguments,
                        },
                        "content": call_content,
                    })
                    await self._fire_tool_hook()
                    if tc.arguments_error is not None:
                        # 参数 JSON 解析失败：不执行工具（避免空参数误触发副作用），直接构造失败结果
                        result = ToolResult(
                            ok=False, content=tc.arguments_error,
                            error="invalid_tool_arguments",
                        )
                    else:
                        result = await self._pipeline.run(
                            tc.name, ctx, tc.arguments,
                            allowed=self._config.tool_names,
                        )
                    yield await self._emit(EventType.TOOL_RESULT, {
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "ok": result.ok,
                        "content": result.content,
                        "error": result.error,
                        "truncated": result.truncated,
                    })
                    await self._fire_tool_hook()
            else:
                # for 正常耗尽（无 break）= max_steps 用尽：发 error，aborted 保持 False，
                # finally 仍发 turn/end，保证事件流完整收尾
                yield await self._emit(EventType.ERROR, {
                    "code": "max_steps",
                    "message": f"达到步数上限 {self._config.max_steps}",
                })
        except BaseException:
            # GeneratorExit（消费者断开）/ CancelledError（任务取消）路径同样视为 aborted，
            # 收尾事件与钩子必须执行；随后 re-raise，生成器据此正常终止
            aborted = True
            raise
        finally:
            # 收尾契约：finally 内只写日志与跑钩子、绝不 yield（否则消费者断开时
            # 会抛 RuntimeError: async generator ignored GeneratorExit 且钩子永不执行）
            closer_type = EventType.TURN_ABORTED if aborted else EventType.TURN_END
            payload = (
                {"step_reached": True, "reason": self._abort_reason()} if aborted else {}
            )
            closer = await self._emit(closer_type, payload)
            if self._hooks and self._hooks.on_session_end:
                await self._hooks.on_session_end(self)
        yield closer  # 仅正常完成路径可达（异常已 re-raise，不会执行到这里）
