"""Synlora 产品系统提示词装配。"""
from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

from synlys_harness import PromptSection, render_prompt_sections

PROMPT_ROOT = Path(__file__).resolve().parents[2] / "catalog" / "prompts"

IDENTITY_PRIORITY = 10
TASK_PRIORITY = 21
SKILLS_PRIORITY = 40
OUTPUT_PRIORITY = 50
ENV_PRIORITY = 60
WORKSPACE_PRIORITY = 70
PERSONA_PRIORITY = 100

SANDBOX_NOTES = {
    "docker": "- 代码执行：临时 Docker 容器（**无网络**、非 root、CPU/内存/进程数受限），跑完即销毁——装不了新依赖包，也访问不了外网；需要联网或额外依赖时改用平台已有工具，或先向用户说明。",
    "local-weak": "- 代码执行：本机子进程（请求容器但不可用的降级模式，无强隔离与资源限额；有网络，但不要当默认手段）。",
    "local": "- 代码执行：本机子进程（`-I` 隔离 + 环境白名单 + 超时/输出截断；是事故围栏，不是安全边界）。",
    "unavailable": "- 代码执行：当前不可用（沙箱未就绪）——不要调用 `python.run`，改用文件工具或直接向用户说明能力受限。",
}


def _read_prompt(name: str) -> str:
    """读取随宿主部署的产品提示词文件。

    Args:
        name: 不含扩展名的提示词文件名。

    Returns:
        Markdown 提示词正文。
    """
    return (PROMPT_ROOT / f"{name}.md").read_text(encoding="utf-8")


def _render_skill_index(skills: list[tuple[str, str]]) -> str:
    """渲染只含名称和描述的技能索引。"""
    if not skills:
        return ""
    lines = [
        "# 技能",
        "",
        "选择与任务最相关的技能，使用技能前，调用 `skill.read` 获取该技能的完整 `SKILL.md`。",
        "",
        "当前可用技能：",
        "",
        *[f"{index}. `{name}`：{description}"
          for index, (name, description) in enumerate(skills, 1)],
    ]
    return "\n".join(lines)


def _render_environment(
    *,
    sandbox: str,
    shell_available: bool,
    today: str | None,
) -> str:
    """渲染日期、沙箱和 Shell 能力说明。"""
    lines = [
        "# 执行环境",
        "",
        f"- 当前日期：{today or date.today().isoformat()}（用户说“今天/最近”时以此为准）。",
    ]
    note = SANDBOX_NOTES.get(sandbox)
    if note:
        if sandbox != "docker":
            lines.append(f"- 本机平台：{platform.system()}。")
        lines.append(note)
    if shell_available:
        lines.append("- 命令执行：可使用 `shell.run` 在临时 Docker 容器中运行 Bash。")
    return "\n".join(lines)


def build_system_prompt(
    *,
    persona: str,
    workspace: Path | None,
    skills: list[tuple[str, str]],
    sandbox: str = "",
    shell_available: bool = False,
    today: str | None = None,
) -> str:
    """构造产品系统提示词。

    Args:
        persona: 专家人设，追加在平台提示词之后。
        workspace: 当前工作区；None 时省略工作区段。
        skills: 当前可见技能的名称和描述。
        sandbox: 执行器能力标记。
        shell_available: 本轮是否可调用 shell.run。
        today: 测试使用的日期覆盖值。

    Returns:
        完整系统提示词。
    """
    sections = [
        PromptSection(IDENTITY_PRIORITY, _read_prompt("identity")),
        PromptSection(TASK_PRIORITY, _read_prompt("workflow")),
        PromptSection(SKILLS_PRIORITY, _render_skill_index(skills)),
        PromptSection(OUTPUT_PRIORITY, _read_prompt("output")),
        PromptSection(
            ENV_PRIORITY,
            _render_environment(
                sandbox=sandbox,
                shell_available=shell_available,
                today=today,
            ),
        ),
    ]
    variables: dict[str, object] = {}
    if workspace is not None:
        sections.append(PromptSection(WORKSPACE_PRIORITY, _read_prompt("workspace")))
        variables["workspace"] = workspace.as_posix()
    if persona.strip():
        sections.append(PromptSection(PERSONA_PRIORITY, persona))
    return render_prompt_sections(sections, variables)
