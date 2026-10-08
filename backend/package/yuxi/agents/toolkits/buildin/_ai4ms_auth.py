"""AI4MS 平台账号登录换 token（Spec_Agent / Poly_Agent 共用）。

两个服务的登录接口同款（`POST /api/v1/auth/login`，username/password →
access_token，响应包络 {code, message, data}，expires_at 缺省按 12h 兜底），
但签发的 token 互不通用，因此每个服务持有独立的 Ai4msTokenProvider 实例。
账号配置全局共享：AI4MS_USERNAME / AI4MS_PASSWORD。
"""

import asyncio
import os
import time

import httpx

LOGIN_PATH = "/api/v1/auth/login"
TOKEN_REFRESH_MARGIN_S = 300.0  # 到期前 5 分钟即视为过期


class Ai4msTokenProvider:
    """按服务实例缓存的平台账号 token 提供者（到期重登、401 强制重登）。"""

    def __init__(self) -> None:
        self._cache: dict = {"token": "", "expires_at": 0.0}
        self._lock = asyncio.Lock()

    @staticmethod
    def credentials() -> tuple[str, str]:
        """读取全局 AI4MS 账号配置。

        Returns:
            (username, password)；未配置时为 ("", "")。
        """
        return (
            (os.getenv("AI4MS_USERNAME") or "").strip(),
            (os.getenv("AI4MS_PASSWORD") or "").strip(),
        )

    def reset(self) -> None:
        """清空 token 缓存（测试隔离用）。"""
        self._cache["token"] = ""
        self._cache["expires_at"] = 0.0

    def _cached(self) -> str | None:
        token = self._cache["token"]
        if token and self._cache["expires_at"] - TOKEN_REFRESH_MARGIN_S > time.time():
            return token
        return None

    async def ensure_token(self, client: httpx.AsyncClient, base_url: str,
                           username: str, password: str) -> str:
        """取当前有效凭证（缓存命中直接用，否则登录并缓存；双重检查锁）。

        Args:
            client: HTTP 客户端（复用调用方已配置超时/传输的实例）。
            base_url: 服务地址（已去尾斜杠）。
            username: 平台账号。
            password: 平台密码。

        Returns:
            有效的 access_token。

        Raises:
            RuntimeError: 登录失败（message 为可读原因）。
        """
        cached = self._cached()
        if cached:
            return cached
        async with self._lock:
            # 双重检查：等锁期间可能已被并发调用刷新
            cached = self._cached()
            if cached:
                return cached
            token, expires_at = await _login(client, base_url, username, password)
            self._cache["token"] = token
            self._cache["expires_at"] = expires_at
            return token

    async def force_relogin(self, client: httpx.AsyncClient, base_url: str,
                            username: str, password: str) -> str:
        """清空缓存并强制重登（401 后使用）。

        Args:
            client: HTTP 客户端。
            base_url: 服务地址（已去尾斜杠）。
            username: 平台账号。
            password: 平台密码。

        Returns:
            新登录的 access_token。

        Raises:
            RuntimeError: 登录失败（message 为可读原因）。
        """
        async with self._lock:
            token, expires_at = await _login(client, base_url, username, password)
            self._cache["token"] = token
            self._cache["expires_at"] = expires_at
            return token


async def _login(client: httpx.AsyncClient, base_url: str,
                 username: str, password: str) -> tuple[str, float]:
    """登录 AI4MS 平台服务换取访问凭证。

    Args:
        client: HTTP 客户端。
        base_url: 服务地址（已去尾斜杠）。
        username: 平台账号。
        password: 平台密码。

    Returns:
        (access_token, 过期时刻的 Unix 秒)；上游未给 expires_at 时按 12h 兜底。

    Raises:
        RuntimeError: 上游拒绝登录或响应结构异常（message 为可读原因）。
    """
    resp = await client.post(
        f"{base_url}{LOGIN_PATH}",
        json={"username": username, "password": password})
    if resp.status_code != 200:
        detail = ""
        try:
            err_body = resp.json() or {}
            err_data = err_body.get("data")
            detail = str((err_data or {}).get("detail") or err_body.get("message") or "")
        except ValueError:
            pass
        raise RuntimeError(
            f"AI4MS 平台登录失败：{detail or f'上游返回 {resp.status_code} {resp.text[:200]}'}")
    try:
        body = resp.json() or {}
    except ValueError as exc:
        raise RuntimeError(f"AI4MS 平台登录失败：上游返回非 JSON: {resp.text[:200]}") from exc
    if body.get("code") != 0:
        raise RuntimeError(f"AI4MS 平台登录失败：{body.get('message') or body.get('code')}")
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError(f"AI4MS 平台登录失败：上游未返回 access_token: {str(data)[:200]}")
    raw_expires = data.get("expires_at")
    # 仅缺失时兜底 12h；显式的 0/过去时间戳按上游原值（立即过期）处理
    expires_at = float(raw_expires) if raw_expires is not None else (time.time() + 12 * 3600)
    return str(data["access_token"]), expires_at
