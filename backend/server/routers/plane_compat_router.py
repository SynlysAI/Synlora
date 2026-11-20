"""Plane(OpenEVA) 兼容面路由。

对齐老平台（Synlora-main develop）暴露给 Plane BFF 的契约：健康检查带
deployment_id、个人令牌校验、OpenEVA 账号联邦登录、委托令牌交换与账户绑定
补账。用例流程在 yuxi.services.plane_auth_service；会话/运行兼容端点在
后续切片接入。
"""

import asyncio
import hmac
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
from yuxi.services.plane_context_service import (
    PlaneMcpRuntime,
    ResearchContextError,
    ResearchContextMetadata,
    validate_plane_mcp_scope,
    validate_plane_metadata_scope,
    validate_research_context,
)
from yuxi.services.plane_session_compat_service import PlaneSessionCompatService
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


# ---------------------------------------------------------------------------
# v1 Sessions 兼容面（Plane BFF 主路径）
# ---------------------------------------------------------------------------

SSE_STREAM_MAX_SECONDS = 30 * 60
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 15.0


class SessionCreateBody(BaseModel):
    """v1 会话创建请求体（plane 专属字段经上下文校验后落 metadata）。"""

    title: str = ""
    assistant_id: str = ""
    model_provider_id: str | None = None
    project_id: str | None = None
    approval_mode: str | None = None
    enabled_plugins: list[str] | None = None
    enabled_mcp: list[str] | None = None
    research_context: ResearchContextMetadata | None = None
    plane_mcp: PlaneMcpRuntime | None = None
    plane_meta: dict | None = None

    @field_validator("approval_mode")
    @classmethod
    def _validate_approval_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("always_allow", "ask_for_dangerous"):
            raise ValueError("approval_mode 必须为 always_allow 或 ask_for_dangerous")
        return value


class SessionPatchBody(BaseModel):
    """v1 会话更新请求体。"""

    title: str | None = None
    archived: bool | None = None
    approval_mode: str | None = None

    @field_validator("approval_mode")
    @classmethod
    def _validate_approval_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("always_allow", "ask_for_dangerous"):
            raise ValueError("approval_mode 必须为 always_allow 或 ask_for_dangerous")
        return value


class V1MessageBody(BaseModel):
    """v1 消息/run 提交请求体。"""

    text: str
    research_context: ResearchContextMetadata | None = None
    plane_mcp: PlaneMcpRuntime | None = None
    plane_meta: dict | None = None
    page_context: dict | None = None
    external_run_id: str = ""

    @field_validator("text")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text 不能为空")
        return value.strip()


def _require_user(user: User | None) -> User:
    """v1 端点统一鉴权（不强制部门绑定，与老平台 /me 口径一致）。"""
    if user is None:
        raise HTTPException(401, "请登录后再访问")
    return user


@plane_compat.post("/v1/sessions", status_code=201)
async def plane_create_session(
    body: SessionCreateBody,
    request: Request,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建 v1 会话（research_context 存 metadata，token 不落库）。"""
    _require_user(user)
    research_context = body.research_context
    if research_context is not None:
        token = request.headers.get("X-Research-Context-Token", "")
        try:
            await validate_research_context(token=token, expected=research_context)
        except ResearchContextError as exc:
            raise HTTPException(exc.status_code, exc.message)
        if body.plane_mcp is not None:
            try:
                validate_plane_mcp_scope(body.plane_mcp, research_context)
            except ResearchContextError as exc:
                raise HTTPException(exc.status_code, exc.message)
            if not request.headers.get("X-Plane-MCP-Token", ""):
                raise HTTPException(401, "缺少 Plane MCP 令牌")
        if body.plane_meta is not None:
            try:
                validate_plane_metadata_scope(body.plane_meta, research_context)
            except ResearchContextError as exc:
                raise HTTPException(exc.status_code, exc.message)
    elif body.plane_meta is not None:
        raise HTTPException(403, "Plane 元数据需要有效的科研上下文")
    service = PlaneSessionCompatService(db)
    return await service.create_session(user=user, body=body.model_dump(mode="json", exclude_none=True))


@plane_compat.get("/v1/sessions")
async def plane_list_sessions(
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """当前身份（含绑定别名）的 v1 会话列表。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).list_sessions(user)


@plane_compat.patch("/v1/sessions/{thread_id}")
async def plane_patch_session(
    thread_id: str,
    body: SessionPatchBody,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新 v1 会话（标题/归档/审批模式）。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).patch_session(thread_id, user, body.model_dump(exclude_none=True))


@plane_compat.delete("/v1/sessions/{thread_id}")
async def plane_delete_session(
    thread_id: str,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """软删 v1 会话。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).delete_session(thread_id, user)


@plane_compat.post("/v1/sessions/{thread_id}/runs", status_code=202)
async def plane_start_run(
    thread_id: str,
    body: V1MessageBody,
    request: Request,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交后台 run 并立即返回稳定 run id（Plane BFF 主路径）。"""
    _require_user(user)
    service = PlaneSessionCompatService(db)
    try:
        return await service.submit_run(
            thread_id=thread_id,
            user=user,
            text=body.text,
            external_run_id=body.external_run_id,
            page_context=body.page_context,
            context_token=request.headers.get("X-Research-Context-Token", ""),
            mcp_token=request.headers.get("X-Plane-MCP-Token", ""),
            research_context=body.research_context,
            plane_mcp=body.plane_mcp,
            plane_meta=body.plane_meta,
        )
    except ResearchContextError as exc:
        raise HTTPException(exc.status_code, exc.message)


@plane_compat.get("/v1/sessions/{thread_id}/runs")
async def plane_list_runs(
    thread_id: str,
    status: str | None = None,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """v1 run 列表（对账用，含 external_run_id）。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).list_runs(thread_id, user, status)


@plane_compat.get("/v1/sessions/{thread_id}/events")
async def plane_list_events(
    thread_id: str,
    after_seq: int = -1,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """durable 事件回放（断线补齐）。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).events_after(thread_id, user, after_seq)


@plane_compat.get("/v1/sessions/{thread_id}/events/stream")
async def plane_stream_events(
    thread_id: str,
    request: Request,
    after_seq: int = -1,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """会话级常驻 SSE：先回放 durable 再持续跟进（心跳 15s，上限 30 分钟）。"""
    _require_user(user)
    cursor_header = request.headers.get("Last-Event-ID")
    if cursor_header is not None:
        try:
            after_seq = int(cursor_header)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    service = PlaneSessionCompatService(db)
    await service.get_own_session(thread_id, user)
    started = time.monotonic()
    last_heartbeat = time.monotonic()

    async def _poll_events(cursor: int) -> list[dict]:
        # 每轮短会话：常驻流不得长时间持有请求级连接（idle-in-transaction 拖垮池）
        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as poll_db:
            return await PlaneSessionCompatService(poll_db).events_after(thread_id, user, cursor)

    async def event_stream():
        nonlocal last_heartbeat
        cursor = after_seq
        while time.monotonic() - started < SSE_STREAM_MAX_SECONDS:
            if await request.is_disconnected():
                return
            for event in await _poll_events(cursor):
                cursor = max(cursor, event["seq"])
                data = json.dumps(
                    {"seq": event["seq"], "type": event["type"], "payload": event["payload"], "ts": event["ts"]},
                    ensure_ascii=False,
                )
                yield f"event: {event['type']}\ndata: {data}\nid: {event['seq']}\n\n"
            if time.monotonic() - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@plane_compat.post("/v1/runs/{v1_run_id}/cancel")
async def plane_cancel_run(
    v1_run_id: str,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """取消 run（审批等待中的 run 以拒绝工具方式收尾并释放线程）。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).cancel_run(v1_run_id, user)


@plane_compat.post("/v1/runs/{v1_run_id}/answer")
async def plane_answer_run(
    v1_run_id: str,
    body: V1MessageBody,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审批/提问应答（"允许"/"拒绝" 或问题回答），v1 run id 不变。"""
    _require_user(user)
    return await PlaneSessionCompatService(db).answer_run(v1_run_id, user, body.text)
