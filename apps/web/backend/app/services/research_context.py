"""Plane Research Context adapter and chain event write-back client."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator


class ResearchContextMetadata(BaseModel):
    """Metadata persisted on a Synlora session; never contains a token."""

    schema_version: str = "agent-context.v2"
    workspace_id: str
    workspace_slug: str
    research_project_id: str
    chain_node_id: str
    context_id: str
    context_hash: str
    visibility_scope: str
    expires_at: datetime
    allowed_knowledge_base_ids: list[str] = Field(default_factory=list)
    allowed_file_ids: list[str] = Field(default_factory=list)
    allowed_plugins: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    policy_id: str = ""
    policy_hash: str = ""
    scope_kind: str = "OWNER"
    scope_source: str = "chain_owner"
    policy_version: str = "research-agent-policy.v1"

    @model_validator(mode="after")
    def validate_v2_scope(self) -> "ResearchContextMetadata":
        """v2 必须携带可执行的工具白名单。"""
        if self.schema_version == "agent-context.v2" and not self.allowed_tools:
            raise ValueError("agent-context.v2 requires allowed_tools")
        return self


class ResearchContextError(Exception):
    """Raised when Plane refuses or mismatches a research context token."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ResearchContextAdapter:
    """Validate an opaque Plane context token against its signed server scope."""

    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        """Create an adapter.

        Args:
            base_url: Plane base URL, for example ``http://plane:8000``.
            timeout_seconds: Context validation timeout.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def validate(
        self,
        *,
        token: str,
        expected: ResearchContextMetadata,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Call Plane and ensure the token cannot widen the requested scope.

        Args:
            token: Opaque short-lived token; never persisted or logged.
            expected: Metadata supplied by the Plane BFF.
            transport: Optional test transport.

        Raises:
            ResearchContextError: Plane is unavailable, token is invalid, or any
                scope field does not match.
        """
        if not self._base_url:
            raise ResearchContextError(503, "Plane Research Context is not configured")
        url = (
            f"{self._base_url}/api/research/workspaces/"
            f"{expected.workspace_slug}/context/"
        )
        headers = {
            "X-Research-Context-Token": token,
            "X-Research-Context-Hash": expected.context_hash,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, transport=transport) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ResearchContextError(503, "Plane Research Context is unavailable") from exc
        if response.status_code in (401, 403):
            raise ResearchContextError(response.status_code, "Research context token is invalid or expired")
        if response.status_code >= 400:
            raise ResearchContextError(502, "Plane Research Context validation failed")
        payload = response.json()
        actual_context = payload.get("context") or {}
        actual_scope = payload.get("scope") or {}
        expected_values = {
            "context_id": expected.context_id,
            "context_hash": expected.context_hash,
            "workspace_id": expected.workspace_id,
            "research_project_id": expected.research_project_id,
            "chain_node_id": expected.chain_node_id,
            "visibility_scope": expected.visibility_scope,
            "allowed_knowledge_base_ids": expected.allowed_knowledge_base_ids,
            "allowed_file_ids": expected.allowed_file_ids,
            "allowed_plugins": expected.allowed_plugins,
            "allowed_tools": expected.allowed_tools,
            "policy_id": expected.policy_id,
            "policy_hash": expected.policy_hash,
            "scope_kind": expected.scope_kind,
            "scope_source": expected.scope_source,
            "policy_version": expected.policy_version,
        }
        actual_values = {
            "context_id": actual_context.get("context_id"),
            "context_hash": actual_context.get("context_hash"),
            "workspace_id": (payload.get("workspace") or {}).get("id"),
            "research_project_id": actual_scope.get("project_id"),
            "chain_node_id": actual_context.get("chain_node_id"),
            "visibility_scope": actual_context.get("visibility_scope"),
            "allowed_knowledge_base_ids": actual_context.get("allowed_knowledge_base_ids") or [],
            "allowed_file_ids": actual_context.get("allowed_file_ids") or [],
            "allowed_plugins": actual_context.get("allowed_plugins") or [],
            "allowed_tools": actual_context.get("allowed_tools") or [],
            "policy_id": actual_context.get("policy_id") or "",
            "policy_hash": actual_context.get("policy_hash") or "",
            "scope_kind": actual_context.get("scope_kind") or "OWNER",
            "scope_source": actual_context.get("scope_source") or "chain_owner",
            "policy_version": actual_context.get("policy_version") or "research-agent-policy.v1",
        }
        if expected_values != actual_values:
            raise ResearchContextError(403, "Research context scope mismatch")
        expires_at = actual_context.get("expires_at")
        try:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ResearchContextError(502, "Research context expiry is invalid") from exc
        if expires <= datetime.now(UTC):
            raise ResearchContextError(401, "Research context token is expired")


class PlaneResearchClient:
    """Backend-only client for writing immutable events back to Plane."""

    def __init__(self, base_url: str, api_token: str, timeout_seconds: float = 3.0) -> None:
        """Create a Plane write-back client.

        Args:
            base_url: Plane base URL.
            api_token: Backend service token; never exposed to the browser.
            timeout_seconds: Request timeout.
        """
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds

    async def write_chain_event(
        self,
        *,
        workspace_slug: str,
        chain_node_id: str,
        event_type: str,
        summary: str,
        request_id: str | None = None,
        event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict[str, Any]:
        """Append one normalized Agent fact to a Plane Chain node.

        Args:
            workspace_slug: Plane Workspace slug.
            chain_node_id: Plane Chain Node id.
            event_type: Research event taxonomy value.
            summary: Safe event summary without source bodies.
            request_id: Idempotency key; generated when omitted.
            event_id: Stable event id; generated when omitted.
            metadata: Optional references and counts.
            transport: Optional test transport.

        Returns:
            Plane response payload.

        Raises:
            ResearchContextError: Plane rejects the event or is unavailable.
        """
        if not self._base_url or not self._api_token:
            raise ResearchContextError(503, "Plane event write-back is not configured")
        request_id = request_id or f"synlora-{uuid.uuid4().hex}"
        event_id = event_id or f"event-{uuid.uuid4().hex}"
        url = f"{self._base_url}/api/research/workspaces/{workspace_slug}/agent/chain-events/"
        payload = {
            "request_id": request_id,
            "event_id": event_id,
            "chain_node_id": chain_node_id,
            "event_type": event_type,
            "summary": summary,
            "refs": metadata or [],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, transport=transport) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"X-API-Key": self._api_token},
                )
        except httpx.HTTPError as exc:
            raise ResearchContextError(503, "Plane event write-back is unavailable") from exc
        if response.status_code >= 400:
            raise ResearchContextError(502, "Plane event write-back failed")
        return response.json()
