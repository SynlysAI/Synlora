"""catalog 扫描（专家/技能/插件三类）与插件工具加载测试。"""
from __future__ import annotations

import json

from app.catalog.loader import (
    CatalogIndex,
    ExpertPackage,
    PluginPackage,
    SkillPackage,
    catalog_roots,
    load_plugin_tools,
    scan_catalog,
)
from app.core.settings import Settings

TOOLS_SOURCE = '''
"""测试插件工具模块。"""
from synlys_harness import ToolContext, ToolResult, tool


@tool(name="demo.hello", description="示例工具",
      parameters={"type": "object", "properties": {}})
async def demo_hello(ctx: ToolContext, args: dict) -> ToolResult:
    """示例工具：回 hi。"""
    return ToolResult(ok=True, content="hi")
'''

MANIFEST = {
    "id": "demo",
    "name": "示例插件",
    "version": "1.0.0",
    "description": "用于测试的插件包",
    "tools_module": "tools.py",
    "config_schema": [
        {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
        {"key": "token", "label": "凭证", "type": "password", "secret": True},
    ],
    "skills": ["demo-skill"],
    "expert": {"name": "示例专家", "system_prompt": "你是示例专家。",
               "tool_whitelist": ["demo.hello"]},
}

EXPERT_MANIFEST = {
    "id": "asst-demo",
    "name": "示例专家",
    "avatar": "🧪",
    "description": "示例专家的描述",
    "system_prompt": "你是示例专家。",
    "tool_whitelist": ["python.run"],
}

SKILL_MD = "---\nname: demo-skill\ndescription: 示例技能\n---\n正文\n"


def _make_plugin(root, plugin_id: str = "demo", manifest: dict | None = None):
    """在 `<root>/plugins` 下造一个插件包目录。

    Args:
        root: catalog 根目录。
        plugin_id: 插件目录名与 manifest id。
        manifest: manifest 内容（None 用 MANIFEST）。

    Returns:
        插件目录 Path。
    """
    d = root / "plugins" / plugin_id
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps(manifest or MANIFEST, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOLS_SOURCE, encoding="utf-8")
    return d


def _make_expert(root, dir_name: str, manifest: dict | None = None):
    """在 `<root>/experts` 下造一个专家包目录。

    Args:
        root: catalog 根目录。
        dir_name: 专家目录名。
        manifest: expert.json 内容（None 用 EXPERT_MANIFEST）。

    Returns:
        专家目录 Path。
    """
    d = root / "experts" / dir_name
    d.mkdir(parents=True)
    (d / "expert.json").write_text(
        json.dumps(manifest or EXPERT_MANIFEST, ensure_ascii=False), encoding="utf-8")
    return d


def _make_skill(root, name: str, text: str | None = SKILL_MD):
    """在 `<root>/skills` 下造一个技能目录。

    Args:
        root: catalog 根目录。
        name: 技能名（= 目录名）。
        text: SKILL.md 内容；None 表示不写 SKILL.md。

    Returns:
        技能目录 Path。
    """
    d = root / "skills" / name
    d.mkdir(parents=True)
    if text is not None:
        (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_catalog_roots_include_repo_and_data_dir(tmp_path):
    """扫描根 = 随仓库的 catalog/ + 数据目录下的 catalog/。"""
    settings = Settings(data_dir=str(tmp_path))
    roots = catalog_roots(settings)
    assert roots[0].name == "catalog" and roots[0].parent.name == "backend"
    assert roots[1] == tmp_path / "catalog"


def test_scan_catalog_parses_three_kinds(tmp_path):
    """三类各自扫到：专家 / 技能 / 插件，各归各的映射。"""
    _make_plugin(tmp_path)
    _make_expert(tmp_path, "demo-expert")
    _make_skill(tmp_path, "demo-skill")

    index = scan_catalog([tmp_path])
    assert isinstance(index, CatalogIndex)
    assert set(index.experts) == {"asst-demo"}
    assert set(index.skills) == {"demo-skill"}
    assert set(index.plugins) == {"demo"}


def test_scan_catalog_kinds_are_isolated(tmp_path):
    """类型由目录位置决定：放错的包不会被当成另一类收录。"""
    _make_plugin(tmp_path, plugin_id="only-plugin",
                 manifest={**MANIFEST, "id": "only-plugin"})
    _make_expert(tmp_path, "only-expert")
    _make_skill(tmp_path, "only-skill")
    # 专家/技能目录里塞 plugin.json：不应被插件扫描捡走（位置决定类型）
    for stray in (tmp_path / "experts" / "only-expert", tmp_path / "skills" / "only-skill"):
        (stray / "plugin.json").write_text(
            json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")

    index = scan_catalog([tmp_path])
    assert set(index.experts) == {"asst-demo"}
    assert set(index.skills) == {"only-skill"}
    assert set(index.plugins) == {"only-plugin"}


def test_scan_catalog_empty_root_is_fine(tmp_path):
    """根目录不存在 / 无任何子目录时返回空索引（首次部署）。"""
    assert scan_catalog([tmp_path / "nope"]) == CatalogIndex({}, {}, {})
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_catalog([empty]) == CatalogIndex({}, {}, {})


def test_scan_experts_parses_package(tmp_path):
    """专家包解析出完整 ExpertPackage。"""
    d = _make_expert(tmp_path, "demo-expert")

    experts = scan_catalog([tmp_path]).experts
    pkg = experts["asst-demo"]
    assert isinstance(pkg, ExpertPackage)
    assert pkg.name == "示例专家" and pkg.avatar == "🧪"
    assert pkg.description == "示例专家的描述"
    assert pkg.system_prompt == "你是示例专家。"
    assert pkg.tool_whitelist == ["python.run"]
    assert pkg.directory == d


def test_scan_experts_optional_fields_default_empty(tmp_path):
    """avatar/description/tool_whitelist 缺省为空串/空表。"""
    _make_expert(tmp_path, "bare", manifest={
        "id": "asst-bare", "name": "裸专家", "system_prompt": "你是裸专家。"})

    pkg = scan_catalog([tmp_path]).experts["asst-bare"]
    assert pkg.avatar == "" and pkg.description == ""
    assert pkg.tool_whitelist == []


def test_scan_experts_skips_malformed(tmp_path):
    """专家包非法（JSON 坏 / 非对象 / 缺 system_prompt / id 非法 / 无 manifest）只跳过。"""
    bad = tmp_path / "experts" / "bad"
    bad.mkdir(parents=True)
    (bad / "expert.json").write_text("{ not json", encoding="utf-8")
    as_list = _make_expert(tmp_path, "as-list",
                           manifest={"id": "x", "name": "n", "system_prompt": "s"})
    (as_list / "expert.json").write_text("[1, 2, 3]", encoding="utf-8")
    _make_expert(tmp_path, "no-prompt", manifest={"id": "asst-x", "name": "缺提示词"})
    _make_expert(tmp_path, "bad-id",
                 manifest={"id": 123, "name": "n", "system_prompt": "s"})
    (tmp_path / "experts" / "not-an-expert").mkdir()  # 无 manifest 的目录

    assert scan_catalog([tmp_path]).experts == {}


def test_scan_experts_duplicate_id_last_wins(tmp_path):
    """两个根出现同名专家 id：后者覆盖前者（不抛异常）。"""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _make_expert(root_a, "dup", manifest={**EXPERT_MANIFEST, "name": "仓库版"})
    _make_expert(root_b, "dup", manifest={**EXPERT_MANIFEST, "name": "数据目录版"})

    experts = scan_catalog([root_a, root_b]).experts
    assert experts["asst-demo"].name == "数据目录版"


def test_scan_skills_collects_skill_md(tmp_path):
    """技能 = 有 SKILL.md 的目录（frontmatter 元数据此处不解析）。"""
    d = _make_skill(tmp_path, "demo-skill")

    skills = scan_catalog([tmp_path]).skills
    pkg = skills["demo-skill"]
    assert isinstance(pkg, SkillPackage)
    assert pkg.name == "demo-skill" and pkg.directory == d


def test_scan_skills_requires_skill_md(tmp_path):
    """无 SKILL.md 的目录不被收录（放错位置）。"""
    _make_skill(tmp_path, "incomplete", text=None)
    _make_skill(tmp_path, "full")

    assert set(scan_catalog([tmp_path]).skills) == {"full"}


def test_scan_plugins_parses_manifest(tmp_path):
    """扫描解析出完整 PluginPackage（含 schema/技能/专家模板）。"""
    _make_plugin(tmp_path)
    packages = scan_catalog([tmp_path]).plugins
    pkg = packages["demo"]
    assert isinstance(pkg, PluginPackage)
    assert pkg.name == "示例插件" and pkg.tools_module == "tools.py"
    assert [f["key"] for f in pkg.config_schema] == ["base_url", "token"]
    assert pkg.skills == ["demo-skill"]
    assert pkg.expert["name"] == "示例专家"
    assert pkg.skills_root is None  # 包内无 skills/ 目录


def test_skills_root_present_when_dir_exists(tmp_path):
    """包内有 skills/ 目录时 skills_root 指向它。"""
    d = _make_plugin(tmp_path)
    (d / "skills" / "demo-skill").mkdir(parents=True)
    (d / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 示例\n---\n正文\n", encoding="utf-8")
    pkg = scan_catalog([tmp_path]).plugins["demo"]
    assert pkg.skills_root == d / "skills"


def test_scan_plugins_skips_malformed(tmp_path):
    """manifest 非法（JSON 坏 / 非对象 JSON / 缺必填字段 / id 非法 / 无 manifest）只跳过，不抛异常。"""
    root = tmp_path / "plugins"
    bad = root / "bad"
    bad.mkdir(parents=True)
    (bad / "plugin.json").write_text("{ not json", encoding="utf-8")
    as_list = root / "as-list"
    as_list.mkdir()
    (as_list / "plugin.json").write_text("[1, 2, 3]", encoding="utf-8")
    as_str = root / "as-str"
    as_str.mkdir()
    (as_str / "plugin.json").write_text('"hello"', encoding="utf-8")
    bad_id = root / "bad-id"
    bad_id.mkdir()
    (bad_id / "plugin.json").write_text(json.dumps({"id": 123, "name": "n",
                                                    "version": "1", "tools_module": "tools.py"}),
                                        encoding="utf-8")
    missing = root / "missing"
    missing.mkdir()
    (missing / "plugin.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    bad_schema = root / "bad-schema"
    bad_schema.mkdir()
    (bad_schema / "plugin.json").write_text(json.dumps({
        "id": "bad-schema", "name": "n", "version": "1", "tools_module": "tools.py",
        "config_schema": [{"label": "缺少 key"}]}), encoding="utf-8")
    (root / "not-a-plugin").mkdir()  # 无 manifest 的目录

    assert scan_catalog([tmp_path]).plugins == {}


def test_scan_plugins_duplicate_id_last_wins(tmp_path):
    """两个根出现同名 id：后者覆盖前者（不抛异常）。"""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _make_plugin(root_a, plugin_id="dup",
                 manifest={**MANIFEST, "id": "dup", "name": "仓库版"})
    _make_plugin(root_b, plugin_id="dup",
                 manifest={**MANIFEST, "id": "dup", "name": "数据目录版"})

    packages = scan_catalog([root_a, root_b]).plugins
    assert packages["dup"].name == "数据目录版"


def test_load_plugin_tools_collects_decorated_functions(tmp_path):
    """加载工具模块，收集带 __tool_definition__ 的函数。"""
    _make_plugin(tmp_path)
    pkg = scan_catalog([tmp_path]).plugins["demo"]
    tools = load_plugin_tools(pkg)
    assert [t.__tool_definition__.name for t in tools] == ["demo.hello"]


def test_load_plugin_tools_missing_module_returns_empty(tmp_path):
    """工具模块缺失时返回空列表（不抛异常）。"""
    d = _make_plugin(tmp_path)
    (d / "tools.py").unlink()
    pkg = scan_catalog([tmp_path]).plugins["demo"]
    assert load_plugin_tools(pkg) == []


def test_load_plugin_tools_cleans_sys_modules_on_failure(tmp_path):
    """工具模块导入抛异常时清理 sys.modules 残留（不污染后续导入）。"""
    import sys

    d = _make_plugin(tmp_path)
    (d / "tools.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    pkg = scan_catalog([tmp_path]).plugins["demo"]

    assert load_plugin_tools(pkg) == []
    assert "synlora_plugin_demo" not in sys.modules


def test_load_plugin_tools_sanitizes_module_name(tmp_path):
    """id 含非法标识符字符时模块名被净化，仍能加载。"""
    import sys

    _make_plugin(tmp_path, plugin_id="spec-agent",
                 manifest={**MANIFEST, "id": "spec-agent"})
    pkg = scan_catalog([tmp_path]).plugins["spec-agent"]

    tools = load_plugin_tools(pkg)
    assert [t.__tool_definition__.name for t in tools] == ["demo.hello"]
    assert "synlora_plugin_spec_agent" in sys.modules
