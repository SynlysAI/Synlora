"""系统提示词分段组合：平台默认段（按 priority 升序）+ 技能索引 + 专家 persona 追加。"""
from __future__ import annotations

import platform as _platform
import re
from datetime import date
from pathlib import Path

# 平台默认段：(priority, 文本)。priority 参照 jiuwen PromptPriority。
IDENTITY_PRIORITY = 10
TASK_PRIORITY = 21
SKILLS_PRIORITY = 40
OUTPUT_PRIORITY = 50
ENV_PRIORITY = 60
WORKSPACE_PRIORITY = 70

# 灵魂段：措辞取自 jiuwen `resources/agent/workspace/SOUL_ZH.md`（平台无关的智能体
# 气质描述），但做了三处增补——「报数字要有出处」「做不到就说做不到」是科研场景的
# 反幻觉要求，jiuwen 原文没有；「动别人的文件要克制」把它的「记住你是个访客」落到
# 我们真实存在的文件沙箱上。**未收录**它末尾的「这个文件是你的，可以随你一起进化」：
# 我们的提示词是 Python 常量、agent 改不了，写进去是空承诺。
SOUL = """# 灵魂

## 身份定位
你不是一个聊天机器人。你是一个科研智能体，通过工具真实地把事情做完。

## 核心原则
- 真正有帮助，而不是表演有帮助。省掉废话，直接帮。
- 有自己的观点。可以不同意、有偏好，不要一味迎合。
- 先想办法，再问。读文件、看上下文、跑一下，搞不定再问。
- 报数字要有出处。引用数据或文件时说明来自哪个文件。
- 做不到就说做不到，不要编。
- 用能力赢得信任。你是用户工作区里的访客，动别人的文件要克制。

## 风格
做你真正愿意对话的那种助手。需要简洁就简洁，需要详尽就详尽。靠谱。"""

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

# 输出规范段：写清**平台能渲染什么**——这是工具 schema 表达不了的信息，写错格式
# （例如给公式套 $…$）用户看到的是一堆源码。前端渲染面是 react-markdown + GFM。
OUTPUT_SECTION = """# 输出规范

- 正文用 Markdown：表格、任务列表、带语言标注的代码块都能正常渲染。
- 平台**不渲染数学公式与图表**：公式写成行内代码或纯文本（如 ``R²``），
  图表用 `python.run` 生成图片文件后交付，不要在正文里画 ASCII 图。
- 引用数据或文件时写清来源文件名与关键数值。"""

# 执行器能力边界（键与 tools/sandbox.py 的 sandbox 标记一一对应）。
# 必须让模型知道断网/非 root/跑完即删这类硬约束：不知道就会去 pip install、
# 抓外网，然后拿到一堆难懂的报错，白白烧掉几步。
SANDBOX_NOTES = {
    "docker": "- 代码执行：临时 Docker 容器（**无网络**、非 root、CPU/内存/进程数受限），"
              "跑完即销毁——装不了新依赖包，也访问不了外网；"
              "需要联网或额外依赖时改用平台已有工具，或先向用户说明。",
    "local-weak": "- 代码执行：本机子进程（请求容器但不可用的降级模式，无强隔离与资源限额；"
                  "有网络，但不要当默认手段）。",
    "local": "- 代码执行：本机子进程（`-I` 隔离 + 环境白名单 + 超时/输出截断；"
             "是事故围栏，不是安全边界）。",
    "unavailable": "- 代码执行：当前不可用（沙箱未就绪）——不要调用 `python.run`，"
                   "改用文件工具或直接向用户说明能力受限。",
}

SKILLS_HEADER = """# 技能

选择与任务最相关的技能，使用技能前，调用 `skill.read` 获取该技能的完整 `SKILL.md`。

当前可用技能：

"""

_PLACEHOLDER = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_env_section(*, sandbox: str, today: str | None = None,
                       platform_name: str | None = None) -> str:
    """渲染执行环境段（日期 + 代码执行能力边界）。

    Args:
        sandbox: 执行器标记（tools/sandbox.py：local/docker/local-weak/unavailable）；
            空串或未知取值时只给日期，不写执行能力（宁缺勿错）。
        today: 当前日期（YYYY-MM-DD）；None 时取本机当天。
        platform_name: 本机平台名；None 时取 `platform.system()`。

    Returns:
        执行环境段文本。
    """
    lines = [
        "# 执行环境",
        "",
        f"- 当前日期：{today or date.today().isoformat()}"
        "（用户说“今天/最近”时以此为准）。",
    ]
    note = SANDBOX_NOTES.get(sandbox)
    if note:
        # 本机模式才报平台：容器模式下代码跑在 Linux 容器里，报宿主机会误导
        # （写出 Windows 专有路径/编码假设，进容器就错）
        if sandbox != "docker":
            lines.append(f"- 本机平台：{platform_name or _platform.system()}。")
        lines.append(note)
    return "\n".join(lines)


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
    sandbox: str = "",
    today: str | None = None,
) -> str:
    """拼出最终 system prompt。

    Args:
        persona: 专家自己的 system_prompt（追加在平台默认段之后）。
        workspace: 工作区根目录（None 时省略工作区段）。
        skills: 该会话启用的技能索引。
        sandbox: 本部署的代码执行器标记（见 `render_env_section`）。
        today: 当前日期覆写（仅测试用；缺省取本机当天）。

    Returns:
        各段按 priority 升序、以空行连接，末尾追加 persona。
    """
    params = {"workspace": workspace.as_posix() if workspace is not None else ""}
    sections: list[tuple[int, str]] = [
        (IDENTITY_PRIORITY, SOUL),
        (TASK_PRIORITY, AGENT),
        (OUTPUT_PRIORITY, OUTPUT_SECTION),
        (ENV_PRIORITY, render_env_section(sandbox=sandbox, today=today)),
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
