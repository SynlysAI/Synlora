"""技能文件服务单测（扫描/解析/写入 SKILL.md）。"""
import pytest

from app.services.skill_service import SkillService


def _seed_one(root, name="data-analysis", desc="数据分析"):
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ntags:\n  - 统计\n---\n\n# 目标\n分析数据",
        encoding="utf-8")


def test_scan_reads_frontmatter(tmp_path):
    _seed_one(tmp_path)
    skills = SkillService(tmp_path).list_skills()
    assert skills[0]["name"] == "data-analysis"
    assert skills[0]["description"] == "数据分析"
    assert skills[0]["tags"] == ["统计"]
    assert skills[0]["builtin"] is False   # 公共层技能：builtin 只由来源根（只读根）决定


def test_write_then_read_roundtrip(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_skill(name="my-skill", description="用途", content="# 目标\n做点事")
    body = svc.read_body("my-skill")
    assert body.startswith("# 目标")
    assert not body.startswith("---")          # 正文不含 frontmatter


def test_write_rejects_bad_name(tmp_path):
    with pytest.raises(ValueError):
        SkillService(tmp_path).write_skill(name="Bad Name!", description="d", content="x")


def test_read_unknown_skill_returns_none(tmp_path):
    assert SkillService(tmp_path).read_body("nope") is None


def test_scan_skips_broken_skill(tmp_path):
    (tmp_path / "skills" / "broken").mkdir(parents=True)
    (tmp_path / "skills" / "broken" / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    assert SkillService(tmp_path).list_skills() == []


def test_delete_public_skill_succeeds_even_if_name_matches_builtin(tmp_path):
    """公共层技能可删（builtin 只由来源根决定，不再看硬编码名单）。"""
    _seed_one(tmp_path)
    svc = SkillService(tmp_path)
    assert svc.list_skills()[0]["builtin"] is False
    assert svc.delete_skill("data-analysis") is True
    assert not (tmp_path / "skills" / "data-analysis").exists()


def test_migrate_removes_identical_legacy_copies(tmp_path):
    """迁移清理：与 catalog 字节一致的旧副本被删；内容不同（管理员改过）保留。"""
    catalog_root = tmp_path / "catalog" / "skills"
    (catalog_root / "office-doc").mkdir(parents=True)
    (catalog_root / "office-doc" / "SKILL.md").write_text(
        "---\nname: office-doc\ndescription: 生成办公文档\n---\n正文\n", encoding="utf-8")
    (catalog_root / "data-analysis").mkdir(parents=True)
    (catalog_root / "data-analysis" / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: 官方\n---\n官方正文\n", encoding="utf-8")

    svc = SkillService(tmp_path / "data")
    (svc.skills_dir / "office-doc").mkdir(parents=True)
    (svc.skills_dir / "office-doc" / "SKILL.md").write_text(
        (catalog_root / "office-doc" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
    (svc.skills_dir / "data-analysis").mkdir(parents=True)
    (svc.skills_dir / "data-analysis" / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: 我改过的\n---\n我的正文\n", encoding="utf-8")

    assert svc.migrate_legacy_builtin_copies(catalog_root) == ["office-doc"]
    assert not (svc.skills_dir / "office-doc").exists()
    assert (svc.skills_dir / "data-analysis").exists()


def test_catalog_root_skills_are_readonly_and_builtin(tmp_path):
    """catalog 根作为只读技能根：可列可读、builtin=True、删不掉。"""
    catalog_root = tmp_path / "catalog" / "skills"
    (catalog_root / "office-doc").mkdir(parents=True)
    (catalog_root / "office-doc" / "SKILL.md").write_text(
        "---\nname: office-doc\ndescription: 生成办公文档\n---\n正文\n", encoding="utf-8")

    svc = SkillService(tmp_path / "data", extra_roots=[catalog_root])
    skills = {s["name"]: s for s in svc.list_skills()}
    assert skills["office-doc"]["builtin"] is True
    assert svc.read_body("office-doc") == "正文"
    assert svc.delete_skill("office-doc") is False
    assert (catalog_root / "office-doc" / "SKILL.md").exists()


def test_frontmatter_handles_quotes_and_fullwidth_colon(tmp_path):
    """description 含全角冒号与引号时解析不能出错（手写解析器会在这里翻车）。"""
    d = tmp_path / "skills" / "quirky"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        '---\n'
        'name: quirky\n'
        'description: 用户说"分析数据"时使用：先看 files/，再出图\n'
        'tags:\n  - 统计\n  - 可视化\n'
        '---\n\n# 目标\n做点事\n',
        encoding="utf-8")
    skill = SkillService(tmp_path).list_skills()[0]
    assert skill["description"] == '用户说"分析数据"时使用：先看 files/，再出图'
    assert skill["tags"] == ["统计", "可视化"]


def test_write_skill_roundtrips_unicode_description(tmp_path):
    """落盘再读回，含中文/引号/冒号的 description 不丢字。"""
    svc = SkillService(tmp_path)
    desc = '把 "CSV" 转成图表：输出到 output/'
    svc.write_skill(name="rt-skill", description=desc, content="# 目标\n做事")
    assert svc.list_skills()[0]["description"] == desc


def test_delete_skill_rejects_path_traversal(tmp_path):
    """非法技能名不得删除 skills 根之外的目录。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("important", encoding="utf-8")

    svc = SkillService(tmp_path)
    for bad in ("../outside", "../../outside", "a/b", "", ".", ".."):
        assert svc.delete_skill(bad) is False
    assert outside.exists() and (outside / "victim.txt").exists()


def test_read_body_rejects_path_traversal(tmp_path):
    """非法技能名不得读到 skills 根之外的内容。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: leak\ndescription: 泄露\n---\n\nSECRET", encoding="utf-8")

    svc = SkillService(tmp_path)
    for bad in ("../outside", "../../outside", "a/b", "", ".", ".."):
        assert svc.read_body(bad) is None


def test_extra_roots_are_listed_and_readable(tmp_path):
    """插件技能根：列在技能表里（builtin=True），正文可读，且不可删除。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 核磁谱图解析\n---\n正文内容\n",
        encoding="utf-8")

    svc = SkillService(data_root, extra_roots=[plugin_skills])
    skills = {s["name"]: s for s in svc.list_skills()}
    assert skills["spec-nmr"]["builtin"] is True
    assert svc.read_body("spec-nmr") == "正文内容"

    # 插件技能不落用户技能目录，删除只作用于用户目录（返回 False 而非删掉插件技能）
    assert svc.delete_skill("spec-nmr") is False
    assert (plugin_skills / "spec-nmr" / "SKILL.md").exists()


def test_extra_root_added_at_runtime(tmp_path):
    """add_root：运行期安装插件后新技能立即可见。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    svc = SkillService(data_root)
    assert [s["name"] for s in svc.list_skills()] == []

    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 核磁谱图解析\n---\n正文\n", encoding="utf-8")
    svc.add_root(plugin_skills)
    assert [s["name"] for s in svc.list_skills()] == ["spec-nmr"]


def test_user_skill_shadows_plugin_skill(tmp_path):
    """同名时用户技能优先（插件技能不覆盖用户目录里的同名技能）。"""
    data_root = tmp_path / "data"
    user_dir = data_root / "skills" / "spec-nmr"
    user_dir.mkdir(parents=True)
    (user_dir / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 用户版本\n---\n用户正文\n", encoding="utf-8")
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 插件版本\n---\n插件正文\n", encoding="utf-8")

    svc = SkillService(data_root, extra_roots=[plugin_skills])
    assert svc.read_body("spec-nmr") == "用户正文"
    assert len([s for s in svc.list_skills() if s["name"] == "spec-nmr"]) == 1


def test_extra_root_missing_dir_is_fine(tmp_path):
    """额外根目录不存在时不报错（插件包无 skills/ 目录）。"""
    svc = SkillService(tmp_path / "data", extra_roots=[tmp_path / "nope"])
    assert svc.list_skills() == []
    assert svc.read_body("anything") is None


def test_broken_user_skill_falls_back_to_plugin(tmp_path):
    """用户目录同名技能损坏时，read_body 回退到插件根（与 list_skills 语义一致）。

    回归：修复前 read_body 命中损坏目录即返回 None，导致技能进索引却没有正文。
    """
    data_root = tmp_path / "data"
    user_dir = data_root / "skills" / "spec-nmr"
    user_dir.mkdir(parents=True)
    (user_dir / "SKILL.md").write_text("没有 frontmatter 的坏文件", encoding="utf-8")
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 插件版本\n---\n插件正文\n", encoding="utf-8")

    svc = SkillService(data_root, extra_roots=[plugin_skills])
    assert [s["name"] for s in svc.list_skills()] == ["spec-nmr"]
    assert svc.read_body("spec-nmr") == "插件正文"
    assert svc.read_body("spec-nmr") != ""  # 不得出现"有索引无正文"


def test_add_root_normalizes_path(tmp_path):
    """同一目录用不同写法传入只挂一次（规范化去重）。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 核磁\n---\n正文\n", encoding="utf-8")

    svc = SkillService(data_root)
    svc.add_root(plugin_skills)
    svc.add_root(plugin_skills / "." / ".." / "skills")  # 等价写法
    assert len(svc._extra_roots) == 1
    assert [s["name"] for s in svc.list_skills()] == ["spec-nmr"]
