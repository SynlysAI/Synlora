"""Plane(OpenEVA) 兼容面路由。

对齐老平台（Synlora-main develop）暴露给 Plane BFF 的契约：健康检查带
deployment_id、个人令牌校验、OpenEVA 账号联邦登录、委托令牌交换与账户绑定
补账。用例流程在 yuxi.services.plane_auth_service；会话/运行兼容端点在
后续切片接入。
"""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_current_user, get_db
from yuxi import get_version
from yuxi.repositories.plane_account_link_repository import PlaneAccountLinkRepository
from yuxi.services.plane_auth_service import (
    PlaneAuthError,
    get_plane_deployment_id,
    issue_plane_delegated_token,
    issue_plane_login_token,
    plane_authenticate,
    plane_claims_from_payload,
    plane_config,
    repair_account_link,
    resolve_plane_delegated_user,
    resolve_plane_login_user,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils

DELEGATED_AUTH_SCHEMA = "plane-delegated-auth.v1"
ACCOUNT_LINK_REPAIR_SCHEMA = "plane-account-link-repair.v1"

plane_compat = APIRouter(tags=["plane-compat"])


class PlaneLoginBody(BaseModel):
    """OpenEVA 账号登录请求体。"""

    email: str
    password: str


class DelegatedTokenBody(BaseModel):
    """Plane BFF 委托令牌交换请求体。"""

    schema_version: str
    account_link_id: str
    workspace_id: str
    plane_user_id: str = Field(min_length=1)
    external_subject: str
    subject_username: str = ""

    @field_validator("schema_version")
    @classmethod
    def _freeze_schema_version(cls, value: str) -> str:
        if value != DELEGATED_AUTH_SCHEMA:
            raise ValueError(f"schema_version 必须为 {DELEGATED_AUTH_SCHEMA}")
        return value


class AccountLinkRepairBody(BaseModel):
    """Plane 侧绑定补账请求体。"""

    schema_version: str = ACCOUNT_LINK_REPAIR_SCHEMA
    account_link_id: str
    workspace_id: str
    plane_user_id: str
    external_subject: str
    synlora_user_id: str
    synlora_username: str
    workspace_slug: str = ""
    workspace_name: str = ""

    @field_validator("schema_version")
    @classmethod
    def _freeze_schema_version(cls, value: str) -> str:
        if value != ACCOUNT_LINK_REPAIR_SCHEMA:
            raise ValueError(f"schema_version 必须为 {ACCOUNT_LINK_REPAIR_SCHEMA}")
        return value


def _service_token_authorized(header_value: str, expected: str) -> bool:
    """校验 Plane 服务凭证（Bearer，字节级常数时间比较）。"""
    if not expected or not header_value.startswith("Bearer "):
        return False
    supplied = header_value[7:].encode("utf-8")
    return hmac.compare_digest(supplied, expected.encode("utf-8"))


def _require_service_token(request: Request) -> None:
    """拒绝未配置或错误的服务凭证（与老平台口径一致返回 403）。"""
    if not _service_token_authorized(request.headers.get("Authorization", ""), plane_config.plane_service_token):
        raise HTTPException(403, "Plane service credential is not configured or invalid")


@plane_compat.get("/health")
async def plane_compat_health() -> dict:
    """Plane 集成健康检查：仅表达 liveness，并返回部署标识。"""
    return {
        "status": "ok",
        "deployment_id": get_plane_deployment_id(),
        "version": get_version(),
        "version_label": get_version(),
    }


@plane_compat.get("/v1/auth/me")
async def plane_compat_me(
    authorization: str | None = Header(None),
    user: User | None = Depends(get_current_user),
) -> dict:
    """当前身份投影：Plane 侧校验个人令牌时读取 sub/username/deployment_id。"""
    if user is None:
        raise HTTPException(401, "请登录后再访问")
    payload = {"sub": str(user.id), "username": user.username, "role": user.role}
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token and not token.startswith("yxkey_"):
        claims = AuthUtils.decode_token(token) or {}
        payload.update(plane_claims_from_payload(claims))
    return {**payload, "deployment_id": get_plane_deployment_id()}


@plane_compat.post("/v1/auth/plane-login")
async def plane_compat_login(body: PlaneLoginBody, db: AsyncSession = Depends(get_db)) -> dict:
    """OpenEVA 账号登录：代理验证邮箱密码后按绑定核心方收敛身份。"""
    email = body.email.strip().lower()
    if not email or "@" not in email or not body.password:
        raise HTTPException(401, "邮箱或密码错误")
    try:
        profile = await plane_authenticate(email, body.password, plane_config)
        links = await PlaneAccountLinkRepository(db).list_for_plane_user(profile.plane_user_id)
        user, _link = await resolve_plane_login_user(db, links, profile, plane_config)
    except PlaneAuthError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return {
        "token": issue_plane_login_token(user, profile=profile),
        "username": user.username,
        "role": "user",
    }


@plane_compat.post("/v1/research/delegated-token")
async def plane_delegated_token(
    body: DelegatedTokenBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Plane 服务凭证换短期委托令牌；身份按 core=ai4ms 绑定收敛。"""
    _require_service_token(request)
    try:
        user, linked_user_id = await resolve_plane_delegated_user(
            db,
            plane_user_id=body.plane_user_id,
            display_name=body.subject_username or body.external_subject,
            config=plane_config,
        )
    except PlaneAuthError as exc:
        raise HTTPException(exc.status_code, exc.message)

    token = issue_plane_delegated_token(
        user,
        plane_user_id=body.plane_user_id,
        plane_workspace_id=body.workspace_id,
        account_link_id=body.account_link_id,
    )
    return {
        "schema_version": DELEGATED_AUTH_SCHEMA,
        "token": token,
        "subject": body.external_subject,
        "linked_user_id": linked_user_id,
        "account_link_id": body.account_link_id,
        "expires_in": 3600,
    }


@plane_compat.post("/v1/research/account-links", status_code=201)
async def plane_repair_account_link(
    body: AccountLinkRepairBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """幂等补记 Plane 委托身份与本方账户的绑定（Plane 管理命令调用）。"""
    _require_service_token(request)

    result = await repair_account_link(
        db,
        account_link_id=body.account_link_id,
        workspace_id=body.workspace_id,
        plane_user_id=body.plane_user_id,
        external_subject=body.external_subject,
        synlora_user_id=body.synlora_user_id,
        synlora_username=body.synlora_username,
    )
    if result["idempotent"]:
        return JSONResponse(
            {
                "schema_version": body.schema_version,
                "account_link_id": body.account_link_id,
                "idempotent": True,
                "sessions_backfilled": 0,
            },
            status_code=200,
        )
    return {
        "schema_version": body.schema_version,
        "account_link_id": body.account_link_id,
        "idempotent": False,
        "sessions_backfilled": 0,
    }
