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
    assert skills[0]["builtin"] is True


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


def test_delete_builtin_rejected(tmp_path):
    _seed_one(tmp_path)
    with pytest.raises(ValueError):
        SkillService(tmp_path).delete_skill("data-analysis")


def test_seed_builtins_is_idempotent(tmp_path):
    svc = SkillService(tmp_path)
    svc.seed_builtins()
    md = tmp_path / "skills" / "data-analysis" / "SKILL.md"
    assert md.is_file()
    first = md.read_text(encoding="utf-8")
    md.write_text(first + "\n用户追加", encoding="utf-8")
    svc.seed_builtins()                          # 二次播种不覆盖
    assert md.read_text(encoding="utf-8").endswith("用户追加")


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
