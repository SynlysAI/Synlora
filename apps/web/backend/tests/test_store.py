"""DocumentStore 单测（sqlite 全跑；mongo 仅在设置 TEST_MONGODB_URI 时跑）。"""
import aiosqlite
import pytest

from app.db.store import create_store


async def test_insert_get_update_delete(store):
    doc = await store.insert("sessions", {"_id": "s1", "user_id": "u1", "title": "t"})
    assert doc["_id"] == "s1"
    got = await store.get("sessions", "s1")
    assert got["title"] == "t"
    upd = await store.update("sessions", "s1", {"title": "t2"})
    assert upd["title"] == "t2" and upd["user_id"] == "u1"   # 合并不丢字段
    assert await store.get("sessions", "nope") is None
    assert await store.delete("sessions", "s1") is True
    assert await store.get("sessions", "s1") is None
    assert await store.delete("sessions", "s1") is False


async def test_list_filter_and_sort(store):
    for i in range(3):
        await store.insert("events", {"_id": f"e{i}", "session_id": "s1", "seq": 2 - i})
    await store.insert("events", {"_id": "x", "session_id": "s2", "seq": 0})
    rows = await store.list("events", filters={"session_id": "s1"}, sort=[("seq", 1)])
    assert [r["seq"] for r in rows] == [0, 1, 2]
    rows_desc = await store.list("events", filters={"session_id": "s1"}, sort=[("seq", -1)])
    assert rows_desc[0]["seq"] == 2
    limited = await store.list("events", filters={"session_id": "s1"}, limit=2)
    assert len(limited) == 2


async def test_duplicate_id_raises(store):
    await store.insert("files", {"_id": "f1", "user_id": "u1"})
    with pytest.raises(ValueError):
        await store.insert("files", {"_id": "f1", "user_id": "u2"})


async def test_index_columns_extracted(tmp_path):
    """COLLECTION_INDEXES 声明的索引字段应提取为 sqlite 真实列（可 SQL 直查）。"""
    path = str(tmp_path / "t.db")
    s = create_store("sqlite", sqlite_path=path)
    await s.init()
    await s.insert("events", {"_id": "e1", "session_id": "s1", "seq": 5})
    await s.close()
    async with aiosqlite.connect(path) as db:
        cur = await db.execute('SELECT session_id, seq FROM "events" WHERE _id = ?', ("e1",))
        row = await cur.fetchone()
    assert row is not None
    assert tuple(row) == ("s1", "5")


async def test_concurrent_update_no_lost_write(store):
    """并发合并更新不丢字段（20 个并发各加一字段，最终全存在）。"""
    await store.insert("sessions", {"_id": "s1", "user_id": "u1"})
    import asyncio

    async def add_field(i: int):
        await store.update("sessions", "s1", {f"f{i}": i})

    await asyncio.gather(*[add_field(i) for i in range(20)])
    doc = await store.get("sessions", "s1")
    assert all(doc.get(f"f{i}") == i for i in range(20))


async def test_insert_unserializable_doc_raises_value_error(store):
    """文档含不可 JSON 序列化类型时报 ValueError（含 _id 提示）而非裸 TypeError。"""
    await store.insert("files", {"_id": "f-ok", "user_id": "u1"})
    with pytest.raises(ValueError, match="不可 JSON 序列化"):
        await store.insert("files", {"_id": "f-bad", "user_id": object()})


async def test_memory_path_init(tmp_path):
    # 常规文件库自管生命周期：init/insert/get/close 全流程
    s = create_store("sqlite", sqlite_path=str(tmp_path / "m.db"))
    await s.init()
    await s.insert("files", {"_id": "a"})
    assert await s.get("files", "a") is not None
    await s.close()


async def test_in_memory_db():
    # sqlite_path 为空串时按计划使用内存库（:memory:）
    s = create_store("sqlite", sqlite_path="")
    await s.init()
    await s.insert("files", {"_id": "a"})
    assert await s.get("files", "a") is not None
    await s.close()
