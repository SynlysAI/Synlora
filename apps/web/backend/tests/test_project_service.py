"""ProjectService 单测（默认工作区/建目录/目录树/删除/并发）。"""
import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.project_service import ProjectNameTaken, ProjectService


async def test_list_returns_empty_for_brand_new_user(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    assert await svc.list_projects("u2") == []
    # 全新用户只读列表，不应顺手落下 default 目录（补种无副作用）
    assert not (tmp_path / "users" / "u2" / "workspaces" / "default").exists()


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


async def test_create_two_projects_same_name_rejected(tmp_path, store):
    """显示名唯一：第二次建同名工作区被拒（ProjectNameTaken）。"""
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    assert a["dir_name"] == "实验一"
    with pytest.raises(ProjectNameTaken):
        await svc.create_project("u1", "实验一")
    assert [p["name"] for p in await svc.list_projects("u1")] == ["实验一"]


async def test_delete_frees_name_for_recreate(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    assert await svc.delete_project("u1", a["_id"]) is True
    b = await svc.create_project("u1", "实验一")
    assert b["dir_name"] == "实验一"
    assert (tmp_path / "users" / "u1" / "workspaces" / "实验一").is_dir()


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
    user_dir = tmp_path / "users" / "u1" / "workspaces"
    assert not (user_dir / "实验一").exists()
    assert len(list(user_dir.glob("实验一.trash-*"))) == 1
    assert p["_id"] not in [x["_id"] for x in await svc.list_projects("u1")]


async def test_concurrent_create_same_name_only_one_wins(tmp_path, store):
    """并发建同名工作区：per-user 锁保证「查重 → 落库」原子，只有一个能成。

    没有这把锁，两个请求会各自查到「名字没被占」而同时建成功——正是这条用例守的东西。
    """
    svc = ProjectService(store, tmp_path)
    results = await asyncio.gather(
        svc.create_project("u1", "实验一"),
        svc.create_project("u1", "实验一"),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, dict)]
    rejected = [r for r in results if isinstance(r, ProjectNameTaken)]
    assert len(ok) == 1 and len(rejected) == 1
    assert [p["name"] for p in await svc.list_projects("u1")] == ["实验一"]


async def test_concurrent_first_message_creates_single_default_project(tmp_path, store):
    """并发首条消息只建一个默认工作区，且目录名恒为 default（不因避让变成 default-2）。"""
    service = ProjectService(store, tmp_path)
    results = await asyncio.gather(*[service.resolve_active_project("u1", None) for _ in range(5)])
    assert {r["_id"] for r in results} == {results[0]["_id"]}
    assert results[0]["dir_name"] == "default"
    assert (tmp_path / "users" / "u1" / "workspaces" / "default").is_dir()


async def test_user_project_cannot_take_default_dir_name(tmp_path, store):
    """用户自建工作区永不占用 default 这个名字，default 恒留给默认工作区。

    先建用户项目（此时还没有默认工作区，占用集合里一条记录都没有）：不把 default
    钉成已占用，用户项目就会直接拿走它，默认工作区只能退成 default-2。
    """
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "default")
    assert project["dir_name"] != "default"
    user_dir = tmp_path / "users" / "u1" / "workspaces"
    assert not (user_dir / "default").exists()  # default 没被用户项目吃掉
    # 该用户已无项目时创建默认工作区，仍拿得到 default（未被避让成 default-2）
    assert await service.delete_project("u1", project["_id"]) is True
    default_project = await service.resolve_active_project("u1", None)
    assert default_project["dir_name"] == "default"
    assert (user_dir / "default").is_dir()


async def test_case_variant_of_default_does_not_take_default_dir(tmp_path, store):
    """Windows 大小写不敏感：Default/DEFAULT 与 default 同目录，同样必须避让。"""
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "Default")
    assert project["dir_name"].casefold() != "default"


async def test_rename_to_default_name_does_not_take_default_dir(tmp_path, store):
    """把用户项目改名为 default 时同样不能占用 default 目录（改名的 create 路径）。"""
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "exp")
    renamed = await service.rename_project("u1", project["_id"], "default")
    assert renamed["dir_name"] != "default"
    assert not (tmp_path / "users" / "u1" / "workspaces" / "default").exists()


async def test_rename_default_project_keeps_default_dir(tmp_path, store):
    """重命名默认工作区只改显示名：目录名恒为 default，否则会再长出第二个 default。"""
    service = ProjectService(store, tmp_path)
    project = await service.resolve_active_project("u1", None)
    renamed = await service.rename_project("u1", project["_id"], "我的常用")
    assert renamed["name"] == "我的常用" and renamed["dir_name"] == "default"
    assert (tmp_path / "users" / "u1" / "workspaces" / "default").is_dir()


async def test_default_project_reuses_orphan_default_dir(tmp_path, store):
    """磁盘残留 default 孤儿目录（DB 无记录）时直接复用，不另造 default-2。"""
    (tmp_path / "users" / "u1" / "workspaces" / "default").mkdir(parents=True)
    service = ProjectService(store, tmp_path)
    project = await service.resolve_active_project("u1", None)
    assert project["dir_name"] == "default"
