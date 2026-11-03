"""Plane Research Context adapter and chain event write-back client."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, model_validator

ResearchContextTargetType = Literal[
    "WORKSPACE",
    "PROJECT",
    "RESEARCH_CHAIN",
    "RESEARCH_CHAIN_NODE",
]


class ResearchContextTarget(BaseModel):
    """Plane 授权对象的窄化描述；只含 ID，不含业务正文。"""

    type: ResearchContextTargetType
    workspace_id: str = ""
    workspace_slug: str = ""
    project_id: str | None = None
    research_chain_id: str | None = None
    chain_node_id: str | None = None


class ResearchContextSubject(BaseModel):
    """发起本轮 Agent 的 Plane 用户与授权来源。"""

    plane_user_id: str = ""
    delegated_subject: str = ""
    scope_kind: str = "OWNER"
    scope_source: str = "chain_owner"


class ResearchContextPolicy(BaseModel):
    """Plane 签发的能力硬上界。"""

    policy_id: str = ""
    policy_hash: str = ""
    policy_version: str = "research-agent-policy.v1"
    visibility_scope: str = "PRIVATE"
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_plugins: list[str] = Field(default_factory=list)
    allowed_mcp_ids: list[str] = Field(default_factory=list)
    allowed_knowledge_base_ids: list[str] = Field(default_factory=list)
    allowed_file_ids: list[str] = Field(default_factory=list)


class ResearchContextManifest(BaseModel):
    """进入系统提示词的 bounded 摘要与资源引用。"""

    version: int = 1
    hash: str
    summary: str = ""
    resources: list[dict[str, Any]] = Field(default_factory=list)


class ResearchContextMetadata(BaseModel):
    """Metadata persisted on a Synlora session; never contains a token."""

    schema_version: str = "agent-context.v2"
    workspace_id: str = ""
    workspace_slug: str = ""
    research_project_id: str | None = None
    chain_node_id: str | None = None
    context_id: str
    context_hash: str
    visibility_scope: str = "PRIVATE"
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
    issuer: str = ""
    plane_user_id: str = ""
    delegated_subject: str = ""
    allowed_mcp_ids: list[str] = Field(default_factory=list)
    target: ResearchContextTarget | None = None
    subject: ResearchContextSubject | None = None
    policy: ResearchContextPolicy | None = None
    manifest: ResearchContextManifest | None = None

    @model_validator(mode="after")
    def normalize_context_shape(self) -> "ResearchContextMetadata":
        """归一化 v2/v3 输入，并同步既有扁平字段投影。"""
        self.issuer = self.issuer.rstrip("/")
        if self.schema_version == "agent-context.v2":
            if not self.workspace_id or not self.workspace_slug or not self.research_project_id:
                raise ValueError("agent-context.v2 requires workspace and project scope")
            if not self.chain_node_id:
                raise ValueError("agent-context.v2 requires chain_node_id")
            if not self.allowed_tools:
                raise ValueError("agent-context.v2 requires allowed_tools")
            self.target = ResearchContextTarget(
                type="RESEARCH_CHAIN_NODE",
                workspace_id=self.workspace_id,
                workspace_slug=self.workspace_slug,
                project_id=self.research_project_id,
                chain_node_id=self.chain_node_id,
            )
            self.subject = ResearchContextSubject(
                plane_user_id=self.plane_user_id,
                delegated_subject=self.delegated_subject,
                scope_kind=self.scope_kind,
                scope_source=self.scope_source,
            )
            self.policy = ResearchContextPolicy(
                policy_id=self.policy_id,
                policy_hash=self.policy_hash,
                policy_version=self.policy_version,
                visibility_scope=self.visibility_scope,
                allowed_tools=self.allowed_tools,
                allowed_plugins=self.allowed_plugins,
                allowed_mcp_ids=self.allowed_mcp_ids,
                allowed_knowledge_base_ids=self.allowed_knowledge_base_ids,
                allowed_file_ids=self.allowed_file_ids,
            )
            return self

        if self.schema_version != "agent-context.v3":
            raise ValueError("unsupported research context schema")
        if self.target is None or self.policy is None:
            raise ValueError("agent-context.v3 requires target and policy")
        if not self.target.workspace_id or not self.target.workspace_slug:
            raise ValueError("agent-context.v3 requires workspace target fields")
        if self.target.type == "PROJECT" and not self.target.project_id:
            raise ValueError("PROJECT context requires project_id")
        if self.target.type == "RESEARCH_CHAIN" and not self.target.research_chain_id:
            raise ValueError("RESEARCH_CHAIN context requires research_chain_id")
        if self.target.type == "RESEARCH_CHAIN_NODE" and not self.target.chain_node_id:
            raise ValueError("RESEARCH_CHAIN_NODE context requires chain_node_id")

        policy = self.policy
        self.workspace_id = self.target.workspace_id
        self.workspace_slug = self.target.workspace_slug
        self.research_project_id = self.target.project_id
        self.chain_node_id = self.target.chain_node_id
        self.allowed_tools = policy.allowed_tools
        self.allowed_plugins = policy.allowed_plugins
        self.allowed_mcp_ids = policy.allowed_mcp_ids
        self.allowed_knowledge_base_ids = policy.allowed_knowledge_base_ids
        self.allowed_file_ids = policy.allowed_file_ids
        self.policy_id = policy.policy_id
        self.policy_hash = policy.policy_hash
        self.policy_version = policy.policy_version
        self.visibility_scope = policy.visibility_scope
        if self.subject is not None:
            self.plane_user_id = self.subject.plane_user_id
            self.delegated_subject = self.subject.delegated_subject
            self.scope_kind = self.subject.scope_kind
            self.scope_source = self.subject.scope_source
        return self

    def normalized_projection(self) -> dict[str, Any]:
        """返回 Context token 校验使用的稳定比较结构。"""
        if self.target is None or self.policy is None:
            raise ValueError("research context is not normalized")
        return {
            "schema_version": self.schema_version,
            "issuer": self.issuer,
            "context_id": self.context_id,
            "context_hash": self.context_hash,
            "target": self.target.model_dump(mode="json"),
            "subject": self.subject.model_dump(mode="json") if self.subject else {},
            "policy": self.policy.model_dump(mode="json"),
            "manifest": self.manifest.model_dump(mode="json") if self.manifest else None,
        }


class ResearchContextError(Exception):
    """Raised when Plane refuses or mismatches a research context token."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ResearchContextAdapter:
    """Validate an opaque Plane context token against its signed server scope."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 3.0,
        allowed_issuers: str | list[str] | None = None,
    ) -> None:
        """Create an adapter.

        Args:
            base_url: Plane base URL, for example ``http://plane:8000``.
            timeout_seconds: Context validation timeout.
            allowed_issuers: Comma-separated string or list of allowed Plane
                issuers; omitted values allow only ``base_url``.
        """
        self._base_url = self._normalize_base_url(base_url)
        self._timeout_seconds = timeout_seconds
        if allowed_issuers is None:
            raw_issuers = [base_url]
        elif isinstance(allowed_issuers, str):
            raw_issuers = [item for item in allowed_issuers.split(",") if item.strip()]
        else:
            raw_issuers = list(allowed_issuers)
        self._allowed_issuers = {
            issuer
            for issuer in (self._normalize_base_url(item) for item in raw_issuers)
            if issuer
        }

    @staticmethod
    def _normalize_base_url(raw_url: str) -> str:
        """Normalize an HTTP(S) base URL for exact issuer allowlist matching.

        Args:
            raw_url: Configured Plane base URL.

        Returns:
            Normalized URL without trailing slash, query, fragment, or credentials.

        Raises:
            ValueError: The value is not an HTTP(S) base URL.
        """
        value = str(raw_url or "").strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Plane issuer must be an HTTP(S) URL")
        if "@" in parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Plane issuer must not contain credentials, query, or fragment")
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
        )

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
        try:
            issuer = self._normalize_base_url(getattr(expected, "issuer", "") or "")
        except ValueError as exc:
            raise ResearchContextError(403, "Research context issuer is invalid") from exc
        if not issuer:
            issuer = self._base_url
        if issuer not in self._allowed_issuers:
            raise ResearchContextError(403, "Research context issuer is not allowed")
        url = f"{issuer}/api/research/workspaces/{expected.workspace_slug}/context/"
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
        if expected.schema_version == "agent-context.v3":
            expected_projection = expected.normalized_projection()
            # Plane returns only the manifest identity (version/hash), never the
            # summary body, so validation does not turn into a content export.
            expected_manifest = expected_projection["manifest"]
            actual_manifest = actual_context.get("manifest")
            if expected_manifest is not None:
                expected_projection["manifest"] = {
                    "version": expected_manifest["version"],
                    "hash": expected_manifest["hash"],
                }
                if isinstance(actual_manifest, dict):
                    actual_manifest = {
                        "version": actual_manifest.get("version"),
                        "hash": actual_manifest.get("hash"),
                    }
            actual_projection = {
                "schema_version": actual_context.get("schema_version"),
                "issuer": str(actual_context.get("issuer") or ""),
                "context_id": actual_context.get("context_id"),
                "context_hash": actual_context.get("context_hash"),
                "target": actual_context.get("target"),
                "subject": actual_context.get("subject") or {},
                "policy": actual_context.get("policy"),
                "manifest": actual_manifest,
            }
            if expected_projection != actual_projection:
                raise ResearchContextError(403, "Research context scope mismatch")
        else:
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
