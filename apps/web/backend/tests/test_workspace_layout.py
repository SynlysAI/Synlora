from pathlib import Path

import pytest

from app.services import workspace


def test_project_root_creates_subdirs(tmp_path: Path):
    root = workspace.project_root(tmp_path, "u1", "my-proj")
    assert root == tmp_path / "workspaces" / "u1" / "my-proj"
    for sub in ("files", "output", "tmp"):
        assert (root / sub).is_dir()


def test_sanitize_dir_name_rejects_escapes():
    assert workspace.sanitize_dir_name("../../etc") == "etc"
    assert workspace.sanitize_dir_name("a/b\\c") == "a_b_c"
    assert workspace.sanitize_dir_name("  ") == ""


def test_legacy_migration_moves_subdirs(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "files").mkdir(parents=True)
    (legacy / "files" / "a.txt").write_text("x", encoding="utf-8")
    (legacy / "tmp").mkdir()
    moved = workspace.migrate_legacy_layout(tmp_path, "u1")
    assert moved is True
    assert (legacy / "default" / "files" / "a.txt").read_text(encoding="utf-8") == "x"
    assert (legacy / "default" / "tmp").is_dir()


def test_legacy_migration_is_idempotent(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "files").mkdir(parents=True)
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is True
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is False


def test_free_dir_name_suffixes_on_disk_collision(tmp_path: Path):
    user_dir = tmp_path / "workspaces" / "u1"
    (user_dir / "实验一").mkdir(parents=True)
    assert workspace.free_dir_name(user_dir, "实验一", set()) == "实验一-2"
    assert workspace.free_dir_name(user_dir, "实验一", {"实验一-2"}) == "实验一-3"


def test_remove_dir_renames_to_trash_when_locked(tmp_path: Path, monkeypatch):
    target = tmp_path / "proj"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(workspace.shutil, "rmtree", boom)
    assert workspace.remove_project_dir(target) is False
    assert not target.exists()
    assert list(tmp_path.glob("proj.trash-*"))


def test_project_root_rejects_illegal_dir_name(tmp_path: Path):
    for bad in ("", ".", "..", "../evil", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            workspace.project_root(tmp_path, "u1", bad)


def test_free_dir_name_rejects_empty_base(tmp_path: Path):
    with pytest.raises(ValueError):
        workspace.free_dir_name(tmp_path / "workspaces" / "u1", "", set())


def test_remove_project_dir_missing_target_is_true(tmp_path: Path):
    assert workspace.remove_project_dir(tmp_path / "nope") is True


def test_remove_project_dir_trash_names_unique_with_same_timestamp(tmp_path: Path, monkeypatch):
    """同一时间戳下两次失败改名也必须产生不同 trash 目录。"""
    def boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(workspace.shutil, "rmtree", boom)
    monkeypatch.setattr(workspace.time, "time", lambda: 1789101841)   # 冻结时间戳
    for _ in range(3):
        target = tmp_path / "proj"
        target.mkdir(exist_ok=True)
        (target / "a.txt").write_text("x", encoding="utf-8")
        assert workspace.remove_project_dir(target) is False
    assert len(list(tmp_path.glob("proj.trash-*"))) == 3


def test_migrate_legacy_moves_partial_subdirs(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "output").mkdir(parents=True)          # 没有 files 目录
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is True
    assert (legacy / "default" / "output").is_dir()


def test_migrate_legacy_resumes_after_partial(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "files").mkdir(parents=True)
    (legacy / "tmp").mkdir()
    (legacy / "default" / "files").mkdir(parents=True)   # 模拟上次只搬了一半
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is True
    assert (legacy / "default" / "tmp").is_dir()
    assert (legacy / "files").is_dir()                    # 已存在冲突项保持原样
