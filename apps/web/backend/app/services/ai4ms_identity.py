"""AI⁴MS 身份代签服务：把「当前登录用户」映射成 AI⁴MS 侧身份并代签短效 token。

背景：本平台的主键是 AI⁴MS 用户文档的 `_id`（workspaces/会话/项目/能力安装都按它
存），而 AI⁴MS 子平台（Spec_Agent 等）认的是 `user_id`（形如 u_xxx）——同一个用户
文档里两个不同字段。子平台会验签 + 用 sub 回查自己那份 `ai4ms.users`，所以代签要求
本方 AUTH_SECRET 与目标子平台一致（生产已满足；本地 dev 的 secret 不同 → 解析不到
时调用方回落插件配置里的服务 token）。
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.auth import mint_ai4ms_token
from app.core.settings import Settings

_LOGGER = logging.getLogger(__name__)

# 代签凭证有效期：子平台每次调用都会验签 + 回查用户状态，长效 token 无必要
TOKEN_TTL_HOURS = 1
VALID_ROLES = ("admin", "user")


class Ai4msIdentityService:
    """把「当前登录用户」映射成「AI⁴MS 侧身份」，并按需代签 token。

    解析顺序：
    1. token payload 里的 `ai4ms_user_id`（本方登录接口签发时写入的快路径）；
    2. mongodb 模式下按 username 查 `ai4ms.users` 取 `user_id`（结果按用户名缓存）；
    3. 都没有（如 sqlite 开发模式 / 匿名）→ None（调用方回落配置里的服务 token）。
    """

    def __init__(self, settings: Settings) -> None:
        """保存配置（Mongo 客户端懒建：sqlite 模式全程不碰网络）。

        Args:
            settings: 应用配置（取 AUTH_SECRET 与 MONGODB_URI）。
        """
        self._settings = settings
        self._client: Any = None
        # 用户名 → AI⁴MS user_id 缓存：用户量小且映射基本不变，故不做失效策略
        # （进程内 dict，重启即清；改名/迁移等极端情况退化为一次查不到）
        self._user_id_by_name: dict[str, str] = {}

    def _mongo_users(self) -> Any:
        """懒建并缓存 motor 客户端，返回 `ai4ms.users` 集合（跟随 auth_api 写法）。

        Returns:
            AsyncIOMotorCollection 句柄。
        """
        if self._client is None:
            from motor.motor_asyncio import AsyncIOMotorClient  # 延迟导入：非 prod 无需装 motor
            self._client = AsyncIOMotorClient(self._settings.mongodb_uri)
        return self._client["ai4ms"]["users"]

    async def _resolve_user_id(self, username: str) -> str | None:
        """按用户名查 `ai4ms.users` 取 AI⁴MS `user_id`（带缓存）。

        Args:
            username: 登录用户名。

        Returns:
            `u_xxx` 形式的 user_id；查不到或查询异常（库不可达等）返回 None。
        """
        if username in self._user_id_by_name:
            return self._user_id_by_name[username]
        try:
            doc = await self._mongo_users().find_one({"username": username})
        except Exception:
            # 静默降级：查库失败只告警，绝不打断对话（调用方回落服务 token）
            _LOGGER.warning("查询 ai4ms 用户失败，本轮回落服务凭证: username=%s",
                            username, exc_info=True)
            return None
        user_id = str((doc or {}).get("user_id") or "")
        if not user_id:
            return None
        self._user_id_by_name[username] = user_id
        return user_id

    async def token_for(self, user_payload: dict) -> str | None:
        """解析用户身份并代签 AI⁴MS token（解析不到返回 None）。

        Args:
            user_payload: 本方登录 token 的 payload
                （sub/username/role，mongodb 登录还会带 ai4ms_user_id）。

        Returns:
            `{payload_b64}.{hmac_hex}` 形式的短效 token；无 AI⁴MS 账号（sqlite
            本地用户/匿名）或解析失败时 None。
        """
        username = str(user_payload.get("username") or "")
        user_id = str(user_payload.get("ai4ms_user_id") or "")
        if not user_id:
            if self._settings.storage_backend != "mongodb":
                return None  # sqlite/匿名：本地用户没有 AI⁴MS 账号
            user_id = await self._resolve_user_id(username) or ""
        if not user_id:
            return None
        role = user_payload.get("role")
        try:
            return mint_ai4ms_token(
                user_id, username, role if role in VALID_ROLES else "user",
                self._settings, ttl_hours=TOKEN_TTL_HOURS)
        except Exception:
            _LOGGER.warning("代签 AI⁴MS token 失败: username=%s", username, exc_info=True)
            return None
