"""平台无关的系统提示词分段组合。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

_PLACEHOLDER = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


@dataclass(frozen=True)
class PromptSection:
    """一段可排序的系统提示词。

    Attributes:
        priority: 数值越小越靠前。
        text: 分段正文。
    """

    priority: int
    text: str


def render_prompt_sections(
    sections: Sequence[PromptSection],
    variables: Mapping[str, object] | None = None,
) -> str:
    """按优先级渲染提示词分段并替换已知变量。

    Args:
        sections: 待渲染的提示词分段。
        variables: 占位符变量；未提供的变量保持原文。

    Returns:
        以空行连接的非空分段文本。
    """
    values = variables or {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(values[key]) if key in values else match.group(0)

    rendered = [
        _PLACEHOLDER.sub(replace, section.text).strip()
        for section in sorted(sections, key=lambda item: item.priority)
    ]
    return "\n\n".join(text for text in rendered if text)
