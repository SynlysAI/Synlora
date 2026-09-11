"""ProjectService 单测（迁移/建目录/目录树/删除/并发）。"""
import asyncio
import shutil
from pathlib import Path

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
    # 全新用户只读列表，不应顺手落下 default 目录（迁移/补种无副作用）
    assert not (tmp_path / "workspaces" / "u2" / "default").exists()


async def test_tree_lists_one_level(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    (root / "files" / "sub").mkdir()
    entries = await svc.list_dir("u1", p["_id"], "files")
    names = {e["name"]: e["is_dir"] for e in entries}
    assert names == {"a.txt": False, "sub": True}


async def test_tree_path_relative_and_dirs_first(tmp_path, store):
    """path 为相对项目根的正斜杠路径，且目录项排在文件前（前端树渲染依赖顺序）。"""
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    (root / "files" / "sub").mkdir()
    entries = await svc.list_dir("u1", p["_id"], "files")
    # 用有序列表断言：既校验顺序，也校验没被 dict 比较吃掉排序信息
    assert [e["path"] for e in entries] == ["files/sub", "files/a.txt"]
    assert [e["is_dir"] for e in entries] == [True, False]
    assert [e["name"] for e in entries] == ["sub", "a.txt"]


async def test_list_dir_missing_project_raises(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    with pytest.raises(ValueError):
        await svc.list_dir("u1", "no-such-project", "")


async def test_list_dir_on_file_raises(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        await svc.list_dir("u1", p["_id"], "files/a.txt")


async def test_list_dir_works_with_relative_data_root(tmp_path, store, monkeypatch):
    """data_root 为相对路径（部署默认 ../data）时 list_dir 仍可用（回归：entry.relative_to(root)）。"""
    monkeypatch.chdir(tmp_path)
    svc = ProjectService(store, Path("data"))
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    entries = await svc.list_dir("u1", p["_id"], "files")
    assert [e["name"] for e in entries] == ["a.txt"]
    assert entries[0]["path"] == "files/a.txt"


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


async def test_delete_missing_project_returns_false(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    assert await svc.delete_project("u1", "no-such-project") is False


async def test_delete_cross_user_returns_false(tmp_path, store):
    """他人项目不可删（get 校验归属，返回 None → False）。"""
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    assert await svc.delete_project("u2", p["_id"]) is False
    assert await svc.get("u1", p["_id"]) is not None


async def test_delete_falls_back_to_trash_and_drops_record(tmp_path, store, monkeypatch):
    """rmtree 失败时目录改名 trash 保住数据，记录仍删除，返回 True（成功语义）。"""
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "实验一")

    def _boom(*args, **kwargs):
        raise OSError("rmtree 失败（模拟被占用）")

    monkeypatch.setattr(shutil, "rmtree", _boom)
    removed = await svc.delete_project("u1", p["_id"])
    assert removed is True
    user_dir = tmp_path / "workspaces" / "u1"
    assert not (user_dir / "实验一").exists()
    assert len(list(user_dir.glob("实验一.trash-*"))) == 1
    assert p["_id"] not in [x["_id"] for x in await svc.list_projects("u1")]


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
