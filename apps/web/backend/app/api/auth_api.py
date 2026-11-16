"""认证 API：登录与当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user, get_settings
from app.core.auth import issue_token, verify_password
from app.core.settings import Settings
from app.services.plane_auth import PlaneAuthError, authenticate as plane_authenticate

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

VALID_ROLES = ("admin", "user")

# Plane 登录 token 有效期（小时）：无服务端会话可吊销，比本方登录（7 天）短，
# 被禁用的 Plane 用户最多一天后失去访问。
PLANE_LOGIN_TTL_HOURS = 24


class LoginBody(BaseModel):
    """登录请求体。

    Attributes:
        username: 用户名。
        password: 明文密码。
    """

    username: str
    password: str


class PlaneLoginBody(BaseModel):
    """Plane 账号登录请求体。

    Attributes:
        email: Plane 登录邮箱（Plane 的登录键）。
        password: 明文密码。
    """

    email: str
    password: str


async def _find_user_sqlite(request: Request, username: str) -> dict | None:
    """sqlite 模式：查 local_users 集合（username 为索引列）。

    Args:
        request: 请求对象（取 app.state.store）。
        username: 用户名。

    Returns:
        用户文档；未找到返回 None。

    Raises:
        HTTPException: store 未初始化（503）。
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "存储未就绪")
    users = await store.list("local_users", filters={"username": username})
    return users[0] if users else None


async def _find_user_mongo(request: Request, username: str) -> dict | None:
    """mongodb 模式：连 ai4ms 库 users 集合按 username 查（客户端缓存于 app.state）。

    Args:
        request: 请求对象（取配置与缓存的 motor 客户端）。
        username: 用户名。

    Returns:
        用户文档（含 username/password_hash/role/status）；未找到返回 None。
    """
    from motor.motor_asyncio import AsyncIOMotorClient  # 延迟导入：非 prod 无需装 motor

    client = getattr(request.app.state, "_mongo_users_client", None)
    if client is None:
        client = AsyncIOMotorClient(request.app.state.settings.mongodb_uri)
        request.app.state._mongo_users_client = client
    return await client["ai4ms"]["users"].find_one({"username": username})


async def find_user(request: Request, username: str) -> dict | None:
    """按用户名查用户（按 storage_backend 分支，便于测试注入/monkeypatch）。

    Args:
        request: 请求对象。
        username: 用户名。

    Returns:
        用户文档；未找到返回 None。
    """
    settings: Settings = request.app.state.settings
    if settings.storage_backend == "mongodb":
        return await _find_user_mongo(request, username)
    return await _find_user_sqlite(request, username)


@router.post("/login")
async def login(
    body: LoginBody, request: Request, settings=Depends(get_settings)
) -> dict:
    """账号密码登录：校验通过后签发 AI4MS 兼容 token。

    Raises:
        HTTPException: 用户名/密码错误或账号禁用（401）、存储未就绪（503）。
    """
    user = await find_user(request, body.username)
    if user is None or not verify_password(
        body.password, str(user.get("password_hash", ""))
    ):
        raise HTTPException(401, "用户名或密码错误")
    if str(user.get("status", "active")) != "active":
        raise HTTPException(401, "账号已禁用")
    role = user.get("role") if user.get("role") in VALID_ROLES else "user"
    payload = {
        # sub 必须是本方主键（AI⁴MS 文档 _id）：workspaces/会话/项目/能力安装都按它存
        "sub": str(user.get("_id", "")),
        "username": str(user.get("username", "")),
        "role": role,
    }
    if user.get("user_id"):
        # AI⁴MS 侧账号 id（与 _id 不同字段）：写进 payload 供代签子平台凭证时走
        # 快路径（省掉一次 ai4ms.users 回查）；sqlite 本地用户没有该字段
        payload["ai4ms_user_id"] = str(user["user_id"])
    return {
        "token": issue_token(payload, settings),
        "username": payload["username"],
        "role": payload["role"],
    }


@router.post("/plane-login")
async def plane_login(
    body: PlaneLoginBody, request: Request, settings=Depends(get_settings)
) -> dict:
    """Plane 账号登录：代理验证 Plane 邮箱密码后签发本方 token（不落库）。

    身份归一：优先取 ``plane_account_links`` 里该 Plane 用户绑定的 Synlora 账户
    （与委托登录/修复端点共享同一绑定表），未绑定时 ``sub = plane:<plane_user_id>``
    ——与委托登录的回退身份一致，网页登录与 Plane 云端智能体落到同一身份。

    Raises:
        HTTPException: 邮箱密码错误/账号禁用（401/403/429）、Plane 不可用（502）、
            未配置（503）。
    """
    email = body.email.strip().lower()
    if not email or "@" not in email or not body.password:
        raise HTTPException(401, "邮箱或密码错误")
    try:
        profile = await plane_authenticate(email, body.password, settings)
    except PlaneAuthError as exc:
        raise HTTPException(exc.status_code, exc.message)

    sub = f"plane:{profile.plane_user_id}"
    store = getattr(request.app.state, "store", None)
    if store is not None:
        links = await store.list(
            "plane_account_links",
            filters={"plane_user_id": profile.plane_user_id},
        )
        for link in links:
            linked = str(link.get("synlora_user_id") or "")
            if linked:
                # 绑定由修复端点/委托流程显式建立（一个 Plane 用户的所有绑定
                # 指向同一 Synlora 账户），取第一条即定身份
                sub = linked
                break
    payload = {
        "sub": sub,
        "username": profile.display_name or profile.email,
        "role": "user",
        "auth": "plane",
        "plane_user_id": profile.plane_user_id,
        "email": profile.email,
    }
    return {
        "token": issue_token(payload, settings, ttl_hours=PLANE_LOGIN_TTL_HOURS),
        "username": payload["username"],
        "role": payload["role"],
    }


@router.get("/me")
async def me(
    request: Request, user=Depends(get_current_user), settings=Depends(get_settings)
) -> dict:
    """当前用户信息（sqlite 模式直接回显；mongodb 模式回查 users 确认 status=active）。

    Plane 来源身份（plane-login / 委托登录）不落 ``ai4ms.users``，跳过回查——
    其存活性由签发时的 Plane 验证与 24h 短时效保证。

    Raises:
        HTTPException: mongodb 模式下用户不存在或已禁用（401）。
    """
    sub = str(user.get("sub", ""))
    plane_identity = (
        user.get("auth") == "plane" or sub.startswith(("plane:", "plane-delegated-"))
    )
    if (
        settings.storage_backend == "mongodb"
        and not plane_identity
        and sub not in ("anon", "dev")
    ):
        record = await find_user(request, str(user.get("username", "")))
        if record is None or str(record.get("status", "active")) != "active":
            raise HTTPException(401, "用户不存在或已禁用")
    return {**user, "deployment_id": settings.deployment_id}
