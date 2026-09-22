"""Agent loop：turn/step 双层循环（RunSession）。"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, AsyncIterator

from .compaction import Compactor
from .events import EventLog
from .models.backend import LLMBackend, ReasoningDelta, TextDelta, ToolCallChunk, Usage
from .session import derive_messages
from .tools.pipeline import ToolPipeline
from .tools.registry import ToolRegistry
from .types import (
    AgentConfig,
    EventType,
    ExtensionHooks,
    Message,
    ResearchContextScope,
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
        context_extra: dict[str, Any] | None = None,
        research_context: ResearchContextScope | None = None,
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
            context_extra: 注入工具上下文的宿主配置，如 http_allowed_hosts。
            research_context: Plane 签发的只读科研范围；None 表示非科研会话。
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
        self._context_extra = context_extra
        self._research_context = research_context
        self._cancel = asyncio.Event()
        self._steering: asyncio.Queue[str] = asyncio.Queue()
        self._last_usage: Usage | None = None
        self._llm_failed = False
        # 本轮待注入的图片附件（pi 式瞬态：file.read_image 结果，下一次 LLM 调用
        # 即消费并清空，不落事件流）
        self._pending_images: list[dict[str, str]] = []
        # 上下文压缩器（threshold=0 时 apply 直接原样返回，零开销）
        self._compactor = Compactor(
            backend, event_log,
            threshold_tokens=config.compaction_threshold_tokens,
            keep_chars=config.compaction_keep_chars,
        )

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

    def take_queued_turn(self) -> str | None:
        """turn 结束后兜底：取走队列中未被消费的插话（转为下一轮输入）。

        模型输出最终回答时已无下一个 step，期间入队的插话不会被 step 边界
        drain 消费——由宿主在 run() 耗尽后调用本方法决定去向：turn 正常
        结束且队列非空返回拼接文本（多条插话以换行合并），宿主据此自动续跑
        下一轮；取消/LLM 失败路径的残留插话直接丢弃（返回 None）。

        Returns:
            下一轮用户输入文本；无需续跑时为 None。
        """
        if self._cancel.is_set() or self._llm_failed:
            while not self._steering.empty():
                self._steering.get_nowait()
            return None
        pending: list[str] = []
        while not self._steering.empty():
            pending.append(self._steering.get_nowait())
        return "\n".join(pending) if pending else None

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

    async def run(
        self,
        user_text: str,
        attachments: list[dict] | None = None,
        input_metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[SessionEvent]:
        """执行一轮 turn：用户输入 → step 循环（steering/钩子/LLM 流/工具管线）→ 收尾事件。

        Args:
            user_text: 用户输入文本。
            attachments: 用户随消息发送的附件元数据（[{file_id, filename, path}]，
                path 为工作区相对路径）；None/空 = 无附件，事件 payload 不带该字段。
            input_metadata: 宿主为当前输入附加的元数据；不得覆盖 text/attachments，
                且必须可 JSON 序列化。

        Yields:
            SessionEvent（本 turn 全部事件，同时写入 EventLog 供 sink 持久化）。
        """
        metadata = dict(input_metadata or {})
        protected = {"text", "attachments"} & metadata.keys()
        if protected:
            raise ValueError(f"输入元数据不得覆盖保留字段: {', '.join(sorted(protected))}")
        try:
            json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("输入元数据必须可 JSON 序列化") from exc

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
        user_payload: dict = {"text": user_text, **metadata}
        if attachments:
            user_payload["attachments"] = attachments
        yield await self._emit(EventType.USER_MESSAGE, user_payload)

        ctx = ToolContext(
            user_id=self._user_id, run_id=self._run_id,
            workspace_root=self._workspace_root,
            research_context=self._research_context,
            extra=dict(self._context_extra or {}),  # 副本防宿主 dict 被共享修改
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

                # 历史按需压缩（超阈值时早期历史摘要为一条消息，见 compaction.py）
                history = await self._compactor.apply(
                    derive_messages(self._log.events, include_system=False),
                    self._last_usage.prompt_tokens if self._last_usage else None,
                )
                # 图片附件（瞬态）：上一步工具收集的图片作为末尾 user 消息注入，
                # 本轮调用后即清空——回放/后续轮次不含图片本体
                if self._pending_images:
                    history = history + [Message(
                        role=Role.USER, content="（工具返回的图片，供本次视觉分析）",
                        images=self._pending_images,
                    )]
                    self._pending_images = []
                messages = (
                    [Message(role=Role.SYSTEM, content=self._config.system_prompt)]
                    + history
                )
                if self._hooks and self._hooks.before_llm_call:
                    messages = await self._hooks.before_llm_call(messages)

                text_parts: list[str] = []
                reasoning_parts: list[str] = []
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
                            elif isinstance(ev, ReasoningDelta):
                                # 瞬态：仍经 EventLog 分配 seq（SSE 照推），宿主落盘层过滤
                                yield await self._emit(
                                    EventType.REASONING_DELTA, {"text": ev.text},
                                )
                                reasoning_parts.append(ev.text)
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

                # 思考定稿：本 step 思考全文在 assistant/message 与 tool 循环
                # 之前落账（每 step 一条；多 step turn 会有多条，回放按序展示）
                if reasoning_parts:
                    yield await self._emit(
                        EventType.ASSISTANT_REASONING, {"content": "".join(reasoning_parts)},
                    )

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
                        # 把本次调用的 id 递给工具（ask_user 的 ask/user 事件要带
                        # tool_call_id 供前端配对回答）
                        ctx.extra["tool_call_id"] = tc.id
                        result = await self._pipeline.run(
                            tc.name, ctx, tc.arguments,
                            allowed=self._config.tool_names,
                        )
                        # 图片附件上浮（瞬态注入，见 messages 组装处）
                        self._pending_images.extend(result.data.get("images") or [])
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
            # turn/end 透传本轮 token 用量（多次 step 为 last-wins 累计视角）
            if not aborted and self._last_usage is not None:
                payload["usage"] = {
                    "prompt_tokens": self._last_usage.prompt_tokens,
                    "completion_tokens": self._last_usage.completion_tokens,
                }
            closer = await self._emit(closer_type, payload)
            if self._hooks and self._hooks.on_session_end:
                await self._hooks.on_session_end(self)
        yield closer  # 仅正常完成路径可达（异常已 re-raise，不会执行到这里）
