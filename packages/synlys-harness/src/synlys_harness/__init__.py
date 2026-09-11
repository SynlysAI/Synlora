"""SynlysAgent 核心 Agent 运行时。"""
from .agent import RunSession
from .events import EventLog
from .models.backend import (
    LLMBackend, ModelProviderConfig, OpenAICompatibleBackend, ReasoningDelta,
    TextDelta, ToolCallChunk, Usage,
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
    "TextDelta", "ReasoningDelta", "ToolCallChunk", "Usage",
    "AgentConfig", "EventType", "ExtensionHooks", "Message", "Permission",
    "Role", "SessionEvent", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult",
]
