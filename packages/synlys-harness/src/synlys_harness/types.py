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
    REASONING_DELTA = "reasoning/delta"  # 瞬态：思考流增量（仅 SSE 推送，不落盘）
    ASSISTANT_REASONING = "assistant/reasoning"  # 定稿：本轮思考全文（落盘，供回放展示）
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    ASK_USER = "ask/user"  # 工具向用户提问（落盘，前端渲染问题卡；回答经 tool/result 回流）
    FILE_SEND = "file/send"  # 工具向用户交付文件（落盘，前端渲染文件卡可下载）
    SESSION_COMPACTION = "session/compaction"  # 历史压缩标记（summary + until_seq，落盘供回放/续用）
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
    # 多模态图片附件（pi 式瞬态附件：只在下一次 LLM 调用时注入，不落事件流；
    # 元素形如 {"mime": "image/png", "base64": "..."}）
    images: list[dict[str, str]] = Field(default_factory=list)


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
    # 上下文自动压缩（参考 pi compaction）：上次 LLM 调用的 prompt_tokens 超过
    # 阈值时，把早期历史 LLM 摘要为一条压缩消息、保留近期原文；0 = 关闭
    compaction_threshold_tokens: int = 24000
    # 压缩时保留的近期历史字符预算（约一半阈值对应量，防止压缩后立刻再触发）
    compaction_keep_chars: int = 24000
