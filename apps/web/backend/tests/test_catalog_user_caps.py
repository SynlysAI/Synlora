"""用户能力安装记录测试。"""
from __future__ import annotations

from app.catalog.user_caps import USER_CAPS_COLLECTION, UserCapabilityRepo


async def test_install_and_list(store):
    """安装写入记录，按用户查询返回该用户的条目。"""
    repo = UserCapabilityRepo(store)
    assert await repo.list_for_user("u1") == []
    await repo.install("u1", "plugin", "spec_agent")
    await repo.install("u1", "skill", "office-doc")
    await repo.install("u2", "plugin", "spec_agent")

    assert await repo.list_for_user("u1") == ["plugin:spec_agent", "skill:office-doc"]
    assert await repo.list_for_user("u2") == ["plugin:spec_agent"]
    assert await repo.is_installed("u1", "plugin", "spec_agent") is True
    assert await repo.is_installed("u2", "skill", "office-doc") is False


async def test_install_is_idempotent(store):
    """重复安装不产生第二条记录，也不刷新 installed_at。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    first = await store.get(USER_CAPS_COLLECTION, "u1:plugin:spec_agent")
    await repo.install("u1", "plugin", "spec_agent")

    docs = await store.list(USER_CAPS_COLLECTION)
    assert len(docs) == 1
    assert docs[0]["installed_at"] == first["installed_at"]


async def test_uninstall_removes_record(store):
    """卸载删除记录；未安装时卸载返回 False。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    assert await repo.uninstall("u1", "plugin", "spec_agent") is True
    assert await repo.uninstall("u1", "plugin", "spec_agent") is False
    assert await repo.list_for_user("u1") == []


async def test_list_by_kind(store):
    """按 kind 过滤（市场页按类分栏用）。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    await repo.install("u1", "skill", "office-doc")
    assert await repo.list_for_user("u1", kind="skill") == ["skill:office-doc"]


async def test_users_are_isolated(store):
    """不同用户互不影响（多租户隔离）。"""
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "plugin", "spec_agent")
    assert await repo.is_installed("u2", "plugin", "spec_agent") is False
    assert await repo.list_for_user("u2") == []
