"""将 Synlora 0.7.3 Postgres 数据迁移到 main 1.8.0 文档存储。

工具分三层：读取快照、纯函数映射、幂等写入。敏感字段只进入目标库，
不进入迁移报告或日志。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.crypto import encrypt_key
from app.db.store import create_store

UTC = timezone.utc
SECRET_MARKERS = ("password", "token", "secret", "api_key", "authorization", "cookie")


class MigrationReport(BaseModel):
    """不含敏感值的迁移结果。"""

    counts: dict[str, int] = Field(default_factory=dict)
    checksums: dict[str, str] = Field(default_factory=dict)
    file_count: int = 0
    file_checksum: str = ""
    warnings: list[str] = Field(default_factory=list)
    source_skills: list[str] = Field(default_factory=list)
    dry_run: bool = False


def _timestamp(value: Any) -> float:
    """把 0.7.3 的 naive UTC 时间转换为 Unix 秒。

    Args:
        value: Postgres timestamp、datetime 或空值。

    Returns:
        Unix 秒；空值返回 0。
    """
    if not value:
        return 0.0
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.timestamp()
    return float(value)


def _mapping(value: Any) -> dict[str, Any]:
    """归一 JSON 字段为 dict。"""
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    """归一 JSON 数组字段为 list。"""
    return value if isinstance(value, list) else []


def _public_hash(rows: list[dict[str, Any]], fields: list[str]) -> str:
    """为非敏感字段生成稳定 SHA-256，用于迁移对账。

    Args:
        rows: 源数据行。
        fields: 参与哈希的字段；调用方不得传入密钥或密码字段。

    Returns:
        hex digest；空输入返回空串。
    """
    if not rows:
        return ""
    canonical = json.dumps(
        [[str(row.get(field, "")) for field in fields] for row in rows],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def map_user(row: dict[str, Any]) -> dict[str, Any]:
    """映射 0.7.3 用户到 main 登录用户文档。

    Args:
        row: 0.7.3 users 行。

    Returns:
        main 用户文档；`_id` 与登录名均使用原 uid。
    """
    role = "admin" if str(row.get("role") or "").lower() in {"admin", "superadmin"} else "user"
    return {
        "_id": str(row["uid"]),
        "username": str(row["uid"]),
        "password_hash": str(row.get("password_hash") or ""),
        "role": role,
        "status": "active",
        "user_id": str(row.get("uid") or ""),
        "display_name": str(row.get("username") or ""),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("last_login") or row.get("created_at")),
        "legacy": {
            "source_id": row.get("id"),
            "display_name": row.get("username"),
            "phone_number": row.get("phone_number"),
            "avatar": row.get("avatar"),
            "department_id": row.get("department_id"),
            "source_role": row.get("role"),
        },
    }


def map_api_key(row: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """映射 0.7.3 API Key，保留禁用、撤销与过期状态。"""
    return {
        "_id": f"legacy-api-key-{row['id']}",
        "key_hash": str(row.get("key_hash") or ""),
        "key_prefix": str(row.get("key_prefix") or ""),
        "name": str(row.get("name") or "Migrated key"),
        "user_id": str(user["_id"]),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or "user"),
        "is_enabled": bool(row.get("is_enabled", False)),
        "expires_at": _timestamp(row.get("expires_at")) or None,
        "revoked_at": _timestamp(row.get("revoked_at")) or None,
        "last_used_at": _timestamp(row.get("last_used_at")) or None,
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("last_used_at") or row.get("created_at")),
        "migrated": True,
        "legacy": {
            "source_id": row.get("id"),
            "department_id": row.get("department_id"),
            "request_id": row.get("request_id"),
        },
    }


def map_agent(row: dict[str, Any]) -> dict[str, Any]:
    """映射 0.7.3 顶层 agent 到 main assistant。"""
    context = _mapping(_mapping(row.get("config_json")).get("context"))
    return {
        "_id": str(row["slug"]),
        "name": str(row.get("name") or row["slug"]),
        "avatar": row.get("icon") or None,
        "description": str(row.get("description") or ""),
        "system_prompt": str(context.get("system_prompt") or row.get("description") or ""),
        "model_provider_id": None,
        "tool_whitelist": _sequence(context.get("tools")),
        "skill_refs": _sequence(context.get("skills")),
        "mcp_refs": [],
        "suggested_prompts": [],
        "knowledge_base_ids": [],
        "builtin": False,
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
        "legacy": {
            "backend_id": row.get("backend_id"),
            "is_default": bool(row.get("is_default", False)),
        },
    }


def map_legacy_agent(row: dict[str, Any]) -> dict[str, Any]:
    """保留 0.7.3 子智能体元数据，避免丢失但不得覆盖 main 内置能力。"""
    return {
        "_id": f"legacy-agent-{row['slug']}",
        "slug": str(row.get("slug") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "backend_id": str(row.get("backend_id") or ""),
        "config": _mapping(row.get("config_json")),
        "share_config": _mapping(row.get("share_config")),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }


def split_provider(row: dict[str, Any]) -> list[dict[str, Any]]:
    """把启用的 0.7.3 chat provider 模型拆成 main provider 文档。"""
    if not bool(row.get("is_enabled", False)):
        return []
    result: list[dict[str, Any]] = []
    for model in _sequence(row.get("enabled_models")):
        if not isinstance(model, dict) or model.get("type") != "chat":
            continue
        if model.get("enabled") is False:
            continue
        model_id = str(model.get("id") or "")
        if not model_id:
            continue
        result.append({
            "_id": f"{row['provider_id']}:{model_id}",
            "name": f"{row.get('display_name') or row['provider_id']} / {model.get('display_name') or model_id}",
            "base_url": str(model.get("base_url_override") or row.get("base_url") or ""),
            "api_key": str(row.get("api_key") or ""),
            "model_id": model_id,
            "enabled": True,
            "multimodal": False,
            "legacy": {"provider_id": row.get("provider_id")},
        })
    return result


def map_project(row: dict[str, Any]) -> dict[str, Any]:
    """映射 0.7.3 项目。"""
    directory = Path(str(row.get("workdir_path") or "")).name or str(row["id"])
    return {
        "_id": str(row["id"]),
        "user_id": str(row["uid"]),
        "name": str(row.get("name") or "未命名项目"),
        "dir_name": directory,
        "archived": str(row.get("status")) != "active" or row.get("deleted_at") is not None,
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
        "legacy": {
            "workdir_path": row.get("workdir_path"),
            "selection_status": row.get("selection_status"),
            "directory_mode": row.get("directory_mode"),
        },
    }


def map_session(row: dict[str, Any], *, message_count: int) -> dict[str, Any]:
    """映射 0.7.3 conversation 到 main session。"""
    return {
        "_id": str(row["thread_id"]),
        "user_id": str(row["uid"]),
        "assistant_id": str(row.get("agent_id") or ""),
        "title": str(row.get("title") or "未命名会话"),
        "archived": str(row.get("status")) == "archived",
        "message_count": message_count,
        "model_provider_id": None,
        "project_id": str(row.get("project_id") or "") or None,
        "enabled_plugins": None,
        "enabled_mcp": None,
        "research_context": None,
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
        "legacy": {
            "source_id": row.get("id"),
            "status": row.get("status"),
            "is_pinned": bool(row.get("is_pinned", False)),
            "metadata": _mapping(row.get("extra_metadata")),
        },
    }


def build_message_events(session_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 human/ai 消息投影为 main durable event 流。

    Args:
        session_id: main 会话 id。
        messages: 0.7.3 messages 行。

    Returns:
        按 seq 排序的事件文档；非 human/ai 历史消息跳过。
    """
    events: list[dict[str, Any]] = []
    seq = 0
    turn_open = False
    last_timestamp = 0.0
    for row in sorted(messages, key=lambda item: (int(item.get("sequence") or 0), int(item.get("id") or 0))):
        role = str(row.get("role") or "").lower()
        timestamp = _timestamp(row.get("created_at"))
        if role == "user":
            if turn_open:
                events.append({
                    "session_id": session_id, "seq": seq, "type": "turn/end",
                    "payload": {}, "ts": last_timestamp,
                })
                seq += 1
                turn_open = False
            events.append({
                "session_id": session_id, "seq": seq, "type": "turn/start",
                "payload": {}, "ts": timestamp,
            })
            seq += 1
            events.append({
                "session_id": session_id, "seq": seq, "type": "user/message",
                "payload": {"text": str(row.get("content") or "")}, "ts": timestamp,
            })
            seq += 1
            turn_open = True
        elif role == "assistant":
            events.append({
                "session_id": session_id, "seq": seq, "type": "assistant/message",
                "payload": {"content": str(row.get("content") or "")}, "ts": timestamp,
            })
            seq += 1
            turn_open = True
        elif role == "tool":
            metadata = _mapping(row.get("extra_metadata"))
            output = _mapping(metadata.get("output"))
            events.append({
                "session_id": session_id, "seq": seq, "type": "tool/result",
                "payload": {
                    "name": str(metadata.get("tool_name") or "legacy_tool"),
                    "tool_call_id": str(metadata.get("tool_call_id") or row.get("run_id") or row.get("id") or ""),
                    "content": str(output.get("content") or row.get("content") or ""),
                    "ok": str(output.get("status") or "success") != "error",
                },
                "ts": timestamp,
            })
            seq += 1
            turn_open = True
        last_timestamp = timestamp
    if turn_open:
        events.append({
            "session_id": session_id, "seq": seq, "type": "turn/end",
            "payload": {}, "ts": last_timestamp,
        })
    return events


def map_run(row: dict[str, Any]) -> dict[str, Any]:
    """映射历史 run；非终态一律收敛为 aborted。"""
    source_status = str(row.get("status") or "").lower()
    if source_status == "completed":
        status = "completed"
    elif source_status in {"cancelled", "canceled", "aborted"}:
        status = "aborted"
    else:
        status = "failed" if source_status in {"failed", "error"} else "aborted"
    return {
        "_id": str(row["id"]),
        "session_id": str(row["thread_id"]),
        "user_id": str(row["uid"]),
        "status": status,
        "started_at": _timestamp(row.get("started_at") or row.get("created_at")),
        "ended_at": _timestamp(row.get("finished_at") or row.get("updated_at")),
        "legacy": {
            "agent_slug": row.get("agent_slug"),
            "source_status": row.get("status"),
            "request_id": row.get("request_id"),
        },
    }


def map_operation_log(row: dict[str, Any], users_by_source_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """映射 0.7.3 操作日志为 legacy 审计文档。"""
    user = users_by_source_id.get(int(row.get("user_id") or -1), {})
    return {
        "_id": f"legacy-operation-{row['id']}",
        "user_id": str(user.get("_id") or ""),
        "operation": str(row.get("operation") or ""),
        "details": str(row.get("details") or ""),
        "ip_address": str(row.get("ip_address") or ""),
        "created_at": _timestamp(row.get("timestamp")),
    }


def _redact_mapping(value: Any) -> dict[str, Any]:
    """保留 MCP 配置结构但遮蔽敏感值。"""
    safe: dict[str, Any] = {}
    for key, item in (_mapping(value)).items():
        key_text = str(key).lower()
        safe[str(key)] = "[REDACTED]" if any(marker in key_text for marker in SECRET_MARKERS) else item
    return safe


def map_mcp_server(row: dict[str, Any]) -> dict[str, Any]:
    """映射 MCP 配置；迁移后保持 disabled，待管理员确认后另行启用。"""
    return {
        "_id": f"legacy-mcp-{row['id']}",
        "slug": str(row.get("slug") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "transport": str(row.get("transport") or ""),
        "url": str(row.get("url") or ""),
        "command": str(row.get("command") or ""),
        "args": _sequence(row.get("args")),
        "env": _redact_mapping(row.get("env")),
        "headers": _redact_mapping(row.get("headers")),
        "timeout_s": float(row.get("timeout") or 60),
        "enabled": False,
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }


async def _upsert(store: Any, collection: str, doc: dict[str, Any]) -> None:
    """按 `_id` 幂等写入文档存储。"""
    doc_id = str(doc["_id"])
    existing = await store.get(collection, doc_id)
    if existing is None:
        await store.insert(collection, doc)
        return
    fields = {key: value for key, value in doc.items() if key != "_id"}
    await store.update(collection, doc_id, fields)


async def _encrypt_provider(store: Any, provider: dict[str, Any], fernet_key: str) -> dict[str, Any]:
    """加密 provider API key 后返回可写入文档。"""
    body = dict(provider)
    api_key = body.pop("api_key", "")
    if api_key:
        enc, encrypted = encrypt_key(api_key, fernet_key)
    else:
        enc, encrypted = "", False
    body["api_key_enc"] = enc
    body["api_key_encrypted"] = encrypted
    return body


def _directory_hash(root: Path) -> tuple[int, str]:
    """计算目录内全部文件的 count 与内容 hash。"""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        count += 1
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return count, digest.hexdigest()


async def _rebuild_events(store: Any, session_id: str, events: list[dict[str, Any]]) -> int:
    """删除并重建一个迁移会话的事件，保证重复执行不重复。"""
    for old in await store.list("events", filters={"session_id": session_id}):
        await store.delete("events", str(old["_id"]))
    for event in events:
        await store.insert("events", {
            "_id": f"{session_id}:{event['seq']}",
            **{key: value for key, value in event.items() if key != "seq"},
            "seq": event["seq"],
        })
    return len(events)


async def migrate_snapshot(
    snapshot: dict[str, list[dict[str, Any]]],
    store: Any,
    source_data_root: str | Path,
    target_data_root: str | Path,
    *,
    dry_run: bool = False,
    target_backend: str = "sqlite",
    fernet_key: str = "",
) -> MigrationReport:
    """把源快照映射并幂等写入 main。

    Args:
        snapshot: 0.7.3 表行集合。
        store: 已初始化 DocumentStore。
        source_data_root: 0.7.3 数据根。
        target_data_root: main 数据根。
        dry_run: 只生成报告不写库、不复制文件。
        target_backend: sqlite 或 mongodb；mongodb 用户由 CLI 另写 ai4ms.users。
        fernet_key: provider key 加密密钥。

    Returns:
        不含敏感值的迁移报告。
    """
    counts: dict[str, int] = defaultdict(int)
    warnings: list[str] = []
    users = [map_user(row) for row in snapshot.get("users", [])]
    users_by_source_id = {
        int(row.get("id") or -1): mapped
        for row, mapped in zip(snapshot.get("users", []), users, strict=False)
    }
    api_keys: list[dict[str, Any]] = []
    skipped_deleted_user_keys = 0
    for row in snapshot.get("api_keys", []):
        owner = users_by_source_id.get(int(row.get("user_id") or -1))
        if owner is None:
            skipped_deleted_user_keys += 1
            continue
        api_keys.append(map_api_key(row, owner))
    top_agents = [row for row in snapshot.get("agents", []) if not row.get("is_subagent")]
    subagents = [row for row in snapshot.get("agents", []) if row.get("is_subagent")]
    assistants = [map_agent(row) for row in top_agents]
    legacy_agents = [map_legacy_agent(row) for row in subagents]
    providers = [provider for row in snapshot.get("providers", []) for provider in split_provider(row)]
    projects = [map_project(row) for row in snapshot.get("projects", []) if row.get("deleted_at") is None]
    conversations = [row for row in snapshot.get("conversations", []) if str(row.get("status")) != "deleted"]

    messages_by_conversation: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot.get("messages", []):
        messages_by_conversation[int(row.get("conversation_id") or -1)].append(row)
    if len(conversations) == 1 and "id" not in conversations[0] and messages_by_conversation:
        conversations[0]["id"] = next(iter(messages_by_conversation))

    def thread_for(row: dict[str, Any]) -> str:
        """按 source id 或唯一会话兜底取得 thread id。"""
        source_id = int(row.get("id") or -1)
        if source_id in messages_by_conversation:
            return str(row["thread_id"])
        return str(row["thread_id"])

    sessions: list[dict[str, Any]] = []
    event_groups: list[tuple[str, list[dict[str, Any]]]] = []
    for row in conversations:
        source_id = int(row.get("id") or -1)
        messages = messages_by_conversation.get(source_id, [])
        sessions.append(map_session(row, message_count=len(messages)))
        event_groups.append((thread_for(row), build_message_events(str(row["thread_id"]), messages)))
    runs = [map_run(row) for row in snapshot.get("runs", [])]
    logs = [map_operation_log(row, users_by_source_id) for row in snapshot.get("operation_logs", [])]
    mcps = [map_mcp_server(row) for row in snapshot.get("mcp_servers", [])]

    file_count = 0
    file_checksum = ""
    if not dry_run:
        for user in users:
            if target_backend == "sqlite":
                await _upsert(store, "local_users", user)
            else:
                await _upsert(store, "legacy_users", user)
        for doc in api_keys:
            await _upsert(store, "api_keys", doc)
        for doc in assistants:
            await _upsert(store, "assistants", doc)
        for doc in legacy_agents:
            await _upsert(store, "legacy_agents", doc)
        for provider in providers:
            await _upsert(store, "providers", await _encrypt_provider(store, provider, fernet_key))
        for doc in projects:
            await _upsert(store, "projects", doc)
        for doc in sessions:
            await _upsert(store, "sessions", doc)
        for session_id, events in event_groups:
            counts["events"] += await _rebuild_events(store, session_id, events)
        for doc in runs:
            await _upsert(store, "runs", doc)
        for doc in logs:
            await _upsert(store, "legacy_operation_logs", doc)
        for doc in mcps:
            await _upsert(store, "legacy_mcp_servers", doc)

        source_root = Path(source_data_root)
        target_root = Path(target_data_root)
        for project, source_row in zip(projects, snapshot.get("projects", []), strict=False):
            if source_row.get("deleted_at") is not None:
                continue
            source_dir = source_root / "shared" / str(source_row["uid"]) / "workspace" / str(source_row["workdir_path"])
            target_dir = target_root / "users" / str(source_row["uid"]) / "projects" / project["dir_name"]
            if not source_dir.is_dir():
                warnings.append(f"项目文件目录缺失: {project['_id']}")
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

        user_project_root = target_root / "users"
        if user_project_root.is_dir():
            file_count, file_checksum = _directory_hash(user_project_root)

    counts.update({
        "users": len(users),
        "api_keys": len(api_keys),
        "agents": len(snapshot.get("agents", [])),
        "assistants": len(assistants),
        "legacy_agents": len(legacy_agents),
        "subagents_preserved": len(subagents),
        "providers": len(providers),
        "skipped_disabled_providers": sum(1 for row in snapshot.get("providers", []) if not row.get("is_enabled")),
        "source_skills": len(snapshot.get("skills", [])),
        "projects": len(projects),
        "sessions": len(sessions),
        "messages": len(snapshot.get("messages", [])),
        "messages_preserved": sum(1 for row in snapshot.get("messages", []) if str(row.get("role")) in {"user", "assistant", "tool"}),
        "skipped_deleted_user_api_keys": skipped_deleted_user_keys,
        "runs": len(runs),
        "operation_logs": len(logs),
        "mcp_servers": len(mcps),
    })
    checksums = {
        "users": _public_hash(users, ["_id", "role", "created_at"]),
        "projects": _public_hash(projects, ["_id", "user_id", "dir_name"]),
        "sessions": _public_hash(sessions, ["_id", "user_id", "assistant_id"]),
    }
    report = MigrationReport(
        counts=dict(counts), checksums=checksums,
        file_count=file_count, file_checksum=file_checksum,
        warnings=warnings,
        source_skills=[str(row.get("slug") or "") for row in snapshot.get("skills", [])],
        dry_run=dry_run,
    )
    return report


def load_snapshot(dsn: str, since: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """从 0.7.3 Postgres 读取迁移快照。"""
    import psycopg
    from psycopg.rows import dict_row

    since_sql = "%s::timestamptz" if since else "NULL::timestamptz"
    queries = {
        "users": "SELECT * FROM users WHERE is_deleted=0",
        "api_keys": "SELECT ak.*, u.uid AS user_uid FROM api_keys ak JOIN users u ON u.id=ak.user_id",
        "agents": f"SELECT * FROM agents WHERE ({since_sql} IS NULL OR updated_at >= {since_sql})",
        "providers": "SELECT * FROM model_providers",
        "skills": "SELECT slug, name, version, enabled FROM skills",
        "projects": f"SELECT * FROM projects WHERE deleted_at IS NULL AND ({since_sql} IS NULL OR updated_at >= {since_sql})",
        "conversations": f"SELECT * FROM conversations WHERE status <> 'deleted' AND ({since_sql} IS NULL OR updated_at >= {since_sql})",
        "messages": """
            SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE c.status <> 'deleted' AND (%s::timestamptz IS NULL OR m.created_at >= %s::timestamptz)
        """,
        "runs": """
            SELECT r.* FROM agent_runs r JOIN conversations c ON c.id=r.conversation_id
            WHERE c.status <> 'deleted' AND (%s::timestamptz IS NULL OR r.updated_at >= %s::timestamptz)
        """,
        "operation_logs": f"SELECT * FROM operation_logs WHERE ({since_sql} IS NULL OR timestamp >= {since_sql})",
        "mcp_servers": f"SELECT * FROM mcp_servers WHERE ({since_sql} IS NULL OR updated_at >= {since_sql})",
    }
    snapshot: dict[str, list[dict[str, Any]]] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        for name, query in queries.items():
            if name in {"messages", "runs"}:
                params = (since, since)
            elif name in {"users", "api_keys", "providers", "skills"}:
                params = ()
            else:
                params = (since, since) if since is not None else ()
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                snapshot[name] = [dict(row) for row in cursor.fetchall()]
    # delta 消息可能属于未因自身 updated_at 变化的 conversation；补齐这些会话。
    message_conversation_ids = {int(row["conversation_id"]) for row in snapshot.get("messages", [])}
    if message_conversation_ids and since is not None:
        existing_ids = {int(row.get("id") or -1) for row in snapshot.get("conversations", [])}
        missing = message_conversation_ids - existing_ids
        if missing:
            with psycopg.connect(dsn, row_factory=dict_row) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM conversations WHERE id = ANY(%s)",
                    (sorted(missing),),
                )
                snapshot["conversations"].extend(dict(row) for row in cursor.fetchall())
    return snapshot


async def _import_mongo_users(uri: str, users: list[dict[str, Any]]) -> None:
    """把用户写入 main Mongo 认证库 ai4ms.users。"""
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(uri)
    try:
        collection = client["ai4ms"]["users"]
        for user in users:
            await collection.replace_one({"_id": user["_id"]}, user, upsert=True)
    finally:
        client.close()


async def run(args: argparse.Namespace) -> MigrationReport:
    """CLI 主流程。"""
    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since else None
    snapshot = load_snapshot(args.source_dsn, since)
    users = [map_user(row) for row in snapshot.get("users", [])]
    if args.dry_run:
        return MigrationReport(
            counts={
                "users": len(snapshot.get("users", [])),
                "api_keys": len(snapshot.get("api_keys", [])),
                "agents": len(snapshot.get("agents", [])),
                "projects": len(snapshot.get("projects", [])),
                "conversations": len(snapshot.get("conversations", [])),
                "messages": len(snapshot.get("messages", [])),
                "runs": len(snapshot.get("runs", [])),
            },
            dry_run=True,
        )
    store = create_store(
        args.target_backend,
        sqlite_path=args.sqlite_path,
        mongodb_uri=args.mongodb_uri,
        mongodb_db=args.mongodb_db,
    )
    await store.init()
    try:
        report = await migrate_snapshot(
            snapshot, store, args.source_data_root, args.target_data_root,
            dry_run=False, target_backend=args.target_backend, fernet_key=args.fernet_key,
        )
        if args.target_backend == "mongodb":
            await _import_mongo_users(args.mongodb_uri, users)
        return report
    finally:
        await store.close()


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", required=True, help="0.7.3 Postgres DSN")
    parser.add_argument("--source-data-root", required=True, help="0.7.3 user-data 根目录")
    parser.add_argument("--target-data-root", required=True, help="main data_root")
    parser.add_argument("--target-backend", choices=["sqlite", "mongodb"], default="mongodb")
    parser.add_argument("--sqlite-path", default="")
    parser.add_argument("--mongodb-uri", default="")
    parser.add_argument("--mongodb-db", default="synlys_agent")
    parser.add_argument("--fernet-key", default="")
    parser.add_argument("--since", default="", help="ISO-8601 UTC；省略为全量")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="", help="报告 JSON 输出路径")
    return parser


def main() -> None:
    """命令行入口。"""
    args = build_parser().parse_args()
    report = asyncio.run(run(args))
    text = report.model_dump_json(indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
