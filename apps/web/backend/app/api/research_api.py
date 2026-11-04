"""Plane Research Agent delegation and capability manifest contracts."""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from app.api.auth_api import find_user
from app.api.deps import get_current_user, get_settings
from app.core.auth import issue_token
from app.services.tool_registry import REGISTRY

router = APIRouter(prefix="/api/v1/research", tags=["research"])


class DelegatedTokenBody(BaseModel):
    """plane-delegated-auth.v1 request."""

    schema_version: str
    account_link_id: str
    workspace_id: str
    plane_user_id: str
    external_subject: str
    subject_username: str = ""

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        """Only the frozen Plane contract is accepted."""
        if value != "plane-delegated-auth.v1":
            raise ValueError("unsupported delegated auth schema")
        return value


class AccountLinkRepairBody(BaseModel):
    """plane-account-link-repair.v1 request."""

    schema_version: str = "plane-account-link-repair.v1"
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
    def validate_schema(cls, value: str) -> str:
        """Only accept the frozen repair contract."""

        if value != "plane-account-link-repair.v1":
            raise ValueError("unsupported account-link repair schema")
        return value


def _service_token_authorized(supplied: str, expected: str) -> bool:
    """Compare Plane service credentials without leaking configuration state."""

    return bool(
        expected
        and supplied.startswith("Bearer ")
        and hmac.compare_digest(supplied[7:], expected)
    )


async def _backfill_plane_sessions(
    store,
    project_service,
    *,
    external_subject: str,
    synlora_user_id: str,
    workspace_id: str,
    workspace_slug: str,
    workspace_name: str,
) -> int:
    """Attach historical delegated Plane sessions to the dedicated Plane workspace.

    Session and event ownership is never changed; only grouping metadata is added.
    """
    from app.api.sessions_api import ResearchContextMetadata

    sessions = await store.list("sessions", filters={"user_id": external_subject})
    updated = 0
    for session in sessions:
        raw_context = session.get("research_context")
        if not raw_context or (session.get("plane_meta") and session.get("project_id")):
            continue
        try:
            context = ResearchContextMetadata.model_validate(raw_context)
        except ValueError:
            continue
        target = context.target
        target_type = str(target.type if target else "RESEARCH_CHAIN_NODE")
        target_id = str(
            (target.chain_node_id if target else context.chain_node_id)
            or (target.research_chain_id if target else "")
            or (target.project_id if target else context.research_project_id)
            or (target.workspace_id if target else context.workspace_id)
        )
        workspace = {
            "id": workspace_id,
            "slug": workspace_slug
            or str(target.workspace_slug if target else context.workspace_slug),
            "name": workspace_name or workspace_slug or workspace_id,
        }
        project = await project_service.ensure_plane_project(
            synlora_user_id,
            plane_workspace_id=workspace_id,
            plane_workspace_name=workspace["name"] or workspace["slug"],
        )
        plane_meta = {
            "workspace": workspace,
            "target": {
                "type": target_type,
                "id": target_id,
                "title": session.get("title") or f"Plane {target_type}",
                "hierarchy": [
                    {
                        "type": "WORKSPACE",
                        "id": workspace_id,
                        "title": workspace["name"],
                    }
                ],
            },
            "page": None,
        }
        await store.update(
            "sessions",
            str(session["_id"]),
            {
                "project_id": str(project["_id"]),
                "origin": "PLANE",
                "plane_meta": plane_meta,
            },
        )
        updated += 1
    return updated


@router.post("/delegated-token")
async def delegated_token(
    body: DelegatedTokenBody,
    request: Request,
    settings=Depends(get_settings),
) -> dict:
    """Exchange a Plane service credential for a short-lived Synlora user token."""
    service_token = settings.plane_service_token
    supplied = request.headers.get("Authorization", "")
    if (
        not service_token
        or not supplied.startswith("Bearer ")
        or not hmac.compare_digest(supplied[7:], service_token)
    ):
        raise HTTPException(403, "Plane delegated auth is not configured or invalid")
    linked_subject = body.external_subject
    linked_user_id = ""
    linked_username = body.external_subject
    store = getattr(request.app.state, "store", None)
    username = body.subject_username.strip()
    matched_user = (
        await find_user(request, username) if store is not None and username else None
    )
    if (
        matched_user is not None
        and str(matched_user.get("status", "active")) == "active"
    ):
        candidate_user_id = str(matched_user.get("_id", ""))
        existing_by_link = await store.get("plane_account_links", body.account_link_id)
        existing_by_plane = await store.list(
            "plane_account_links",
            filters={
                "plane_workspace_id": body.workspace_id,
                "plane_user_id": body.plane_user_id,
            },
        )
        existing = existing_by_link or (
            existing_by_plane[0] if existing_by_plane else None
        )
        # 两个唯一入口指向不同记录时说明绑定身份冲突，必须 fail-closed，
        # 不能凭 username 提示改写既有不可变绑定。
        identities = {
            str(item.get("account_link_id"))
            for item in (existing_by_link, *(existing_by_plane or []))
            if isinstance(item, dict)
        }
        binding_conflict = len(identities) > 1 or (
            existing is not None
            and (
                str(existing.get("plane_workspace_id")) != body.workspace_id
                or str(existing.get("plane_user_id")) != body.plane_user_id
                or str(existing.get("synlora_user_id")) != candidate_user_id
            )
        )
        if binding_conflict:
            matched_user = None
        elif existing is None:
            await store.insert(
                "plane_account_links",
                {
                    "_id": body.account_link_id,
                    "account_link_id": body.account_link_id,
                    "plane_workspace_id": body.workspace_id,
                    "plane_user_id": body.plane_user_id,
                    "synlora_user_id": candidate_user_id,
                    "delegated_subject": body.external_subject,
                    "created_at": int(time.time()),
                },
            )
            existing = await store.get("plane_account_links", body.account_link_id)
        if str(existing.get("synlora_user_id")) == candidate_user_id:
            linked_subject = body.external_subject
            linked_user_id = candidate_user_id
            linked_username = str(matched_user.get("username") or username)
    token = (
        issue_token(
            {
                "sub": linked_user_id,
                "username": linked_username,
                "role": "user",
                "plane_delegated": True,
                "plane_workspace_id": body.workspace_id,
                "plane_user_id": body.plane_user_id,
                "account_link_id": body.account_link_id,
            },
            settings,
            ttl_hours=1,
        )
        if linked_user_id
        else issue_token(
            {
                "sub": body.external_subject,
                "username": body.external_subject,
                "role": "user",
            },
            settings,
            ttl_hours=1,
        )
    )
    return {
        "schema_version": "plane-delegated-auth.v1",
        "token": token,
        "subject": linked_subject,
        "linked_user_id": linked_user_id,
        "account_link_id": body.account_link_id,
        "expires_in": 3600,
    }


@router.post("/account-links", status_code=201)
async def repair_account_link(
    body: AccountLinkRepairBody,
    request: Request,
    settings=Depends(get_settings),
) -> dict:
    """Idempotently alias a historical Plane delegated subject to a Synlora account.

    The endpoint is restricted to the Plane service credential. It never changes event
    ownership; it only creates the immutable alias used by session list/read access.
    """
    if not _service_token_authorized(
        request.headers.get("Authorization", ""), settings.plane_service_token
    ):
        raise HTTPException(
            403, "Plane account-link repair is not configured or invalid"
        )

    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "存储未就绪")
    matched_user = await find_user(request, body.synlora_username)
    if (
        matched_user is None
        or str(matched_user.get("status", "active")) != "active"
        or str(matched_user.get("_id", "")) != body.synlora_user_id
    ):
        raise HTTPException(403, "Synlora 用户校验失败")

    existing_by_link = await store.get("plane_account_links", body.account_link_id)
    existing_by_plane = await store.list(
        "plane_account_links",
        filters={
            "plane_workspace_id": body.workspace_id,
            "plane_user_id": body.plane_user_id,
        },
    )
    existing_by_synlora = await store.list(
        "plane_account_links",
        filters={"synlora_user_id": body.synlora_user_id},
    )
    if any(
        str(item.get("plane_user_id")) != body.plane_user_id
        for item in existing_by_synlora
    ):
        raise HTTPException(409, "一个 Synlora 用户只能绑定一个 Plane 用户")
    existing = existing_by_link or (existing_by_plane[0] if existing_by_plane else None)
    identities = {
        str(item.get("account_link_id"))
        for item in (existing_by_link, *(existing_by_plane or []))
        if isinstance(item, dict)
    }
    if len(identities) > 1 or (
        existing is not None
        and (
            str(existing.get("plane_workspace_id")) != body.workspace_id
            or str(existing.get("plane_user_id")) != body.plane_user_id
            or str(existing.get("synlora_user_id")) != body.synlora_user_id
            or str(existing.get("delegated_subject")) != body.external_subject
        )
    ):
        raise HTTPException(409, "Plane 账号绑定冲突")
    if existing is not None:
        backfilled = await _backfill_plane_sessions(
            request.app.state.store,
            request.app.state.project_service,
            external_subject=body.external_subject,
            synlora_user_id=body.synlora_user_id,
            workspace_id=body.workspace_id,
            workspace_slug=body.workspace_slug,
            workspace_name=body.workspace_name,
        )
        return JSONResponse(
            {
                "schema_version": body.schema_version,
                "account_link_id": body.account_link_id,
                "idempotent": True,
                "sessions_backfilled": backfilled,
            },
            status_code=200,
        )

    await store.insert(
        "plane_account_links",
        {
            "_id": body.account_link_id,
            "account_link_id": body.account_link_id,
            "plane_workspace_id": body.workspace_id,
            "plane_user_id": body.plane_user_id,
            "synlora_user_id": body.synlora_user_id,
            "delegated_subject": body.external_subject,
            "created_at": int(time.time()),
        },
    )
    backfilled = await _backfill_plane_sessions(
        store,
        request.app.state.project_service,
        external_subject=body.external_subject,
        synlora_user_id=body.synlora_user_id,
        workspace_id=body.workspace_id,
        workspace_slug=body.workspace_slug,
        workspace_name=body.workspace_name,
    )
    return {
        "schema_version": body.schema_version,
        "account_link_id": body.account_link_id,
        "idempotent": False,
        "sessions_backfilled": backfilled,
    }


@router.get("/capabilities")
async def capability_manifest(request: Request, user=Depends(get_current_user)) -> dict:
    """Return a read-only, user-scoped capability projection for Plane."""
    service = request.app.state.capability_service
    plugin_ids = await service.visible_ids(user["sub"], "plugin")
    policies = await service.policy.all_policies()
    plugins = []
    for plugin_id in sorted(plugin_ids):
        package = service.catalog.plugins.get(plugin_id)
        policy = policies.get(f"plugin:{plugin_id}", {})
        plugins.append(
            {
                "id": plugin_id,
                "name": package.name if package else plugin_id,
                "version": package.version if package else "0",
                "auto_load": False,
                "health": "OK",
                "visibility": policy.get("visibility", "public"),
            }
        )
    tool_by_plugin = service.tool_names_by_plugin
    tools = []
    for name in REGISTRY.names:
        definition = REGISTRY.get(name)
        source_plugin = next(
            (
                plugin_id
                for plugin_id, names in tool_by_plugin.items()
                if definition.name in names
            ),
            None,
        )
        tools.append(
            {
                "name": definition.name,
                "description": definition.description,
                "source_plugin": source_plugin,
                "permission": definition.permission.value,
                "risk": "high"
                if definition.permission.value == "ask_user"
                else "standard",
                "health": "OK"
                if source_plugin is None or source_plugin in plugin_ids
                else "UNAVAILABLE",
            }
        )
    return {
        "schema_version": "capability-manifest.v1",
        "user_id": user["sub"],
        "plugins": plugins,
        "tools": tools,
    }
