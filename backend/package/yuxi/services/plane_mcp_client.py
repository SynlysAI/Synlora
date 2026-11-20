"""Plane 统一 MCP 客户端：按 run 短期凭证调用 Plane 只读工具面。

Plane 在 Agent Dock 会话创建时签发 `pum_` 前缀短期 token（60 分钟），经
`X-Plane-MCP-Token` 请求头随消息传入；本方不落库，仅写入带 TTL 的 Redis
（会话级键，随每轮消息刷新）。工具面只暴露只读工具（`PLANE_READ_ONLY_TOOLS`
前缀集合）——写工具不经审批通道放行，直接不装配。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import create_model
from pydantic.fields import FieldInfo

from yuxi.services.run_queue_service import get_redis_client
from yuxi.utils.logging_config import logger

MCP_PROTOCOL_VERSION = "2025-03-26"
CALL_TIMEOUT_SECONDS = 120.0
TOKEN_TTL_SECONDS = 90 * 60
# 只读工具名前缀：与老平台 Permission.ALLOW 集合对齐；其余（写类）不装配
PLANE_READ_ONLY_PREFIXES = (
    "plane_list_",
    "plane_get_",
    "plane_read_",
)


class PlaneMcpError(Exception):
    """Plane MCP 调用失败。"""


def _token_key(thread_id: str) -> str:
    return f"plane:mcp:{thread_id}"


async def store_plane_mcp_token(thread_id: str, token: str) -> None:
    """缓存会话级 MCP 短期凭证（TTL 覆盖 Plane 侧 60 分钟有效期）。"""
    if not token:
        return
    redis = await get_redis_client()
    await redis.set(_token_key(thread_id), token, ex=TOKEN_TTL_SECONDS)


async def load_plane_mcp_token(thread_id: str) -> str:
    """读取会话当前 MCP 凭证（无或过期返回空串）。"""
    redis = await get_redis_client()
    raw = await redis.get(_token_key(thread_id))
    if raw is None:
        return ""
    return raw.decode() if isinstance(raw, bytes) else str(raw)


class PlaneUnifiedMcpClient:
    """Plane `/api/mcp/` 无状态 JSON-RPC 客户端。"""

    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url = url
        self.token = token
        self.timeout = timeout
        self._request_id = 0

    def _post(self, method: str, params: dict[str, Any] | None = None) -> httpx.Request:
        self._request_id += 1
        return httpx.Request(
            "POST",
            self.url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}},
        )

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.send(self._post(method, params))
        if response.status_code == 401:
            raise PlaneMcpError("Plane MCP 凭证无效或已过期")
        if response.status_code >= 400:
            raise PlaneMcpError(f"Plane MCP 服务异常: HTTP {response.status_code}")
        body = _decode_mcp_body(response)
        if body.get("error"):
            raise PlaneMcpError(f"Plane MCP 调用失败: {body['error']}")
        return body.get("result") or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """带 cursor 分页枚举全部工具定义。"""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._rpc("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise PlaneMcpError("Plane MCP tools/list 分页过深")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用工具并返回文本结果（isError 时抛错）。"""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise PlaneMcpError(f"Plane MCP 工具 {name} 执行失败: {_result_text(result)}")
        return _result_text(result)


def _decode_mcp_body(response: httpx.Response) -> dict[str, Any]:
    """兼容 JSON 与 SSE 两种 MCP 响应封装。"""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except ValueError:
                    continue
        raise PlaneMcpError("Plane MCP SSE 响应不含有效数据")
    try:
        return response.json()
    except ValueError as exc:
        raise PlaneMcpError("Plane MCP 响应不是合法 JSON") from exc


def _result_text(result: dict[str, Any]) -> str:
    parts = [
        item.get("text", "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def _is_read_only(name: str) -> bool:
    return name.startswith(PLANE_READ_ONLY_PREFIXES)


def _build_args_schema(tool_def: dict[str, Any]) -> type | None:
    """把 MCP inputSchema 转换为 pydantic 模型（基础类型；复杂 schema 返回 None 走 infer）。"""
    schema = tool_def.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return None
    type_map = {"string": str, "integer": int, "number": float, "boolean": bool}
    fields: dict[str, tuple] = {}
    required = set(schema.get("required") or [])
    for key, prop in properties.items():
        if not isinstance(prop, dict) or prop.get("type") not in type_map:
            return None
        annotation = type_map[prop["type"]]
        default = ... if key in required else prop.get("default", None)
        fields[key] = (annotation, FieldInfo(default=default, description=str(prop.get("description") or "")[:200]))
    try:
        return create_model(f"PlaneMcp_{tool_def['name']}", **fields)
    except (TypeError, ValueError):
        return None


async def build_plane_runtime_tools(thread_id: str, plane_mcp: dict[str, Any]) -> list[StructuredTool]:
    """按会话凭证与授权范围装配只读 Plane 工具（失败降级为空集并告警）。"""
    token = await load_plane_mcp_token(thread_id)
    if not token:
        logger.warning(f"plane mcp token missing, tools not loaded: thread={thread_id}")
        return []
    client = PlaneUnifiedMcpClient(
        url=str(plane_mcp.get("url") or ""),
        token=token,
        timeout=CALL_TIMEOUT_SECONDS,
    )
    allowed = set(plane_mcp.get("allowed_tools") or [])
    try:
        tool_defs = await client.list_tools()
    except PlaneMcpError as exc:
        logger.warning(f"plane mcp tools/list failed, tools not loaded: {exc}")
        return []

    tools: list[StructuredTool] = []
    for tool_def in tool_defs:
        name = str(tool_def.get("name") or "")
        if not _is_read_only(name) or (allowed and name not in allowed):
            continue
        tools.append(_make_tool(client, tool_def))
    return tools


def _make_tool(client: PlaneUnifiedMcpClient, tool_def: dict[str, Any]) -> StructuredTool:
    name = str(tool_def.get("name") or "")
    description = str(tool_def.get("description") or "")[:1000]

    async def _call(**kwargs: Any) -> str:
        try:
            return await client.call_tool(name, kwargs)
        except PlaneMcpError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    return StructuredTool.from_function(
        name=name,
        coroutine=_call,
        description=description,
        args_schema=_build_args_schema(tool_def),
        infer_schema=True,
    )
