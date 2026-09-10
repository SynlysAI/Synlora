"""repository 单测（sqlite 全跑；mongo 随 conftest 参数化跳过）。"""
import pytest
from cryptography.fernet import Fernet
from synlys_harness import EventType, SessionEvent

from app.db.repos import (
    AssistantRepo,
    EventRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
    seed_assistants,
)


def _ev(seq: int, etype: EventType = EventType.LLM_DELTA) -> SessionEvent:
    """构造测试事件。

    Args:
        seq: 事件序号。
        etype: 事件类型。

    Returns:
        SessionEvent 实例。
    """
    return SessionEvent(seq=seq, type=etype, payload={"v": seq}, ts=1000.0 + seq)


@pytest.fixture
def fernet_key() -> str:
    """每个测试独立的 Fernet key。"""
    return Fernet.generate_key().decode()


async def test_provider_key_roundtrip(store, fernet_key):
    repo = ProviderRepo(store, fernet_key=fernet_key)
    created = await repo.create({
        "name": "openai", "base_url": "https://api.openai.com/v1",
        "api_key": "sk-secret", "model_id": "gpt-4o", "enabled": True,
    })
    pid = created["_id"]

    raw = await store.get("providers", pid)          # 存储层无明文字段
    assert "api_key" not in raw
    assert raw["api_key_enc"] != "sk-secret"
    assert raw["api_key_encrypted"] is True

    pub = await repo.get_public(pid)                 # 公共视图无密文
    assert "api_key_enc" not in pub and "api_key" not in pub
    assert pub["has_key"] is True

    dec = await repo.get_decrypted(pid)              # 解密副本拿回原文
    assert dec["api_key"] == "sk-secret"
    assert dec["name"] == "openai"


async def test_provider_no_key_has_key_false(store, fernet_key):
    repo = ProviderRepo(store, fernet_key=fernet_key)
    created = await repo.create({"name": "free", "api_key": ""})
    pub = await repo.get_public(created["_id"])
    assert pub["has_key"] is False
    dec = await repo.get_decrypted(created["_id"])
    assert dec["api_key"] == ""


async def test_provider_key_plain_without_fernet_key(store):
    repo = ProviderRepo(store, fernet_key="")
    created = await repo.create({"name": "local", "api_key": "sk-plain"})
    raw = await store.get("providers", created["_id"])
    assert "api_key" not in raw                      # 仍不存名为 api_key 的字段
    assert raw["api_key_enc"] == "sk-plain"
    assert raw["api_key_encrypted"] is False
    pub = await repo.get_public(created["_id"])
    assert pub["has_key"] is True and "api_key_enc" not in pub
    dec = await repo.get_decrypted(created["_id"])
    assert dec["api_key"] == "sk-plain"


async def test_assistant_builtin_delete_rejected(store):
    repo = AssistantRepo(store)
    builtin = await repo.create({"_id": "asst-x", "name": "X", "builtin": True})
    with pytest.raises(ValueError, match="内置助手不可删除"):
        await repo.delete(builtin["_id"])
    assert await repo.get(builtin["_id"]) is not None   # 未被删除

    normal = await repo.create({"_id": "asst-y", "name": "Y"})
    assert await repo.delete(normal["_id"]) is True
    assert await repo.get(normal["_id"]) is None


async def test_event_append_and_numeric_seq_order(store):
    """seq 用 2/10/1 倒序插入：暴露 sqlite TEXT 索引列字典序（"10"<"2"）问题。"""
    repo = EventRepo(store)
    for seq in (2, 10, 1):
        await repo.append("s1", _ev(seq))
    await repo.append("s2", _ev(99))                 # 其它会话隔离

    events = await repo.list_events("s1")
    assert [e.seq for e in events] == [1, 2, 10]
    assert all(isinstance(e, SessionEvent) for e in events)
    assert events[0].type == EventType.LLM_DELTA
    assert events[0].payload == {"v": 1}


async def test_event_roundtrip_fields(store):
    repo = EventRepo(store)
    ev = SessionEvent(seq=3, type=EventType.TOOL_CALL,
                      payload={"name": "python.run", "arguments": {"code": "1+1"}}, ts=123.5)
    doc = await repo.append("s9", ev)
    assert doc["_id"] == "s9:3"
    assert doc["type"] == "tool/call"
    loaded = await repo.list_events("s9")
    assert len(loaded) == 1
    assert loaded[0] == ev


async def test_repo_autofill_and_update_touch(store):
    repo = SessionRepo(store)
    doc = await repo.create({"user_id": "u1", "assistant_id": "asst-research", "title": "t"})
    assert len(doc["_id"]) == 12
    assert doc["created_at"] > 0 and doc["updated_at"] >= doc["created_at"]

    upd = await repo.update(doc["_id"], {"title": "t2"})
    assert upd["title"] == "t2" and upd["user_id"] == "u1"   # 合并不丢字段
    assert upd["updated_at"] >= doc["updated_at"]

    rows = await repo.list(filters={"user_id": "u1"})
    assert [r["_id"] for r in rows] == [doc["_id"]]


async def test_run_repo_thin_wrapper(store):
    repo = RunRepo(store)
    doc = await repo.create({"session_id": "s1", "user_id": "u1", "status": "running"})
    upd = await repo.update(doc["_id"], {"status": "completed"})
    assert upd["status"] == "completed"
    assert await repo.delete(doc["_id"]) is True


async def test_seed_assistants_idempotent(store):
    await seed_assistants(store)
    await seed_assistants(store)
    all_docs = await store.list("assistants")
    assert len(all_docs) == 2

    research = await store.get("assistants", "asst-research")
    assert research["builtin"] is True
    assert research["system_prompt"].startswith("你是 SynlysAgent 科研助手")
    assert sorted(research["tool_whitelist"]) == [
        "file.list", "file.read", "file.write", "http.request", "knowledge.search", "python.run",
    ]

    data = await store.get("assistants", "asst-data")
    assert data["builtin"] is True
    assert data["system_prompt"].startswith("你是数据分析助手")
    assert "python.run" in data["tool_whitelist"]
    assert "http.request" not in data["tool_whitelist"]

    # 种子受 builtin 保护不可删
    repo = AssistantRepo(store)
    with pytest.raises(ValueError, match="内置助手不可删除"):
        await repo.delete("asst-data")
