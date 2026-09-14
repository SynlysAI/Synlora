"""插件包扫描与工具加载测试。"""
from __future__ import annotations

import json

from app.core.settings import Settings
from app.plugins.loader import (
    PluginPackage,
    load_plugin_tools,
    plugin_roots,
    scan_plugins,
)

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


def _make_package(root, plugin_id: str = "demo", manifest: dict | None = None):
    """在 root 下造一个插件包目录。

    Args:
        root: 插件根目录。
        plugin_id: 插件目录名与 manifest id。
        manifest: manifest 内容（None 用 MANIFEST）。

    Returns:
        插件目录 Path。
    """
    d = root / plugin_id
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps(manifest or MANIFEST, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOLS_SOURCE, encoding="utf-8")
    return d


def test_plugin_roots_include_repo_and_data_dir(tmp_path):
    """插件根 = 随仓库的 plugins/ + 数据目录下的 plugins/。"""
    settings = Settings(data_dir=str(tmp_path))
    roots = plugin_roots(settings)
    assert roots[0].name == "plugins" and roots[0].parent.name == "backend"
    assert roots[1] == tmp_path / "plugins"


def test_scan_plugins_parses_manifest(tmp_path):
    """扫描解析出完整 PluginPackage（含 schema/技能/专家模板）。"""
    _make_package(tmp_path)
    packages = scan_plugins([tmp_path])
    pkg = packages["demo"]
    assert isinstance(pkg, PluginPackage)
    assert pkg.name == "示例插件" and pkg.tools_module == "tools.py"
    assert [f["key"] for f in pkg.config_schema] == ["base_url", "token"]
    assert pkg.skills == ["demo-skill"]
    assert pkg.expert["name"] == "示例专家"
    assert pkg.skills_root is None  # 包内无 skills/ 目录


def test_skills_root_present_when_dir_exists(tmp_path):
    """包内有 skills/ 目录时 skills_root 指向它。"""
    d = _make_package(tmp_path)
    (d / "skills" / "demo-skill").mkdir(parents=True)
    (d / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 示例\n---\n正文\n", encoding="utf-8")
    pkg = scan_plugins([tmp_path])["demo"]
    assert pkg.skills_root == d / "skills"


def test_scan_plugins_skips_malformed(tmp_path):
    """manifest 非法（JSON 坏 / 缺必填字段 / 无 manifest）只跳过，不抛异常。"""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text("{ not json", encoding="utf-8")
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "plugin.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    (tmp_path / "not-a-plugin").mkdir()  # 无 manifest 的目录

    assert scan_plugins([tmp_path]) == {}


def test_scan_plugins_missing_root_is_fine(tmp_path):
    """根目录不存在时返回空表（首次部署无 plugins/ 目录）。"""
    assert scan_plugins([tmp_path / "nope"]) == {}


def test_load_plugin_tools_collects_decorated_functions(tmp_path):
    """加载工具模块，收集带 __tool_definition__ 的函数。"""
    _make_package(tmp_path)
    pkg = scan_plugins([tmp_path])["demo"]
    tools = load_plugin_tools(pkg)
    assert [t.__tool_definition__.name for t in tools] == ["demo.hello"]


def test_load_plugin_tools_missing_module_returns_empty(tmp_path):
    """工具模块缺失时返回空列表（不抛异常）。"""
    d = _make_package(tmp_path)
    (d / "tools.py").unlink()
    pkg = scan_plugins([tmp_path])["demo"]
    assert load_plugin_tools(pkg) == []
