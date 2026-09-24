"""Plane Research Agent delegation and capability manifest contracts."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.api.deps import get_current_user, get_settings
from app.core.auth import mint_ai4ms_token
from app.core.settings import Settings
from app.services.tool_registry import REGISTRY

router = APIRouter(prefix="/api/v1/research", tags=["research"])


class DelegatedTokenBody(BaseModel):
    """plane-delegated-auth.v1 request."""

    schema_version: str
    account_link_id: str
    workspace_id: str
    plane_user_id: str
    external_subject: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        """Only the frozen Plane contract is accepted."""
        if value != "plane-delegated-auth.v1":
            raise ValueError("unsupported delegated auth schema")
        return value


@router.post("/delegated-token")
async def delegated_token(
    body: DelegatedTokenBody,
    request: Request,
    settings=Depends(get_settings),
) -> dict:
    """Exchange a Plane service credential for a short-lived Synlora user token."""
    service_token = settings.plane_service_token
    supplied = request.headers.get("Authorization", "")
    if not service_token or not supplied.startswith("Bearer ") or not hmac.compare_digest(
        supplied[7:], service_token
    ):
        raise HTTPException(403, "Plane delegated auth is not configured or invalid")
    token = mint_ai4ms_token(
        body.external_subject,
        body.external_subject,
        "user",
        settings,
        ttl_hours=1,
    )
    return {
        "schema_version": "plane-delegated-auth.v1",
        "token": token,
        "subject": body.external_subject,
        "account_link_id": body.account_link_id,
        "expires_in": 3600,
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
            (plugin_id for plugin_id, names in tool_by_plugin.items() if definition.name in names),
            None,
        )
        tools.append(
            {
                "name": definition.name,
                "description": definition.description,
                "source_plugin": source_plugin,
                "permission": definition.permission.value,
                "risk": "high" if definition.permission.value == "ask_user" else "standard",
                "health": "OK" if source_plugin is None or source_plugin in plugin_ids else "UNAVAILABLE",
            }
        )
    return {
        "schema_version": "capability-manifest.v1",
        "user_id": user["sub"],
        "plugins": plugins,
        "tools": tools,
    }
