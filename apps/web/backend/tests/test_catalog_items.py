"""内置目录条目枚举测试。

三类条目一律来自 `apps/web/backend/catalog/`（专家/技能/插件分目录），
不再依赖代码里的种子助手常量或内置技能名单。
"""
from __future__ import annotations

from pathlib import Path

from app.catalog.items import CatalogItem, CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.core.settings import Settings


def _service(tmp_path: Path) -> CatalogService:
    """组装 CatalogService（三类条目均来自一次 catalog 扫描）。

    Args:
        tmp_path: 临时数据目录（不含 catalog/ 时只用随仓库的内容根）。

    Returns:
        CatalogService。
    """
    settings = Settings(data_dir=str(tmp_path))
    return CatalogService(index=scan_catalog(catalog_roots(settings)))


def test_items_come_from_catalog(tmp_path):
    """三类条目均来自 catalog/（专家不再来自代码常量、技能不再来自内置名单）。"""
    svc = _service(tmp_path)
    assert {i.id for i in svc.list_items("expert")} == {"asst-research", "asst-data"}
    assert {i.id for i in svc.list_items("skill")} == {
        "data-analysis", "pdf-extraction", "office-doc"}
    assert "spec_agent" in {i.id for i in svc.list_items("plugin")}


def test_skill_description_comes_from_skill_md(tmp_path):
    """技能条目的 description 取自 SKILL.md frontmatter（单一来源）。"""
    svc = _service(tmp_path)
    office = next(i for i in svc.list_items("skill") if i.id == "office-doc")
    assert "生成 Word" in office.description


def test_unknown_kind_returns_empty(tmp_path):
    """未知 kind 返回空列表（不抛异常）。"""
    svc = _service(tmp_path)
    assert svc.list_items("nope") == []


def test_names_come_from_packages(tmp_path):
    """展示名取自各自的包（专家 expert.json / 插件 plugin.json）。"""
    svc = _service(tmp_path)
    experts = {i.id: i for i in svc.list_items("expert")}
    plugins = {i.id: i for i in svc.list_items("plugin")}
    assert experts["asst-research"].name == "科研助手"
    assert plugins["spec_agent"].name == "Spec_Agent 谱图解析"


def test_all_items_are_builtin_source(tmp_path):
    """目录条目的 source 恒为 builtin（首期只有内置项）。"""
    svc = _service(tmp_path)
    for kind in ("expert", "skill", "plugin"):
        for item in svc.list_items(kind):
            assert isinstance(item, CatalogItem)
            assert item.kind == kind and item.source == "builtin"


def test_all_items_aggregates_three_kinds(tmp_path):
    """all_items 汇总三类（每类都非空）。"""
    svc = _service(tmp_path)
    items = svc.all_items()
    assert {i.kind for i in items} == {"expert", "skill", "plugin"}
    # 按 kind 顺序 expert/skill/plugin 聚合，各类内按 id 排序
    assert [i.kind for i in items] == sorted(
        (i.kind for i in items), key=("expert", "skill", "plugin").index)


def test_non_mapping_frontmatter_does_not_raise(tmp_path):
    """frontmatter 是合法 YAML 但非映射（标量/列表）时：不抛异常，描述留空。"""
    catalog = tmp_path / "catalog"
    d = catalog / "skills" / "weird-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nhello\n---\n正文\n", encoding="utf-8")
    (catalog / "experts").mkdir()
    (catalog / "plugins").mkdir()

    from app.catalog.loader import scan_catalog
    from app.catalog.items import CatalogService

    items = CatalogService(scan_catalog([catalog])).list_items("skill")
    assert [i.id for i in items] == ["weird-skill"]
    assert items[0].description == ""


def test_skill_dir_name_mismatch_is_tolerable(tmp_path, caplog):
    """目录名与 frontmatter name 不一致：以目录名为 id，仅告警。"""
    catalog = tmp_path / "catalog"
    d = catalog / "skills" / "dir-name"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: 示例\n---\n正文\n", encoding="utf-8")
    (catalog / "experts").mkdir()
    (catalog / "plugins").mkdir()

    from app.catalog.loader import scan_catalog
    from app.catalog.items import CatalogService

    with caplog.at_level("WARNING", logger="app.catalog.items"):
        items = CatalogService(scan_catalog([catalog])).list_items("skill")
    assert [i.id for i in items] == ["dir-name"]
    assert any("不一致" in r.getMessage() for r in caplog.records)
