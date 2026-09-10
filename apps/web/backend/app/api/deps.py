"""FastAPI 依赖：认证 + repo 集中访问。"""
import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.core.auth import parse_token
from app.core.settings import Settings


@dataclass(frozen=True)
class Repos:
    """集中持有六个 repo（lifespan 初始化后可取）。

    Attributes:
        provider: 模型服务 repo（api_key 加密）。
        assistant: 助手 repo（builtin 拒删）。
        session: 会话元数据 repo。
        run: 对话运行 repo。
        file: 用户文件 repo。
        event: 会话事件 repo。
    """

    provider: Any
    assistant: Any
    session: Any
    run: Any
    file: Any
    event: Any


def get_settings(request: Request) -> Settings:
    """从 app.state 取 Settings。"""
    return request.app.state.settings


def get_repos(request: Request) -> Repos:
    """从 app.state 集中取六个 repo。

    Raises:
        HTTPException: 存储未就绪（503）。
    """
    s = request.app.state
    if getattr(s, "store", None) is None:
        raise HTTPException(503, "存储未就绪")
    return Repos(
        provider=s.provider_repo,
        assistant=s.assistant_repo,
        session=s.session_repo,
        run=s.run_repo,
        file=s.file_repo,
        event=s.event_repo,
    )


async def get_current_user(request: Request, settings=Depends(get_settings)) -> dict:
    """解析 Bearer token 返回用户 payload。

    两个开发后门（auth_enabled=false 匿名 admin、DEV_AUTH_TOKEN 固定 token）
    仅在 storage_backend=sqlite 时生效，mongodb 生产模式下走正常 401 流程。
    失败抛 401。
    """
    dev_mode = settings.storage_backend == "sqlite"
    if dev_mode and not settings.auth_enabled:
        return {"sub": "anon", "username": "anonymous", "role": "admin"}
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if dev_mode and settings.dev_auth_token and hmac.compare_digest(
        token.encode(), settings.dev_auth_token.encode()
    ):
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
