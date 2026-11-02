"""MCP Streamable HTTP 服务端（JSON-RPC 2.0 最小实现）。

支持 initialize / tools/list / tools/call 三个方法，足以被 ScienceDiscovery
的 custom MCP 注册与调用；鉴权为单 Bearer 令牌（注册进 SD 的 headers）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .registry import build_plugin_tool_table, make_tool_context, mcp_name
from .settings import GatewaySettings
from . import spectra_jobs

logger = logging.getLogger("mcp_gateway")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "ai4ms-professional-tools-gateway", "version": "1.0.0"}


def build_app(settings: GatewaySettings | None = None) -> FastAPI:
    """构建网关 FastAPI 应用（测试可注入配置）。"""
    settings = settings or GatewaySettings()
    plugin_tools = build_plugin_tool_table()

    spectra_tool_specs = {
        "spec_spectra_submit": {
            "description": "提交谱图异步解析任务（NMR/IR/GPC/Raman/LC-MS），返回 task_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["nmr", "ir", "gpc", "raman", "lcms"]},
                    "file_name": {"type": "string"},
                    "file_base64": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["kind", "file_name", "file_base64"],
            },
            "handler": spectra_jobs.spectra_submit,
        },
        "spec_spectra_status": {
            "description": "查询谱图解析任务状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["nmr", "ir", "gpc", "raman", "lcms"]},
                    "task_id": {"type": "string"},
                },
                "required": ["kind", "task_id"],
            },
            "handler": spectra_jobs.spectra_status,
        },
        "spec_spectra_result": {
            "description": "获取已完成的谱图解析任务结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["nmr", "ir", "gpc", "raman", "lcms"]},
                    "task_id": {"type": "string"},
                },
                "required": ["kind", "task_id"],
            },
            "handler": spectra_jobs.spectra_result,
        },
        "spec_spectra_cancel": {
            "description": "取消谱图解析任务",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["nmr", "ir", "gpc", "raman", "lcms"]},
                    "task_id": {"type": "string"},
                },
                "required": ["kind", "task_id"],
            },
            "handler": spectra_jobs.spectra_cancel,
        },
    }

    app = FastAPI(title="AI4MS Professional Tools MCP Gateway", docs_url=None, redoc_url=None)

    def _unauthorized() -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}}, status_code=401)

    def _tool_specs() -> list[dict]:
        specs = [
            {
                "name": name,
                "description": entry["definition"].description,
                "inputSchema": entry["definition"].parameters,
            }
            for name, entry in plugin_tools.items()
        ]
        specs.extend(
            {"name": name, "description": spec["description"], "inputSchema": spec["parameters"]}
            for name, spec in spectra_tool_specs.items()
        )
        return specs

    async def _call_tool(name: str, arguments: dict) -> dict:
        if name in plugin_tools:
            entry = plugin_tools[name]
            ctx = make_tool_context(settings.plugin_configs())
            try:
                result = await asyncio.wait_for(entry["fn"](ctx, arguments), timeout=entry["definition"].timeout_s + 5)
            except asyncio.TimeoutError:
                return {"content": [{"type": "text", "text": "ERROR: tool timeout"}], "isError": True}
            except Exception as exc:  # noqa: BLE001 —— 工具异常一律转 MCP 错误文本
                logger.warning("tool %s failed: %s", name, exc)
                return {"content": [{"type": "text", "text": f"ERROR: {exc}"}], "isError": True}
            text = result.content if result.ok else f"ERROR: {result.error or 'tool failed'}\n{result.content}"
            return {"content": [{"type": "text", "text": text}], "isError": not result.ok}
        if name in spectra_tool_specs:
            try:
                text = await asyncio.wait_for(
                    spectra_tool_specs[name]["handler"](arguments, settings.spec_agent), timeout=420
                )
            except asyncio.TimeoutError:
                return {"content": [{"type": "text", "text": "ERROR: job call timeout"}], "isError": True}
            except Exception as exc:  # noqa: BLE001
                logger.warning("job tool %s failed: %s", name, exc)
                return {"content": [{"type": "text", "text": f"ERROR: {exc}"}], "isError": True}
            is_error = str(text).startswith("ERROR")
            return {"content": [{"type": "text", "text": str(text)}], "isError": is_error}
        raise KeyError(name)

    @app.get("/healthz")
    async def healthz() -> dict:
        """探活：返回工具数量（不暴露配置）。"""
        return {"ok": True, "tools": len(_tool_specs())}

    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        """MCP JSON-RPC 入口。"""
        if settings.auth_token:
            authorization = request.headers.get("authorization", "")
            if authorization != f"Bearer {settings.auth_token}":
                return _unauthorized()
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                status_code=400,
            )
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method.startswith("notifications/"):
            return JSONResponse({}, status_code=202)
        try:
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                }
            elif method == "tools/list":
                result = {"tools": _tool_specs()}
            elif method == "tools/call":
                params = message.get("params") or {}
                result = await _call_tool(str(params.get("name") or ""), dict(params.get("arguments") or {}))
            else:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}
                )
        except KeyError:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unknown tool"}}
            )
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    return app


app = build_app()
