"""ProjectService 单测（迁移/建目录/目录树/并发）。"""
import asyncio

import pytest

from app.services.project_service import ProjectService


async def test_list_ensures_default_project_on_legacy(tmp_path, store):
    (tmp_path / "workspaces" / "u1" / "files").mkdir(parents=True)
    (tmp_path / "workspaces" / "u1" / "files" / "a.txt").write_text("x", encoding="utf-8")
    svc = ProjectService(store, tmp_path)
    projects = await svc.list_projects("u1")
    assert [p["dir_name"] for p in projects] == ["default"]
    assert (tmp_path / "workspaces" / "u1" / "default" / "files" / "a.txt").exists()


async def test_list_returns_empty_for_brand_new_user(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    assert await svc.list_projects("u2") == []


async def test_tree_lists_one_level(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    (root / "files" / "sub").mkdir()
    entries = await svc.list_dir("u1", p["_id"], "files")
    names = {e["name"]: e["is_dir"] for e in entries}
    assert names == {"a.txt": False, "sub": True}


async def test_tree_rejects_escape(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    with pytest.raises(ValueError):
        await svc.list_dir("u1", p["_id"], "../../../etc")


async def test_create_two_projects_same_name_gets_distinct_dirs(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    b = await svc.create_project("u1", "实验一")
    assert a["dir_name"] == "实验一"
    assert b["dir_name"] == "实验一-2"


async def test_delete_frees_name_for_recreate(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    assert await svc.delete_project("u1", a["_id"]) is True
    b = await svc.create_project("u1", "实验一")
    assert b["dir_name"] == "实验一"
    assert (tmp_path / "workspaces" / "u1" / "实验一").is_dir()


async def test_concurrent_create_same_name_gets_distinct_dirs(tmp_path, store):
    """并发建同名项目必须拿到不同目录（check-then-act 有 per-user 锁兜住）。"""
    svc = ProjectService(store, tmp_path)
    a, b = await asyncio.gather(
        svc.create_project("u1", "实验一"),
        svc.create_project("u1", "实验一"),
    )
    assert {a["dir_name"], b["dir_name"]} == {"实验一", "实验一-2"}


async def test_concurrent_list_seeds_single_default_project(tmp_path, store):
    """并发首次加载只补种一条默认项目（迁移也在锁内）。"""
    (tmp_path / "workspaces" / "u1" / "files").mkdir(parents=True)
    svc = ProjectService(store, tmp_path)
    await asyncio.gather(svc.list_projects("u1"), svc.list_projects("u1"))
    projects = await svc.list_projects("u1")
    assert [p["dir_name"] for p in projects] == ["default"]
