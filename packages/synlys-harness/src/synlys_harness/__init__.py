"""SynlysAgent 核心 Agent 运行时。"""
from .agent import RunSession
from .compaction import Compactor
from .events import EventLog
from .jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    can_transition,
    is_terminal,
)
from .models.backend import (
    LLMBackend, ModelProviderConfig, OpenAICompatibleBackend, ReasoningDelta,
    TextDelta, ToolCallChunk, Usage,
)
from .prompts import PromptSection, render_prompt_sections
from .session import derive_messages
from .tools.builtin import register_builtin_tools
from .tools.execution import ExecutionRequest, ReadOnlyResource, validate_execution_request
from .tools.execution_tools import shell_run
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
    AgentConfig, ApprovalDecision, EventType, ExtensionHooks, Message, Permission, Role,
    SessionEvent, ToolCall, ToolContext, ToolDefinition, ToolResult,
)

__all__ = [
    "RunSession", "EventLog", "Compactor", "derive_messages",
    "PromptSection", "render_prompt_sections",
    "JobStatus", "ACTIVE_STATUSES", "TERMINAL_STATUSES", "can_transition",
    "is_terminal",
    "ToolRegistry", "tool", "ToolPipeline", "register_builtin_tools",
    "ExecutionRequest", "ReadOnlyResource", "validate_execution_request", "shell_run",
    "CodeExecutor", "LocalCodeExecutor", "DockerCodeExecutor", "FailingExecutor",
    "DEFAULT_LOCAL_EXECUTOR", "resolve_executor", "run_python",
    "LLMBackend", "OpenAICompatibleBackend", "ModelProviderConfig",
    "TextDelta", "ReasoningDelta", "ToolCallChunk", "Usage",
    "AgentConfig", "ApprovalDecision", "EventType", "ExtensionHooks", "Message", "Permission",
    "Role", "SessionEvent", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult",
]
