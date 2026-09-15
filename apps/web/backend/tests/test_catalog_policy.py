"""管理员目录策略存储测试。"""
from __future__ import annotations

import asyncio

import pytest

from app.catalog.policy import POLICY_COLLECTION, CatalogPolicyRepo


async def test_default_policy_is_public_enabled(store):
    """无记录时缺省 public + 默认启用（保持升级前语义）。"""
    repo = CatalogPolicyRepo(store)
    policy = await repo.get("plugin", "spec_agent")
    assert policy == {"visibility": "public", "default_enabled": True}


async def test_set_and_get_policy(store):
    """写入后可读回；再次写入是更新同一条记录。"""
    repo = CatalogPolicyRepo(store)
    await repo.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    assert await repo.get("plugin", "spec_agent") == {
        "visibility": "hidden", "default_enabled": False}

    await repo.set("plugin", "spec_agent", visibility="public", default_enabled=False)
    docs = await store.list(POLICY_COLLECTION)
    assert len(docs) == 1
    assert await repo.get("plugin", "spec_agent") == {
        "visibility": "public", "default_enabled": False}


async def test_all_policies_returns_overrides_only(store):
    """all_policies 只返回显式配置过的条目（缺省由 get 兜底）。"""
    repo = CatalogPolicyRepo(store)
    assert await repo.all_policies() == {}
    await repo.set("skill", "office-doc", visibility="hidden", default_enabled=False)
    assert await repo.all_policies() == {
        "skill:office-doc": {"visibility": "hidden", "default_enabled": False}}


async def test_invalid_visibility_rejected(store):
    """非法 visibility 拒绝写入（fail-closed）。"""
    repo = CatalogPolicyRepo(store)
    with pytest.raises(ValueError, match="visibility"):
        await repo.set("plugin", "x", visibility="everyone", default_enabled=True)
    assert await store.get(POLICY_COLLECTION, "plugin:x") is None


async def test_policy_id_is_kind_scoped(store):
    """同一 id 在不同 kind 下互不干扰。"""
    repo = CatalogPolicyRepo(store)
    await repo.set("skill", "demo", visibility="hidden", default_enabled=False)
    await repo.set("plugin", "demo", visibility="public", default_enabled=True)
    assert (await repo.get("skill", "demo"))["visibility"] == "hidden"
    assert (await repo.get("plugin", "demo"))["visibility"] == "public"


async def test_concurrent_set_does_not_raise(store):
    """并发写同一新条目的策略：不抛异常，最终状态为其中一次写入。"""
    repo = CatalogPolicyRepo(store)
    results = await asyncio.gather(
        repo.set("plugin", "race", visibility="hidden", default_enabled=False),
        repo.set("plugin", "race", visibility="public", default_enabled=True),
        return_exceptions=True,
    )
    assert all(not isinstance(r, Exception) for r in results), results
    docs = await store.list(POLICY_COLLECTION)
    assert len(docs) == 1


async def test_dirty_visibility_falls_back_to_hidden(store):
    """库中非法 visibility 值按最严处理（fail-closed，不 fail-open）。"""
    await store.insert(POLICY_COLLECTION, {
        "_id": "plugin:dirty", "kind": "plugin", "item_id": "dirty",
        "visibility": "zzz", "default_enabled": True})
    assert (await CatalogPolicyRepo(store).get("plugin", "dirty"))["visibility"] == "hidden"

    await store.insert(POLICY_COLLECTION, {
        "_id": "plugin:dirty2", "kind": "plugin", "item_id": "dirty2",
        "visibility": "zzz", "default_enabled": True})
    assert (await CatalogPolicyRepo(store).all_policies())["plugin:dirty2"]["visibility"] == "hidden"
