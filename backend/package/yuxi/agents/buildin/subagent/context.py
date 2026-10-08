from dataclasses import dataclass, field

from yuxi.agents.context import BaseContext, ResourceSelection


@dataclass(kw_only=True)
class SubAgentContext(BaseContext):
    tools: ResourceSelection = field(
        default_factory=list,
        metadata={
            "name": "工具",
            "description": "按角色选择内置工具，默认不选择。",
            "type": "list",
            "kind": "tools",
        },
    )
    skills: ResourceSelection = field(
        default_factory=list,
        metadata={
            "name": "Skills",
            "description": "按角色选择共享和内置 Skill，默认不选择；个人 Skill 始终可用。",
            "type": "list",
            "kind": "skills",
        },
    )
    parent_thread_id: str | None = field(
        default=None,
        metadata={"name": "父线程ID", "configurable": False, "hide": True},
    )
    is_subagent_runtime: bool = field(
        default=False,
        metadata={"name": "子智能体运行态", "configurable": False, "hide": True},
    )
