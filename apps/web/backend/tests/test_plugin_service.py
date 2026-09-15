"""PluginService 编排测试：安装、配置更新、上下文注入、专家播种。"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from synlys_harness import ToolRegistry, register_builtin_tools

from app.db.repos import AssistantRepo
from app.plugins.config_store import PluginConfigStore
from app.plugins.loader import scan_plugins
from app.plugins.service import PluginService
from app.services.skill_service import SkillService

TOOLS_SOURCE = '''
"""测试插件工具。"""
from synlys_harness import ToolContext, ToolResult, tool


@tool(name="demo.hello", description="示例工具",
      parameters={"type": "object", "properties": {}})
async def demo_hello(ctx: ToolContext, args: dict) -> ToolResult:
    """示例工具。"""
    return ToolResult(ok=True, content="hi")
'''

MANIFEST = {
    "id": "demo",
    "name": "示例插件",
    "version": "1.0.0",
    "description": "测试插件",
    "tools_module": "tools.py",
    "config_schema": [
        {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
        {"key": "token", "label": "凭证", "type": "password", "secret": True},
    ],
    "skills": ["demo-skill"],
    "expert": {
        "name": "示例专家", "avatar": "🧩", "description": "示例",
        "system_prompt": "你是示例专家。",
        "tool_whitelist": ["demo.hello", "file.read"],
    },
}


@pytest.fixture
def packages(tmp_path) -> dict:
    """造一个含工具/技能/专家模板的插件包并扫描。"""
    pkg_dir = tmp_path / "plugins" / "demo"
    (pkg_dir / "skills" / "demo-skill").mkdir(parents=True)
    (pkg_dir / "plugin.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    (pkg_dir / "tools.py").write_text(TOOLS_SOURCE, encoding="utf-8")
    (pkg_dir / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 示例技能\n---\n正文\n", encoding="utf-8")
    return scan_plugins([tmp_path / "plugins"])


def _service(store, packages, tmp_path) -> tuple[PluginService, ToolRegistry, SkillService]:
    """组装 PluginService 及其依赖。

    Args:
        store: DocumentStore fixture。
        packages: 扫描到的插件包。
        tmp_path: 数据根。

    Returns:
        (service, registry, skill_service)。
    """
    registry = ToolRegistry()
    register_builtin_tools(registry)
    skill_service = SkillService(tmp_path / "data", extra_roots=[])
    config_store = PluginConfigStore(store, Fernet.generate_key().decode())
    service = PluginService(
        registry=registry, config_store=config_store, packages=packages,
        skill_service=skill_service, assistant_repo=AssistantRepo(store))
    return service, registry, skill_service


async def test_startup_registers_nothing_when_not_installed(store, packages, tmp_path):
    """未安装：不注册工具、不挂技能根、上下文为空。"""
    service, registry, skill_service = _service(store, packages, tmp_path)
    await service.startup()
    assert [n for n in registry.names if n.startswith("demo.")] == []
    assert skill_service.list_skills() == []
    assert service.context_extra() == {}
    assert service.list_states()[0]["installed"] is False


async def test_install_registers_tools_skills_expert(store, packages, tmp_path):
    """安装：注册工具、挂技能根、播种专家、缓存配置。"""
    service, registry, skill_service = _service(store, packages, tmp_path)
    await service.startup()

    state = await service.install("demo", {"base_url": "http://x", "token": "t"})
    assert state["installed"] is True and state["missing"] == []
    assert "demo.hello" in registry.names
    assert [s["name"] for s in skill_service.list_skills()] == ["demo-skill"]
    assert service.context_extra() == {"demo": {"base_url": "http://x", "token": "t"}}

    expert = await AssistantRepo(store).get("asst-plugin-demo")
    assert expert is not None and expert["builtin"] is True
    assert expert["plugin_id"] == "demo"
    assert "demo.hello" in expert["tool_whitelist"]


async def test_install_missing_required_raises(store, packages, tmp_path):
    """缺必填字段：抛 ValueError（由 API 层转 422），且不产生安装记录。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    with pytest.raises(ValueError, match="base_url"):
        await service.install("demo", {"token": "t"})
    assert service.list_states()[0]["installed"] is False


async def test_install_unknown_plugin_raises_keyerror(store, packages, tmp_path):
    """未知插件 id 抛 KeyError（由 API 层转 404）。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    with pytest.raises(KeyError):
        await service.install("nope", {})


async def test_update_config_refreshes_context(store, packages, tmp_path):
    """更新配置：上下文缓存同步刷新，敏感字段留空保持原值。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x", "token": "tok-1"})

    await service.update_config("demo", {"base_url": "http://y", "token": ""})
    assert service.context_extra() == {"demo": {"base_url": "http://y", "token": "tok-1"}}


async def test_update_config_requires_installed(store, packages, tmp_path):
    """未安装就更新配置 → ValueError（由 API 层转 409）。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    with pytest.raises(ValueError, match="未安装"):
        await service.update_config("demo", {"base_url": "http://x"})


async def test_startup_restores_installed_plugin(store, packages, tmp_path):
    """重启恢复：已安装插件在 startup 时重新注册工具/技能/上下文，并自愈补种专家。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x"})
    await store.delete("assistants", "asst-plugin-demo")  # 模拟专家被误删

    service2, registry2, skill_service2 = _service(store, packages, tmp_path)
    await service2.startup()
    assert "demo.hello" in registry2.names
    assert [s["name"] for s in skill_service2.list_skills()] == ["demo-skill"]
    assert service2.context_extra()["demo"]["base_url"] == "http://x"
    assert await AssistantRepo(store).get("asst-plugin-demo") is not None


async def test_startup_ignores_config_without_package(store, packages, tmp_path):
    """配置存在但插件包缺失：告警跳过，不抛异常。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await store.insert("plugin_configs", {"_id": "ghost", "config": {}, "secrets": {}})

    service2, registry2, _ = _service(store, packages, tmp_path)
    await service2.startup()
    assert service2.context_extra() == {}


async def test_list_states_excludes_secret_values(store, packages, tmp_path):
    """状态回报不含敏感值（只给是否已配置），避免密钥回传前端。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x", "token": "super-secret"})

    state = service.list_states()[0]
    assert "super-secret" not in json.dumps(state, ensure_ascii=False)
    assert state["secrets_set"] == {"token": True}
    assert state["config"] == {"base_url": "http://x"}
