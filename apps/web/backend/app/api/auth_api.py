"""认证 API：登录与当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user, get_settings
from app.core.auth import issue_token, verify_password
from app.core.settings import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

VALID_ROLES = ("admin", "user")


class LoginBody(BaseModel):
    """登录请求体。

    Attributes:
        username: 用户名。
        password: 明文密码。
    """

    username: str
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
async def login(body: LoginBody, request: Request,
                settings=Depends(get_settings)) -> dict:
    """账号密码登录：校验通过后签发 AI4MS 兼容 token。

    Raises:
        HTTPException: 用户名/密码错误或账号禁用（401）、存储未就绪（503）。
    """
    user = await find_user(request, body.username)
    if user is None or not verify_password(body.password, str(user.get("password_hash", ""))):
        raise HTTPException(401, "用户名或密码错误")
    if str(user.get("status", "active")) != "active":
        raise HTTPException(401, "账号已禁用")
    role = user.get("role") if user.get("role") in VALID_ROLES else "user"
    payload = {
        "sub": str(user.get("_id", "")),
        "username": str(user.get("username", "")),
        "role": role,
    }
    return {
        "token": issue_token(payload, settings),
        "username": payload["username"],
        "role": payload["role"],
    }


@router.get("/me")
async def me(request: Request, user=Depends(get_current_user),
             settings=Depends(get_settings)) -> dict:
    """当前用户信息（sqlite 模式直接回显；mongodb 模式回查 users 确认 status=active）。

    Raises:
        HTTPException: mongodb 模式下用户不存在或已禁用（401）。
    """
    if settings.storage_backend == "mongodb" and user.get("sub") not in ("anon", "dev"):
        record = await find_user(request, str(user.get("username", "")))
        if record is None or str(record.get("status", "active")) != "active":
            raise HTTPException(401, "用户不存在或已禁用")
    return user
