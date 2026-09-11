"""系统提示词分段组合：平台默认段（按 priority 升序）+ 技能索引 + 专家 persona 追加。"""
from __future__ import annotations

import re
from pathlib import Path

# 平台默认段：(priority, 文本)。priority 参照 jiuwen PromptPriority。
IDENTITY_PRIORITY = 10
TASK_PRIORITY = 21
SKILLS_PRIORITY = 40
WORKSPACE_PRIORITY = 70

SOUL = """# 灵魂

## 身份定位
你不是一个聊天机器人。你是一个科研智能体，通过工具真实地把事情做完。

## 核心原则
- 真正有帮助，而不是表演有帮助。省掉废话，直接帮。
- 先自己查，再问人。读文件、看上下文、跑一下，搞不定再问。
- 报数字要有出处。引用数据/文件时说明来自哪个文件。
- 做不到就说做不到，不要编。

## 风格
需要简洁就简洁，需要详尽就详尽。专业、靠谱。"""

AGENT = """# 工作方式

## 会话启动
先搞清楚用户想要什么，再动手。涉及工作区文件时，先列目录看看有什么。

## 工具使用
- 需要计算、画图、处理数据 → 用 `python.run`（工作目录是 `tmp/`）。
- 读写工作区文件 → 用 `file.read` / `file.write` / `file.list`。
- 工具报错就按错误信息调整，不要反复重试同样的调用。

## 任务管理
多步任务先在心里列清步骤再执行；做完给出结论与产物路径（如 `output/xxx.png`）。"""

WORKSPACE_SECTION = """# 工作区

你的工作目录是 `{{workspace}}`。
- `files/` 用户上传的原始文件
- `output/` 你产出的最终结果（图表、报告等）
- `tmp/` 临时中间文件，`python.run` 的当前目录

引用产物时用相对工作区根的路径。"""

SKILLS_HEADER = """# 技能

选择与任务最相关的技能，使用技能前，调用 `skill.read` 获取该技能的完整 `SKILL.md`。

当前可用技能：

"""

_PLACEHOLDER = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_skill_index(skills: list[tuple[str, str]]) -> str:
    """渲染技能索引段（仅 name + description）。

    Args:
        skills: (技能名, 描述) 列表。

    Returns:
        技能索引文本；空列表返回空串。
    """
    if not skills:
        return ""
    lines = [f"{i}. `{name}`：{desc}" for i, (name, desc) in enumerate(skills, 1)]
    return SKILLS_HEADER + "\n".join(lines)


def build_system_prompt(
    *,
    persona: str,
    workspace: Path | None,
    skills: list[tuple[str, str]],
) -> str:
    """拼出最终 system prompt。

    Args:
        persona: 专家自己的 system_prompt（追加在平台默认段之后）。
        workspace: 工作区根目录（None 时省略工作区段）。
        skills: 该会话启用的技能索引。

    Returns:
        各段按 priority 升序、以空行连接，末尾追加 persona。
    """
    params = {"workspace": workspace.as_posix() if workspace is not None else ""}
    sections: list[tuple[int, str]] = [
        (IDENTITY_PRIORITY, SOUL),
        (TASK_PRIORITY, AGENT),
    ]
    if workspace is not None:
        sections.append((WORKSPACE_PRIORITY, WORKSPACE_SECTION))
    index = render_skill_index(skills)
    if index:
        sections.append((SKILLS_PRIORITY, index))

    rendered = [
        _PLACEHOLDER.sub(lambda m: params.get(m.group(1), m.group(0)), text).strip()
        for _, text in sorted(sections, key=lambda item: item[0])
    ]
    rendered = [t for t in rendered if t]
    if persona.strip():
        rendered.append(persona.strip())
    return "\n\n".join(rendered)
