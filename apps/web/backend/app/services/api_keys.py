"""个人 yxkey 的生成、校验与公开投影。"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any


def generate_api_key() -> tuple[str, str, str]:
    """生成一把 yxkey 个人访问令牌。

    Returns:
        ``(明文 key, SHA-256 hash, key 前缀)`` 三元组；明文只允许出现在创建响应中。
    """
    raw = f"yxkey_{secrets.token_hex(24)}"
    return raw, hashlib.sha256(raw.encode()).hexdigest(), raw[:12]


def api_key_is_valid(doc: dict[str, Any], now: float | None = None) -> bool:
    """判断访问令牌文档当前是否可用。

    Args:
        doc: 存储层中的令牌文档。
        now: 校验时间戳；默认当前时间，便于测试注入。

    Returns:
        启用、未撤销且未过期返回 True。
    """
    if not doc.get("is_enabled", False) or doc.get("revoked_at") is not None:
        return False
    expires_at = doc.get("expires_at")
    return not expires_at or float(expires_at) > (now if now is not None else time.time())


def public_api_key(doc: dict[str, Any]) -> dict[str, Any]:
    """返回可交给浏览器的令牌字段，绝不包含 hash 或明文。

    Args:
        doc: 存储层中的令牌文档。

    Returns:
        公开投影字典。
    """
    return {
        "id": str(doc["_id"]),
        "name": str(doc.get("name") or ""),
        "key_prefix": str(doc.get("key_prefix") or ""),
        "is_enabled": bool(doc.get("is_enabled", False)),
        "expires_at": doc.get("expires_at"),
        "last_used_at": doc.get("last_used_at"),
        "created_at": doc.get("created_at"),
        "migrated": bool(doc.get("migrated", False)),
    }


async def resolve_api_key(store, raw_key: str) -> dict[str, Any] | None:
    """用请求中的 yxkey 明文解析用户身份并记录最后使用时间。

    Args:
        store: DocumentStore 实例。
        raw_key: 请求方提供的 yxkey 明文。

    Returns:
        有效令牌对应的用户 payload；无效返回 None。
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    docs = await store.list("api_keys", filters={"key_hash": key_hash})
    if not docs:
        return None
    doc = docs[0]
    if not api_key_is_valid(doc):
        return None
    now = time.time()
    await store.update("api_keys", str(doc["_id"]), {"last_used_at": now, "updated_at": now})
    return {
        "sub": str(doc.get("user_id") or ""),
        "username": str(doc.get("username") or ""),
        "role": str(doc.get("role") or "user"),
    }
