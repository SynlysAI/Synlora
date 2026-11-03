"""Ephemeral Plane unified MCP tool assembly for Agent Dock runs."""
from __future__ import annotations

import json
from typing import Any

from synlys_harness import Permission, ToolDefinition, ToolRegistry, ToolResult

from app.services.mcp_service import PLANE_MCP_PROTOCOL_VERSION


READ_ONLY_TOOLS = frozenset(
    {
        "plane_list_workspaces",
        "plane_list_containers",
        "plane_list_nodes",
        "plane_list_tasks",
        "plane_get_context",
        "plane_read_resource",
        "plane_get_write_result",
        "plane_get_research_operation",
        "plane_list_research_operations",
    }
)


class PlaneMcpUnavailable(Exception):
    """Plane unified MCP 初始化或工具发现失败。"""


def _tool_result(result: dict[str, Any], connection_id: str) -> ToolResult:
    """Convert an MCP result to a bounded harness result without credentials."""
    parts = result.get("content") or []
    text = "\n".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    structured = result.get("structuredContent")
    error_code = ""
    if isinstance(structured, dict) and structured.get("error_code"):
        error_code = str(structured["error_code"])
    return ToolResult(
        ok=result.get("isError") is not True,
        content=text or json.dumps(result, ensure_ascii=False, default=str),
        data={"mcp_id": "plane", "connection_id": connection_id},
        error=error_code or ("plane_mcp_tool_error" if result.get("isError") is True else None),
    )


async def assemble_plane_mcp_tools(
    mcp_service: Any,
    *,
    metadata: dict[str, Any],
    token: str,
    allowed_tools: list[str] | None,
    registry: ToolRegistry,
) -> list[str]:
    """Register Plane unified MCP tools for exactly one run.

    Args:
        mcp_service: Shared MCP client service.
        metadata: Token-free Plane MCP descriptor from the BFF.
        token: Short-lived Bearer token; retained only in this call's closures.
        allowed_tools: Plane signed capability intersection.
        registry: Per-run tool registry.

    Returns:
        Registered tool names.
    """
    url = str(metadata.get("url") or "")
    connection_id = str(metadata.get("connection_id") or "")
    if not url.startswith(("http://", "https://")) or not token or not connection_id:
        return []
    config = {
        "id": "plane",
        "name": "Plane unified MCP",
        "url": url,
        "headers": {},
        "bearer_token": token,
        "protocol_version": PLANE_MCP_PROTOCOL_VERSION,
    }
    authorized = set(allowed_tools or [])
    registered: list[str] = []
    try:
        tools = await mcp_service.discover_inline_tools(config)
    except Exception as exc:
        raise PlaneMcpUnavailable("Plane unified MCP is unavailable") from exc
    for remote_tool in tools:
        remote_name = str(remote_tool.get("name") or "")
        if not remote_name or remote_name not in authorized or registry.find(remote_name) is not None:
            continue

        async def execute_tool(_ctx, args, *, tool_name=remote_name):
            try:
                result = await mcp_service.call_inline_tool(config, tool_name, args)
                return _tool_result(result, connection_id)
            except Exception as exc:
                return ToolResult(
                    ok=False,
                    content=f"Plane MCP 调用失败: {exc}",
                    error="plane_mcp_unavailable",
                )

        registry.register(
            ToolDefinition(
                name=remote_name,
                description=str(remote_tool.get("description") or remote_name),
                parameters=remote_tool.get("input_schema") or {"type": "object", "properties": {}},
                execute=execute_tool,
                timeout_s=120.0,
                permission=Permission.ALLOW if remote_name in READ_ONLY_TOOLS else Permission.ASK_USER,
            )
        )
        registered.append(remote_name)
    if not registered:
        raise PlaneMcpUnavailable("Plane unified MCP returned no authorized tools")
    return registered
