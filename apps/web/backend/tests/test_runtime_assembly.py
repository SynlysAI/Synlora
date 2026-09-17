"""运行期技能资源装配测试。"""

from pathlib import PurePosixPath

from app.runtime.assembly import make_skill_resource_reader, prepare_skills
from app.services.skill_service import ResolvedSkill


def _skill(directory, name="demo", plugin_id=None):
    return ResolvedSkill(
        name=name,
        description="描述",
        body="正文",
        directory=directory,
        source="plugin" if plugin_id else "catalog",
        plugin_id=plugin_id,
    )


def test_prepare_skills_maps_docker_resources_readonly(tmp_path):
    """Docker 装配使用固定 /skills/name 根和同一来源目录。"""
    directory = tmp_path / "demo"
    directory.mkdir()

    prepared = prepare_skills([_skill(directory)], sandbox="docker")

    assert prepared.items[0].directory == directory
    assert prepared.resources[0].source == directory
    assert prepared.resources[0].target == PurePosixPath("/skills/demo")
    assert prepared.resource_roots == {"demo": "/skills/demo"}


async def test_resource_reader_rejects_escape_binary_and_truncates(tmp_path):
    """文本资源读取拒绝逃逸/二进制，并显式标记长文本截断。"""
    directory = tmp_path / "demo"
    (directory / "references").mkdir(parents=True)
    (directory / "references" / "long.md").write_text("中" * 50_000, encoding="utf-8")
    (directory / "assets").mkdir()
    (directory / "assets" / "binary.bin").write_bytes(b"\x00\x01")
    reader = make_skill_resource_reader((_skill(directory),))

    escaped = await reader("demo", "../outside.txt")
    binary = await reader("demo", "assets/binary.bin")
    long_text = await reader("demo", "references/long.md")

    assert not escaped.ok and escaped.error == "skill_path_invalid"
    assert not binary.ok and binary.error == "skill_resource_binary"
    assert long_text.ok and long_text.truncated
    assert len(long_text.content) == 48_000
