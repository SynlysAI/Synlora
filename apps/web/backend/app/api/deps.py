"""FastAPI 认证依赖。"""
from fastapi import Depends, HTTPException, Request

from app.core.auth import parse_token
from app.core.settings import Settings


def get_settings(request: Request) -> Settings:
    """从 app.state 取 Settings。"""
    return request.app.state.settings


async def get_current_user(request: Request, settings=Depends(get_settings)) -> dict:
    """解析 Bearer token 返回用户 payload。

    auth_enabled=false 时匿名放行；DEV_AUTH_TOKEN 命中时返回开发管理员。
    失败抛 401。
    """
    if not settings.auth_enabled:
        return {"sub": "anon", "username": "anonymous", "role": "admin"}
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if settings.dev_auth_token and token == settings.dev_auth_token:
        return {"sub": "dev", "username": "dev", "role": "admin"}
    if not token:
        raise HTTPException(401, "缺少 token")
    payload = parse_token(token, settings)
    if payload is None:
        raise HTTPException(401, "token 无效或过期")
    return payload


async def require_admin(user=Depends(get_current_user)) -> dict:
    """要求 admin 角色。"""
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
