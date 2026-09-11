"""系统提示词分段组合单测。"""
from synlys_harness.prompts import build_system_prompt, render_skill_index


def test_persona_appended_after_platform_sections():
    """persona 追加在平台默认段之后。"""
    out = build_system_prompt(persona="你是高分子专家。", workspace=None, skills=[])
    assert out.index("核心原则") < out.index("你是高分子专家。")


def test_workspace_placeholder_substituted(tmp_path):
    """工作区段的 {{workspace}} 占位符被实际路径替换。"""
    out = build_system_prompt(persona="", workspace=tmp_path, skills=[])
    # 路径统一正斜杠：与 file.list 等工具的 .as_posix() 口径一致
    assert tmp_path.as_posix() in out and "{{workspace}}" not in out
    assert "\\" not in out


def test_skill_index_rendered_only_when_present():
    """有技能时渲染技能段与 skill.read 指引。"""
    out = build_system_prompt(persona="", workspace=None, skills=[("pdf-extraction", "抽取 PDF 文本")])
    assert "`pdf-extraction`：抽取 PDF 文本" in out
    assert "skill.read" in out
    assert build_system_prompt(persona="", workspace=None, skills=[]).count("# 技能") == 0


def test_blank_persona_adds_no_trailing_segment():
    """persona 为空串或纯空白时，末尾不产生多余空段。"""
    empty = build_system_prompt(persona="", workspace=None, skills=[])
    blank = build_system_prompt(persona="  \n\t ", workspace=None, skills=[])
    assert empty == blank
    assert empty == empty.strip()
    assert not empty.endswith("\n")
    assert "\n\n\n" not in empty


def test_no_workspace_section_when_none():
    """workspace=None 时既无工作区段，也不残留占位符。"""
    out = build_system_prompt(persona="", workspace=None, skills=[])
    assert "# 工作区" not in out
    assert "{{workspace}}" not in out


def test_skill_section_precedes_workspace_section(tmp_path):
    """技能(40) 必须排在工作区(70) 之前，不能依赖 append 次序。"""
    out = build_system_prompt(
        persona="", workspace=tmp_path, skills=[("a-skill", "用途")])
    assert out.index("# 技能") < out.index("# 工作区")


def test_empty_skill_index_returns_empty_string():
    """技能索引为空列表时不渲染技能段，直接调用 render_skill_index 返回空串。"""
    out = build_system_prompt(persona="", workspace=None, skills=[])
    assert "# 技能" not in out
    assert render_skill_index([]) == ""
