"""Plane(OpenEVA) 联邦身份服务：登录代理、身份收敛与委托令牌签发。

Plane 没有面向服务端的 JSON 密码校验接口，``/auth/sign-in/`` 是浏览器 302 流程。
本模块在服务端按同一路径三步走（cookie jar 贯穿），把它当作密码验证预言机：

1. ``GET /auth/get-csrf-token/`` 取 csrf_token（同时种 csrftoken cookie）；
2. ``POST /auth/sign-in/``（form + X-CSRFToken）：302 且无 ``error_code`` 且拿到
   会话 cookie（Plane 定制名 ``session-id``，兼容原生 Django ``sessionid``）
   = 密码正确；
3. ``GET /api/users/me/``（session cookie）取 plane_user_id / display_name。

身份模型沿用本仓库 OIDC 的联邦先例：Plane 身份是真实 users 行（uid=``plane:<pid>``，
首次登录自动建号），不引入虚拟 subject；绑定存在且 core=ai4ms 时收敛到绑定账户。
Plane 侧限流 ``AUTHENTICATION_RATE_LIMIT``（缺省 10 次/分钟/IP）按出口 IP 计数，
所有本方用户的 OpenEVA 登录共享同一配额。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from yuxi.repositories.plane_account_link_repository import PlaneAccountLinkRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.models_business import Department, PlaneAccountLink, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

# 登录链路超时：sign-in 里 Plane 要做高迭代哈希 + 两次跳转，比普通 API 调用慢
_LOGIN_TIMEOUT_SECONDS = 10.0
PLANE_LOGIN_TTL_HOURS = 24
PLANE_DELEGATED_TTL_HOURS = 1
# Plane AUTHENTICATION_ERROR_CODES 中需要精确映射的错误码（其余一律 401 防枚举）
_CODE_RATE_LIMIT = "5900"
_CODE_INSTANCE_NOT_CONFIGURED = "5000"
_CODE_PASSWORD_LOGIN_DISABLED = "5018"
_CODE_ACCOUNT_DEACTIVATED = "5019"
_USERNAME_SAFE_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass(frozen=True)
class PlaneConfig:
    """Plane 集成配置（本切片仅含联邦登录所需项）。"""

    plane_base_url: str = ""
    plane_auth_base_url: str = ""
    plane_service_token: str = ""
    plane_request_timeout_seconds: float = 3.0
    plane_default_department: str = "OpenEVA用户"

    @classmethod
    def from_env(cls) -> PlaneConfig:
        """从环境变量加载配置。"""

        def _env(name: str, default: str = "") -> str:
            return os.environ.get(name, default).strip()

        return cls(
            plane_base_url=_env("PLANE_BASE_URL"),
            plane_auth_base_url=_env("PLANE_AUTH_BASE_URL"),
            plane_service_token=_env("PLANE_SERVICE_TOKEN"),
            plane_request_timeout_seconds=float(os.environ.get("PLANE_REQUEST_TIMEOUT_SECONDS", "3.0") or "3.0"),
            plane_default_department=_env("PLANE_DEFAULT_DEPARTMENT", "OpenEVA用户"),
        )


plane_config = PlaneConfig.from_env()


def get_plane_deployment_id() -> str:
    """返回 Plane 侧用于约束身份绑定范围的部署标识。"""
    configured = os.environ.get("SYNLORA_DEPLOYMENT_ID", "").strip()
    if configured:
        return configured
    instance_id = os.environ.get("YUXI_INSTANCE_ID", "").strip()
    if instance_id:
        digest = hashlib.sha256(instance_id.encode()).hexdigest()[:16]
        return f"yuxi-{digest}"
    return "yuxi-dev"


class PlaneAuthError(Exception):
    """Plane 登录验证失败；status_code 直接映射 HTTP 状态。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class PlaneProfile:
    """验证通过后的 Plane 用户最小档案。"""

    plane_user_id: str
    email: str
    display_name: str


def resolve_auth_base_url(config: PlaneConfig) -> str:
    """Plane 登录验证地址：优先专用配置，回落通用 base_url；未配置返回空串。"""
    return (config.plane_auth_base_url or config.plane_base_url or "").rstrip("/")


def _error_from_redirect(location: str) -> tuple[str, str]:
    """从 sign-in 302 的 Location 提取 (error_code, error_message)。"""
    query = parse_qs(urlsplit(location).query)
    code = str((query.get("error_code") or [""])[0])
    message = str((query.get("error_message") or [""])[0])
    return code, message


def _map_error(code: str) -> PlaneAuthError:
    """把 Plane 错误码映射为本方 HTTP 语义。"""
    if code == _CODE_RATE_LIMIT:
        return PlaneAuthError(429, "尝试过于频繁，请稍后再试")
    if code == _CODE_INSTANCE_NOT_CONFIGURED:
        return PlaneAuthError(503, "OpenEVA 实例未完成初始化")
    if code == _CODE_PASSWORD_LOGIN_DISABLED:
        return PlaneAuthError(403, "OpenEVA 已禁用密码登录")
    if code == _CODE_ACCOUNT_DEACTIVATED:
        return PlaneAuthError(401, "账号已禁用")
    return PlaneAuthError(401, "邮箱或密码错误")


async def plane_authenticate(
    email: str,
    password: str,
    config: PlaneConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PlaneProfile:
    """用 Plane 浏览器登录流验证邮箱密码，返回 Plane 用户档案。

    Raises:
        PlaneAuthError: 未配置（503）、凭证错误（401/403）、限流（429）、
            Plane 不可达或响应异常（502）。
    """
    base = resolve_auth_base_url(config)
    if not base:
        raise PlaneAuthError(503, "OpenEVA 账号登录未配置")
    timeout = max(_LOGIN_TIMEOUT_SECONDS, config.plane_request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            csrf_resp = await client.get(f"{base}/auth/get-csrf-token/")
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务不可用") from exc
        if csrf_resp.status_code >= 400:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")
        try:
            csrf_token = str(csrf_resp.json().get("csrf_token") or "")
        except ValueError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常") from exc
        if not csrf_token:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")

        try:
            resp = await client.post(
                f"{base}/auth/sign-in/",
                data={"email": email, "password": password},
                headers={"X-CSRFToken": csrf_token, "Referer": f"{base}/"},
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务不可用") from exc
        if resp.status_code not in (301, 302, 303, 307, 308):
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")
        code, _message = _error_from_redirect(resp.headers.get("location", ""))
        if code:
            raise _map_error(code)
        if not (client.cookies.get("session-id") or client.cookies.get("sessionid")):
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")

        try:
            me_resp = await client.get(f"{base}/api/users/me/")
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败") from exc
        if me_resp.status_code >= 400:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败")
        try:
            payload = me_resp.json()
        except ValueError as exc:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败") from exc
        plane_user_id = str(payload.get("id") or "")
        if not plane_user_id:
            raise PlaneAuthError(502, "OpenEVA 用户信息不完整")
        profile_email = str(payload.get("email") or email).strip().lower() or email
        display_name = str(payload.get("display_name") or payload.get("email") or email).strip()
        return PlaneProfile(plane_user_id=plane_user_id, email=profile_email, display_name=display_name)


async def _get_or_create_plane_department(db, config: PlaneConfig) -> Department:
    """获取或创建 OpenEVA 联邦用户的默认部门。"""
    dept_name = config.plane_default_department or "OpenEVA用户"
    result = await db.execute(select(Department).filter(Department.name == dept_name))
    dept = result.scalar_one_or_none()
    if dept:
        return dept
    dept = Department(name=dept_name, description=f"{dept_name}部门")
    db.add(dept)
    try:
        await db.commit()
        await db.refresh(dept)
        return dept
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(Department).filter(Department.name == dept_name))
        return result.scalar_one()


async def _unique_plane_username(db, base_name: str, plane_user_id: str) -> str:
    """为 Plane 联邦用户生成不冲突的用户名。"""
    candidate = _USERNAME_SAFE_RE.sub("", base_name).strip("._-")[:32]
    if not candidate:
        candidate = f"plane_{plane_user_id[:12]}"
    result = await db.execute(select(User.id).filter(User.username == candidate))
    if result.scalar_one_or_none() is None:
        return candidate
    digest = hashlib.sha256(plane_user_id.encode()).hexdigest()[:6]
    for suffix in (digest, *(f"{digest}-{i}" for i in range(2, 20))):
        result = await db.execute(select(User.id).filter(User.username == f"{candidate}-{suffix}"))
        if result.scalar_one_or_none() is None:
            return f"{candidate}-{suffix}"
    raise PlaneAuthError(500, "无法为 OpenEVA 账户生成可用用户名，请联系管理员")


async def get_or_create_plane_user(db, profile: PlaneProfile, config: PlaneConfig) -> User:
    """按 uid=plane:<pid> 获取 Plane 身份用户，首次出现时自动建号。"""
    uid = f"plane:{profile.plane_user_id}"
    result = await db.execute(select(User).filter(User.uid == uid, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user:
        user.last_login = utc_now_naive()
        await db.commit()
        return user

    dept = await _get_or_create_plane_department(db, config)
    username = await _unique_plane_username(db, profile.display_name or profile.email, profile.plane_user_id)
    user = User(
        username=username,
        uid=uid,
        phone_number=None,
        avatar=None,
        password_hash=AuthUtils.hash_password(secrets.token_urlsafe(32)),
        role="user",
        department_id=dept.id if dept else None,
        last_login=utc_now_naive(),
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        logger.info(f"Created Plane federated user: {user.username} ({uid})")
        return user
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(User).filter(User.uid == uid, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if user:
            return user
        raise PlaneAuthError(502, "OpenEVA 账户登录失败，请重试")


async def _converge_to_bound_user(db, links: list[PlaneAccountLink]) -> tuple[User, PlaneAccountLink] | None:
    """在绑定列表中找 core=ai4ms 且账户仍存活的收敛目标；无则返回 None。"""
    user_repo = UserRepository()
    for link in links:
        if str(link.core or "ai4ms") != "ai4ms":
            continue
        bound = await user_repo.get_active_by_id_with_db(db, link.synlora_user_id)
        if bound is not None:
            return bound, link
    return None


async def resolve_plane_login_user(
    db, links: list[PlaneAccountLink], profile: PlaneProfile, config: PlaneConfig
) -> tuple[User, PlaneAccountLink | None]:
    """按绑定核心方解析登录身份：core=ai4ms 收敛到绑定账户，否则 Plane 身份。

    绑定账户已删除时回落 Plane 身份（与老平台口径一致）。
    """
    converged = await _converge_to_bound_user(db, links)
    if converged is not None:
        bound, link = converged
        bound.last_login = utc_now_naive()
        await db.commit()
        return bound, link
    user = await get_or_create_plane_user(db, profile, config)
    return user, None


async def resolve_plane_delegated_user(
    db, *, plane_user_id: str, display_name: str, config: PlaneConfig
) -> tuple[User, str]:
    """解析 Plane BFF 委托身份：core=ai4ms 绑定收敛，否则即时建号。

    Returns:
        (用户, linked_user_id)：收敛成功时为绑定账户 id 字符串，否则为空串。
    """
    links = await PlaneAccountLinkRepository(db).list_for_plane_user(plane_user_id)
    converged = await _converge_to_bound_user(db, links)
    if converged is not None:
        bound, _link = converged
        bound.last_login = utc_now_naive()
        await db.commit()
        return bound, str(bound.id)
    profile = PlaneProfile(plane_user_id=plane_user_id, email="", display_name=display_name)
    user = await get_or_create_plane_user(db, profile, config)
    return user, ""


async def repair_account_link(
    db,
    *,
    account_link_id: str,
    workspace_id: str,
    plane_user_id: str,
    external_subject: str,
    synlora_user_id: str,
    synlora_username: str,
) -> dict:
    """幂等补记 Plane 委托身份与本方账户的绑定（Plane 管理命令调用）。

    Returns:
        {"idempotent": bool}：True 表示同绑定已存在（路由回 200），False 表示新建。

    Raises:
        HTTPException: 用户名/ID 校验失败（403）、一对多或绑定冲突（409）。
    """
    matched_user = await UserRepository().get_active_by_username_with_db(db, synlora_username)
    if matched_user is None or str(matched_user.id) != synlora_user_id:
        raise HTTPException(403, "Synlora 用户校验失败")

    repo = PlaneAccountLinkRepository(db)
    existing_by_link = await repo.get_by_account_link_id(account_link_id)
    existing_by_plane = await repo.list_for_binding(plane_workspace_id=workspace_id, plane_user_id=plane_user_id)
    existing_by_synlora = await repo.list_for_synlora_user(matched_user.id)
    if any(link.plane_user_id != plane_user_id for link in existing_by_synlora):
        raise HTTPException(409, "一个 Synlora 用户只能绑定一个 Plane 用户")
    existing = existing_by_link or (existing_by_plane[0] if existing_by_plane else None)
    identities = {link.account_link_id for link in (existing_by_link, *existing_by_plane) if link}
    if len(identities) > 1 or (
        existing is not None
        and (
            existing.plane_workspace_id != workspace_id
            or existing.plane_user_id != plane_user_id
            or str(existing.synlora_user_id) != synlora_user_id
            or existing.delegated_subject != external_subject
        )
    ):
        raise HTTPException(409, "Plane 账号绑定冲突")

    if existing is not None:
        return {"idempotent": True}

    await repo.create(
        {
            "account_link_id": account_link_id,
            "plane_workspace_id": workspace_id,
            "plane_user_id": plane_user_id,
            "synlora_user_id": matched_user.id,
            "delegated_subject": external_subject,
            "synlora_username": matched_user.username,
            "source": "repair",
        }
    )
    await db.commit()
    return {"idempotent": False}


def issue_plane_login_token(user: User, *, profile: PlaneProfile) -> str:
    """签发 OpenEVA 网页登录令牌（24 小时）。"""
    return AuthUtils.create_access_token(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": "user",
            "auth": "plane",
            "plane_user_id": profile.plane_user_id,
            "email": profile.email,
        },
        timedelta(hours=PLANE_LOGIN_TTL_HOURS),
    )


def issue_plane_delegated_token(
    user: User,
    *,
    plane_user_id: str,
    plane_workspace_id: str,
    account_link_id: str,
) -> str:
    """签发 Plane BFF 委托令牌（1 小时）。"""
    return AuthUtils.create_access_token(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": "user",
            "auth": "plane",
            "plane_delegated": True,
            "plane_user_id": plane_user_id,
            "plane_workspace_id": plane_workspace_id,
            "account_link_id": account_link_id,
        },
        timedelta(hours=PLANE_DELEGATED_TTL_HOURS),
    )


def plane_claims_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """从已验证的 JWT payload 提取 OpenEVA 联邦声明（无则返回空字典）。"""
    if payload.get("auth") != "plane":
        return {}
    claims: dict[str, Any] = {"auth": "plane"}
    for key in ("plane_user_id", "email", "plane_workspace_id", "account_link_id"):
        value = payload.get(key)
        if value:
            claims[key] = value
    if payload.get("plane_delegated"):
        claims["plane_delegated"] = True
    return claims
