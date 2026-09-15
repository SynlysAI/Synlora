"""AI4MS 共享 HMAC token 解析 + sqlite 本地用户模式。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

from app.core.settings import Settings


def _secret(settings: Settings) -> bytes:
    """HMAC secret：优先 AUTH_SECRET，否则按门户规则派生。"""
    if settings.auth_secret:
        return settings.auth_secret.encode()
    return hashlib.sha256(f"{Path.cwd()}_ai4ms_portal".encode()).hexdigest().encode()


def parse_token(token: str, settings: Settings) -> dict[str, Any] | None:
    """校验签名与过期，返回 payload（失败返回 None）。

    Args:
        token: `{payload_b64}.{hmac_hex}` 格式令牌。
        settings: 应用配置。
    """
    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None  # payload 为数组/字符串/数字等非对象时拒绝（防 .get 抛 AttributeError）
    if payload.get("role") not in ("admin", "user"):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def issue_token(payload: dict[str, Any], settings: Settings) -> str:
    """签发 token（本地登录用；payload 自动补 iat/exp，exp=7天）。"""
    body = {**payload, "iat": int(time.time()), "exp": int(time.time()) + 7 * 86400}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(body, ensure_ascii=False).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def mint_ai4ms_token(user_id: str, username: str, role: str,
                     settings: Settings, ttl_hours: int = 1) -> str:
    """为某 AI⁴MS 账号代签一个短效 token（供调用 AI⁴MS 子平台用）。

    与 issue_token 的算法一致（`{payload_b64}.{hmac_hex}`）；差别只在于 sub 用
    AI⁴MS 的 `user_id` 字段、且有效期短（默认 1 小时）——子平台会验签并回查
    ai4ms 用户库，因此要求本方 AUTH_SECRET 与目标子平台一致。

    Args:
        user_id: AI⁴MS 用户库里的 `user_id`（形如 u_xxx）。
        username: 用户名。
        role: AI⁴MS 侧角色（admin|user）。
        settings: 应用配置（取 AUTH_SECRET）。
        ttl_hours: 有效期小时数。

    Returns:
        `{payload_b64}.{hmac_hex}` 形式的 token。
    """
    now = int(time.time())
    body = {
        "sub": user_id, "username": username, "role": role,
        "iat": now, "exp": now + max(1, int(ttl_hours)) * 3600,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(body, ensure_ascii=False).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_secret(settings), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def hash_password(password: str, salt_hex: str | None = None) -> str:
    """PBKDF2-SHA256 密码哈希（AI4MS 兼容格式 pbkdf2_sha256$260000$salt$hash）。

    Args:
        password: 明文密码。
        salt_hex: 盐（16 字节 hex；None 时生成新盐）。

    Returns:
        形如 pbkdf2_sha256$260000$<salt_hex>$<hash_hex> 的字符串。
    """
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000).hex()
    return f"pbkdf2_sha256$260000${salt.hex()}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码（hmac.compare_digest 防时序）。

    Args:
        password: 明文密码。
        stored: hash_password 产出的存储串。

    Returns:
        匹配 True；格式损坏或不匹配 False。
    """
    try:
        _, iterations, salt_hex, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False
