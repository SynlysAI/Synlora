"""Synlora 0.7.3 -> main 1.8.0 迁移工具测试。"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db.store import create_store
from scripts.migrate_from_0_7_3 import (
    build_message_events,
    map_agent,
    map_api_key,
    map_project,
    map_run,
    map_session,
    map_user,
    migrate_snapshot,
    split_provider,
    load_snapshot,
)

UTC = timezone.utc


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def user_row(**overrides):
    row = {
        "id": 1,
        "uid": "fangyikai",
        "username": "方亦凯",
        "password_hash": "$argon2id$legacy",
        "role": "superadmin",
        "status": None,
        "created_at": dt("2026-06-25T01:22:40"),
        "is_deleted": 0,
        "department_id": 3,
    }
    row.update(overrides)
    return row


def test_map_user_preserves_login_uid_and_argon2_hash():
    mapped = map_user(user_row())
    assert mapped["_id"] == "fangyikai"
    assert mapped["username"] == "fangyikai"
    assert mapped["display_name"] == "方亦凯"
    assert mapped["role"] == "admin"
    assert mapped["password_hash"] == "$argon2id$legacy"
    assert mapped["legacy"]["source_id"] == 1


def test_map_api_key_keaps_disabled_and_revoked_state():
    mapped = map_api_key({
        "id": 9,
        "key_hash": "a" * 64,
        "key_prefix": "yxkey_demo",
        "name": "Plane",
        "user_id": 1,
        "expires_at": dt("2027-01-01T00:00:00"),
        "is_enabled": False,
        "revoked_at": dt("2026-11-01T00:00:00"),
        "last_used_at": None,
        "created_at": dt("2026-10-16T09:00:00"),
    }, {"_id": "fangyikai", "username": "fangyikai", "role": "admin"})
    assert mapped["_id"] == "legacy-api-key-9"
    assert mapped["user_id"] == "fangyikai"
    assert mapped["is_enabled"] is False
    assert mapped["revoked_at"] is not None
    assert "raw_key" not in mapped


def test_map_agent_and_split_provider():
    agent = map_agent({
        "slug": "academic-research",
        "name": "学术研究助手",
        "description": "研究",
        "icon": "academic.png",
        "config_json": {"context": {
            "system_prompt": "You are researcher",
            "tools": ["web_search"],
            "skills": ["deep-research"],
        }},
        "is_subagent": False,
        "backend_id": "ChatbotAgent",
        "created_at": dt("2026-10-01T00:00:00"),
        "updated_at": dt("2026-10-02T00:00:00"),
    })
    assert agent["_id"] == "academic-research"
    assert agent["system_prompt"] == "You are researcher"
    assert agent["tool_whitelist"] == ["web_search"]
    assert agent["skill_refs"] == ["deep-research"]

    providers = split_provider({
        "provider_id": "HW-LOCAL",
        "display_name": "HW-LOCAL",
        "base_url": "http://local/v1",
        "api_key": "",
        "is_enabled": True,
        "enabled_models": [
            {"id": "deepseek", "display_name": "DeepSeek", "enabled": True, "type": "chat"},
            {"id": "embedding", "display_name": "Embedding", "enabled": True, "type": "embedding"},
        ],
    })
    assert [p["model_id"] for p in providers] == ["deepseek"]
    assert providers[0]["name"] == "HW-LOCAL / DeepSeek"


def test_map_project_session_events_and_run():
    project = map_project({
        "id": "project-1",
        "uid": "fangyikai",
        "name": None,
        "workdir_path": "projects/2026-10-21_project1",
        "status": "active",
        "deleted_at": None,
        "created_at": dt("2026-10-21T00:00:00"),
        "updated_at": dt("2026-10-21T01:00:00"),
    })
    assert project["_id"] == "project-1"
    assert project["dir_name"] == "2026-10-21_project1"

    session = map_session({
        "thread_id": "thread-1",
        "uid": "fangyikai",
        "agent_id": "academic-research",
        "title": "历史会话",
        "status": "active",
        "project_id": "project-1",
        "created_at": dt("2026-10-21T00:00:00"),
        "updated_at": dt("2026-10-21T00:01:00"),
    }, message_count=2)
    assert session["_id"] == "thread-1"
    assert session["assistant_id"] == "academic-research"

    events = build_message_events("thread-1", [
        {"id": 1, "role": "user", "content": "你好", "sequence": 1, "created_at": dt("2026-10-21T00:00:01")},
        {"id": 2, "role": "assistant", "content": "你好，我是 Synlora。", "sequence": 2, "created_at": dt("2026-10-21T00:00:02")},
        {"id": 3, "role": "tool", "content": "skipped", "sequence": 3, "created_at": dt("2026-10-21T00:00:03")},
    ])
    types = [event["type"] for event in events]
    assert types == ["turn/start", "user/message", "assistant/message", "tool/result", "turn/end"]
    assert events[1]["payload"] == {"text": "你好"}
    assert events[2]["payload"]["content"] == "你好，我是 Synlora。"
    assert [event["seq"] for event in events] == [0, 1, 2, 3, 4]
    assert events[3]["payload"]["name"] == "legacy_tool"

    run = map_run({
        "id": "run-1",
        "thread_id": "thread-1",
        "uid": "fangyikai",
        "status": "completed",
        "created_at": dt("2026-10-21T00:00:00"),
        "finished_at": dt("2026-10-21T00:00:03"),
    })
    assert run["_id"] == "run-1"
    assert run["status"] == "completed"


@pytest.mark.asyncio
async def test_migrate_snapshot_is_idempotent_and_copies_project_files(tmp_path):
    store = create_store("sqlite", sqlite_path=str(tmp_path / "target.db"))
    await store.init()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target-data"
    project_dir = source_root / "shared" / "fangyikai" / "workspace" / "projects" / "project-dir"
    project_dir.mkdir(parents=True)
    (project_dir / "a.txt").write_text("hello", encoding="utf-8")

    snapshot = {
        "users": [user_row()],
        "api_keys": [{
            "id": 2, "key_hash": "b" * 64, "key_prefix": "yxkey_key", "name": "CLI",
            "user_id": 1, "expires_at": None, "is_enabled": True, "revoked_at": None,
            "last_used_at": None, "created_at": dt("2026-10-16T00:00:00"),
        }],
        "agents": [{
            "slug": "default-chatbot", "name": "智能助手", "description": "聊天",
            "icon": "", "config_json": {"context": {}}, "is_subagent": False,
            "backend_id": "ChatbotAgent", "created_at": dt("2026-10-01T00:00:00"),
            "updated_at": dt("2026-10-01T00:00:00"),
        }],
        "providers": [],
        "projects": [{
            "id": "p1", "uid": "fangyikai", "name": "项目", "workdir_path": "projects/project-dir",
            "status": "active", "deleted_at": None, "created_at": dt("2026-10-21T00:00:00"),
            "updated_at": dt("2026-10-21T00:00:00"),
        }],
        "conversations": [{
            "thread_id": "t1", "uid": "fangyikai", "agent_id": "default-chatbot", "title": "会话",
            "status": "active", "project_id": "p1", "created_at": dt("2026-10-21T00:00:00"),
            "updated_at": dt("2026-10-21T00:00:00"),
        }],
        "messages": [{
            "id": 1, "conversation_id": 1, "role": "user", "content": "hello",
            "sequence": 1, "created_at": dt("2026-10-21T00:00:01"),
        }],
        "runs": [],
        "operation_logs": [],
        "mcp_servers": [],
    }
    first = await migrate_snapshot(snapshot, store, str(source_root), str(target_root), dry_run=False)
    second = await migrate_snapshot(snapshot, store, str(source_root), str(target_root), dry_run=False)
    assert first.counts["users"] == second.counts["users"] == 1
    assert first.counts["api_keys"] == 1
    assert first.counts["sessions"] == 1
    assert first.counts["events"] == 3
    assert second.counts["events"] == first.counts["events"]
    assert len(await store.list("api_keys")) == 1
    assert len(await store.list("sessions")) == 1
    copied = target_root / "users" / "fangyikai" / "projects" / "project-dir" / "a.txt"
    assert copied.read_text(encoding="utf-8") == "hello"
    report_text = first.model_dump_json()
    assert "argon2" not in report_text and "key_hash" not in report_text
    await store.close()


@pytest.mark.asyncio
async def test_migrate_snapshot_skips_api_keys_for_deleted_users(tmp_path):
    """已删除用户的 key 不导入，避免失效主体重新获得访问权限。"""
    store = create_store("sqlite", sqlite_path=str(tmp_path / "target.db"))
    await store.init()
    snapshot = {
        "users": [],
        "api_keys": [{
            "id": 1, "key_hash": "c" * 64, "key_prefix": "yxkey_old", "name": "Deleted owner",
            "user_id": 99, "expires_at": None, "is_enabled": True, "revoked_at": None,
            "last_used_at": None, "created_at": dt("2026-10-16T00:00:00"),
        }],
        "agents": [], "providers": [], "projects": [], "conversations": [], "messages": [],
        "runs": [], "operation_logs": [], "mcp_servers": [], "skills": [],
    }
    report = await migrate_snapshot(snapshot, store, tmp_path, tmp_path / "data", dry_run=False)
    assert report.counts["api_keys"] == 0
    assert report.counts["skipped_deleted_user_api_keys"] == 1
    assert await store.list("api_keys") == []
    await store.close()


def test_load_snapshot_full_mode_matches_placeholders_and_params(monkeypatch):
    """全量模式不向无占位符 SQL 传参数，避免 psycopg 运行期失败。"""
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=()):
            executed.append((query, tuple(params)))

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr("psycopg.connect", lambda *_args, **_kwargs: Connection())
    load_snapshot("dsn", None)
    assert executed
    for query, params in executed:
        assert query.count("%s") == len(params)


def test_load_snapshot_delta_sql_uses_explicit_timestamp_cast(monkeypatch):
    """delta 参数显式 cast，PostgreSQL 不需要额外推断类型。"""
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=()):
            queries.append(query)

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr("psycopg.connect", lambda *_args, **_kwargs: Connection())
    load_snapshot("dsn", dt("2026-11-02T00:00:00"))
    assert queries
    assert all("::timestamptz" in query for query in queries if "%s" in query)
