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


async def test_undeclared_keys_are_ignored(store, fernet_key):
    """schema 未声明的 key 被忽略（漏传 schema 时敏感值不得明文落库）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "rogue": "x", "token": "s"}, SCHEMA)

    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["config"] == {"base_url": "http://x"}  # rogue 未落库
    assert doc["secrets"]["token"]["encrypted"] is True
    assert await cs.resolved("demo") == {"base_url": "http://x", "token": "s"}


async def test_secret_not_declared_stays_out_of_config(store, fernet_key):
    """schema 为空时，可疑字段不会明文写进 config。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"token": "SUPER_SECRET"}, [])

    assert await cs.resolved("demo") == {}
    assert "SUPER_SECRET" not in str(await store.get(CONFIG_COLLECTION, "demo"))


async def test_whitespace_secret_keeps_previous_value(store, fernet_key):
    """敏感字段纯空白也视为留空（保持原值）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "tok-1"}, SCHEMA)
    await cs.save("demo", {"base_url": "http://y", "token": "   "}, SCHEMA)

    assert (await cs.resolved("demo"))["token"] == "tok-1"


async def test_resolved_raises_clear_error_on_key_rotation(store, fernet_key):
    """密钥轮换后用新 key 解密 → 抛出清晰的 RuntimeError（不裸抛 InvalidToken）。"""
    await PluginConfigStore(store, fernet_key).save(
        "demo", {"base_url": "http://x", "token": "tok"}, SCHEMA)

    rotated = PluginConfigStore(store, Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="解密失败"):
        await rotated.resolved("demo")


async def test_all_resolved_skips_undecryptable_plugin(store, fernet_key):
    """运行期批量注入：单条解密失败只跳过该插件，不打挂其余。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("good", {"base_url": "http://x", "token": "t"}, SCHEMA)
    await cs.save("bad", {"base_url": "http://y", "token": "t"}, SCHEMA)

    rotated = PluginConfigStore(store, Fernet.generate_key().decode())
    out = await rotated.all_resolved()
    assert "bad" not in out and "good" not in out  # 两把 key 都不同：均应跳过

    # 同 key 场景：只有 bad 被外部改成不可解时才跳过（用直接改库文档模拟畸形）
    await store.update(CONFIG_COLLECTION, "bad",
                      {"secrets": {"token": {"value": "not-a-fernet-token", "encrypted": True}}})
    out2 = await cs.all_resolved()
    assert "good" in out2 and "bad" not in out2


async def test_malformed_secret_item_is_skipped(store, fernet_key):
    """secrets 项结构畸形（非 dict）时跳过该项，不抛 AttributeError。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "t"}, SCHEMA)
    await store.update(CONFIG_COLLECTION, "demo", {"secrets": {"token": "legacy-plain"}})

    assert await cs.resolved("demo") == {"base_url": "http://x"}


async def test_clear_secret_removes_stored_value(store, fernet_key):
    """clear_secrets 删除已存凭证：之后 resolved 不再返回该字段。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "tok-1"}, SCHEMA)
    assert (await cs.resolved("demo"))["token"] == "tok-1"

    await cs.save("demo", {"base_url": "http://x"}, SCHEMA, clear_secrets=["token"])
    assert "token" not in await cs.resolved("demo")
    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["secrets"] == {} and doc["config"]["base_url"] == "http://x"


async def test_clear_then_value_wins(store, fernet_key):
    """同一字段既 clear 又给新值 → 新值生效（显式输入优先）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "old"}, SCHEMA)
    await cs.save("demo", {"token": "new"}, SCHEMA, clear_secrets=["token"])
    assert (await cs.resolved("demo"))["token"] == "new"


async def test_clear_undeclared_key_ignored(store, fernet_key):
    """clear_secrets 里出现非敏感字段名时忽略（不误删非敏感数据）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "t"}, SCHEMA)
    await cs.save("demo", {"base_url": "http://y"}, SCHEMA, clear_secrets=["base_url"])
    assert (await cs.resolved("demo")) == {"base_url": "http://y", "token": "t"}


async def test_installed_ids_excludes_user_docs(store, fernet_key):
    """installed_ids 只统计公共配置，用户维度文档不算"插件已安装"。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://public"}, SCHEMA)
    await cs.save_for_user("u1", "demo", {"token": "mine"}, SCHEMA)

    assert await cs.installed_ids() == ["demo"]
    assert await cs.resolved_for_user("u1", "demo") == {
        "base_url": "http://public", "token": "mine"}
    # 只有个人配置、没有公共配置时，插件整体视为"未安装"
    await cs.save_for_user("u1", "solo", {"token": "mine"}, SCHEMA)
    assert await cs.installed_ids() == ["demo"]
