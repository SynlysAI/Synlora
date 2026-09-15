"""管理员目录策略存储测试。"""
from __future__ import annotations

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
