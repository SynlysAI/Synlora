"""内置目录条目枚举测试。"""
from __future__ import annotations

from app.catalog.items import CatalogItem, CatalogService
from app.core.settings import Settings
from app.plugins.loader import plugin_roots, scan_plugins
from app.services.skill_service import SkillService


def _service(settings: Settings, skill_service: SkillService) -> CatalogService:
    """组装 CatalogService。

    Args:
        settings: 应用配置。
        skill_service: 技能服务。

    Returns:
        CatalogService。
    """
    return CatalogService(
        settings=settings,
        skill_service=skill_service,
        packages=scan_plugins(plugin_roots(settings)),
    )


def test_experts_include_seeded_builtins(tmp_path):
    """内置专家 = 代码种子助手（不含插件播种的 asst-plugin-*）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(settings, SkillService(tmp_path))
    experts = {i.id: i for i in svc.list_items("expert")}
    assert "asst-research" in experts and "asst-data" in experts
    assert all(not i.id.startswith("asst-plugin-") for i in svc.list_items("expert"))
    assert experts["asst-research"].name == "科研助手"


def test_skills_include_repo_builtins(tmp_path):
    """内置技能 = 随包播种到技能目录的内置技能。"""
    settings = Settings(data_dir=str(tmp_path))
    skill_service = SkillService(tmp_path)
    skill_service.seed_builtins()
    svc = _service(settings, skill_service)
    names = {i.id for i in svc.list_items("skill")}
    assert {"data-analysis", "pdf-extraction", "office-doc"} <= names


def test_plugins_include_repo_packages(tmp_path):
    """内置插件 = 扫描到的插件包（含随仓库的 spec_agent）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(settings, SkillService(tmp_path))
    plugins = {i.id: i for i in svc.list_items("plugin")}
    assert "spec_agent" in plugins
    assert plugins["spec_agent"].name == "Spec_Agent 谱图解析"


def test_all_items_are_builtin_source(tmp_path):
    """目录条目的 source 恒为 builtin（首期只有内置项）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(settings, SkillService(tmp_path))
    for kind in ("expert", "skill", "plugin"):
        for item in svc.list_items(kind):
            assert isinstance(item, CatalogItem)
            assert item.kind == kind and item.source == "builtin"


def test_unknown_kind_returns_empty(tmp_path):
    """未知 kind 返回空列表（不抛异常）。"""
    settings = Settings(data_dir=str(tmp_path))
    svc = _service(settings, SkillService(tmp_path))
    assert svc.list_items("nope") == []


def test_all_items_aggregates_three_kinds(tmp_path):
    """all_items 汇总三类（每类都非空）。"""
    settings = Settings(data_dir=str(tmp_path))
    skill_service = SkillService(tmp_path)
    skill_service.seed_builtins()
    svc = _service(settings, skill_service)
    kinds = {i.kind for i in svc.all_items()}
    assert kinds == {"expert", "skill", "plugin"}
