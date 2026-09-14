"""插件配置存储测试（敏感字段加密、留空保持原值、安装状态判定）。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.plugins.config_store import CONFIG_COLLECTION, PluginConfigStore

SCHEMA = [
    {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
    {"key": "token", "label": "凭证", "type": "password", "secret": True},
]


@pytest.fixture
def fernet_key() -> str:
    """一次性 Fernet key。"""
    return Fernet.generate_key().decode()


async def test_save_and_resolve_roundtrip(store, fernet_key):
    """非敏感字段明文、敏感字段密文落库；resolved 还原扁平配置。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "secret-token"}, SCHEMA)

    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["config"]["base_url"] == "http://x"
    assert "secret-token" not in str(doc["secrets"])  # 密文落库
    assert doc["secrets"]["token"]["encrypted"] is True

    assert await cs.resolved("demo") == {"base_url": "http://x", "token": "secret-token"}


async def test_empty_secret_keeps_previous_value(store, fernet_key):
    """敏感字段留空表示保持原值（前端密码框"留空保持不变"语义）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "tok-1"}, SCHEMA)
    await cs.save("demo", {"base_url": "http://y", "token": ""}, SCHEMA)

    resolved = await cs.resolved("demo")
    assert resolved["base_url"] == "http://y" and resolved["token"] == "tok-1"


async def test_plaintext_when_no_fernet_key(store):
    """未配 Fernet key 时明文存储（开发模式，与 provider api_key 同口径）。"""
    cs = PluginConfigStore(store, "")
    await cs.save("demo", {"base_url": "http://x", "token": "tok"}, SCHEMA)

    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["secrets"]["token"] == {"value": "tok", "encrypted": False}
    assert await cs.resolved("demo") == {"base_url": "http://x", "token": "tok"}


async def test_installed_and_all_resolved(store, fernet_key):
    """安装状态 = 有配置记录；all_resolved 返回全部已安装插件。"""
    cs = PluginConfigStore(store, fernet_key)
    assert await cs.installed_ids() == []
    await cs.save("demo", {"base_url": "http://x", "token": "t"}, SCHEMA)
    assert await cs.installed_ids() == ["demo"]
    assert (await cs.all_resolved())["demo"]["base_url"] == "http://x"


async def test_resolved_missing_plugin_returns_empty(store, fernet_key):
    """未安装插件的 resolved 返回空 dict（不抛异常）。"""
    assert await PluginConfigStore(store, fernet_key).resolved("nope") == {}


async def test_repeated_save_updates_same_record(store, fernet_key):
    """重复保存是更新同一条记录（不新增文档、保留 created_at）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "t1"}, SCHEMA)
    first = await store.get(CONFIG_COLLECTION, "demo")
    await cs.save("demo", {"base_url": "http://z", "token": "t2"}, SCHEMA)

    docs = await store.list(CONFIG_COLLECTION)
    assert len(docs) == 1
    assert docs[0]["_id"] == "demo"
    assert docs[0]["created_at"] == first["created_at"]
    assert await cs.resolved("demo") == {"base_url": "http://z", "token": "t2"}
