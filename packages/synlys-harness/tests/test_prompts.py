"""通用系统提示词分段组合测试。"""

from synlys_harness.prompts import PromptSection, render_prompt_sections


def test_sections_are_sorted_and_empty_sections_are_ignored():
    """分段按优先级稳定排序，空白段不产生额外分隔。"""
    output = render_prompt_sections([
        PromptSection(priority=30, text="第三段"),
        PromptSection(priority=10, text="第一段"),
        PromptSection(priority=20, text="  \n"),
        PromptSection(priority=30, text="第四段"),
    ])

    assert output == "第一段\n\n第三段\n\n第四段"


def test_variables_are_replaced_without_dropping_unknown_placeholders():
    """已知变量被替换，未知占位符保留供后续装配诊断。"""
    output = render_prompt_sections(
        [PromptSection(priority=10, text="目录：{{ workspace }}；未知：{{other}}")],
        {"workspace": "/workspace"},
    )

    assert output == "目录：/workspace；未知：{{other}}"
