"""SynlysAgent 核心 Agent 运行时。"""
from .agent import RunSession
from .compaction import Compactor
from .events import EventLog
from .models.backend import (
    LLMBackend, ModelProviderConfig, OpenAICompatibleBackend, ReasoningDelta,
    TextDelta, ToolCallChunk, Usage,
)
from .prompts import build_system_prompt
from .session import derive_messages
from .tools.builtin import register_builtin_tools
from .tools.pipeline import ToolPipeline
from .tools.registry import ToolRegistry, tool
from .tools.sandbox import (
    CodeExecutor,
    DEFAULT_LOCAL_EXECUTOR,
    DockerCodeExecutor,
    FailingExecutor,
    LocalCodeExecutor,
    resolve_executor,
    run_python,
)
from .types import (
    AgentConfig, EventType, ExtensionHooks, Message, Permission, Role,
    SessionEvent, ToolCall, ToolContext, ToolDefinition, ToolResult,
    ResearchContextScope,
)

__all__ = [
    "RunSession", "EventLog", "Compactor", "derive_messages", "build_system_prompt",
    "ToolRegistry", "tool", "ToolPipeline", "register_builtin_tools",
    "CodeExecutor", "LocalCodeExecutor", "DockerCodeExecutor", "FailingExecutor",
    "DEFAULT_LOCAL_EXECUTOR", "resolve_executor", "run_python",
    "LLMBackend", "OpenAICompatibleBackend", "ModelProviderConfig",
    "TextDelta", "ReasoningDelta", "ToolCallChunk", "Usage",
    "AgentConfig", "EventType", "ExtensionHooks", "Message", "Permission",
    "Role", "SessionEvent", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult",
    "ResearchContextScope",
]
