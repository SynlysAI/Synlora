"""公共 MCP 管理端点（require_admin；写入数据目录覆盖层 + catalog 热重载）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.catalog.items import CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.mcp_admin import delete_public, is_overlaid, write_public

router = APIRouter(prefix="/api/v1/admin/mcp", tags=["admin-mcp"])


class McpAdminBody(BaseModel):
    """公共 MCP 新建/编辑请求体（与 manifest 字段一一对应）。"""

    id: str
    name: str
    description: str = ""
    transport: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token: str = ""
    timeout_s: float = 60.0


async def _reload_catalog(request: Request) -> None:
    """重扫 catalog 并替换能力服务的目录视图（公共 MCP 写入即生效）。

    只重建 CatalogService（mcp 条目消费方均经它取数）；插件/专家包对象
    不受 mcp 写入影响，维持原引用。
    """
    settings = request.app.state.settings
    index = scan_catalog(catalog_roots(settings))
    request.app.state.capability_service.catalog = CatalogService(index=index)


def _manifest_row(request: Request, pkg: Any) -> dict:
    """管理员视角的一行 manifest 视图（不含 policy/状态，调用方各自补）。"""
    return {
        "id": pkg.id, "name": pkg.name, "description": pkg.description,
        "transport": pkg.transport,
        "command": pkg.command, "args": list(pkg.args), "cwd": pkg.cwd,
        "env": dict(pkg.env), "url": pkg.url,
        "header_names": sorted(pkg.headers),
        "bearer_token_set": bool(pkg.bearer_token),
        "timeout_s": pkg.timeout_s,
        "overlaid": is_overlaid(request.app.state.settings, pkg.id),
    }


def _get_pkg(request: Request, mcp_id: str) -> Any:
    """取公共 MCP 包（不存在 404）。

    Raises:
        HTTPException: 条目不存在（404）。
    """
    pkg = request.app.state.capability_service.catalog.mcps.get(mcp_id)
    if pkg is None:
        raise HTTPException(404, f"MCP 不存在: {mcp_id}")
    return pkg


@router.get("")
async def list_public_mcps(request: Request,
                           user=Depends(require_admin)) -> list[dict]:
    """全部公共 MCP（含 hidden；附 policy 与探测状态）。"""
    capability = request.app.state.capability_service
    rows = []
    for pkg in sorted(capability.catalog.mcps.values(), key=lambda p: p.id):
        pol = await capability.policy.get("mcp", pkg.id)
        status = request.app.state.mcp_service.public_status(pkg.id)
        rows.append({**_manifest_row(request, pkg),
                     "visibility": pol["visibility"],
                     "default_enabled": pol["default_enabled"],
                     "status": status["status"], "last_error": status["last_error"],
                     "tool_count": status["tool_count"]})
    return rows


@router.post("", status_code=201)
async def create_public_mcp(request: Request, body: McpAdminBody,
                            user=Depends(require_admin)) -> dict:
    """新增公共 MCP（写数据目录公共层，写入即热重载生效）。

    Raises:
        HTTPException: id 已存在（409）、manifest 非法（422）。
    """
    if body.id in request.app.state.capability_service.catalog.mcps:
        raise HTTPException(409, f"MCP 已存在: {body.id}")
    try:
        write_public(request.app.state.settings, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _reload_catalog(request)
    return {"ok": True, "id": body.id}


@router.put("/{mcp_id}")
async def update_public_mcp(request: Request, mcp_id: str, body: McpAdminBody,
                            user=Depends(require_admin)) -> dict:
    """编辑公共 MCP（内置条目 = 写同名覆盖；id 不可改）。

    Raises:
        HTTPException: 条目不存在（404）、id 被改动（422）、manifest 非法（422）。
    """
    _get_pkg(request, mcp_id)
    if body.id != mcp_id:
        raise HTTPException(422, "id 不可修改")
    try:
        write_public(request.app.state.settings, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _reload_catalog(request)
    return {"ok": True, "id": mcp_id}


@router.delete("/{mcp_id}")
async def delete_public_mcp(request: Request, mcp_id: str,
                            user=Depends(require_admin)) -> dict:
    """删除公共 MCP（仅数据目录层；仓库内置请用 hidden 下线）。

    Raises:
        HTTPException: 条目不存在（404）、无覆盖副本不可删（409）。
    """
    _get_pkg(request, mcp_id)
    if not is_overlaid(request.app.state.settings, mcp_id):
        raise HTTPException(409, "仓库内置条目不可删除，请用「隐藏」下线")
    delete_public(request.app.state.settings, mcp_id)
    await _reload_catalog(request)
    return {"ok": True}


@router.post("/{mcp_id}/reset")
async def reset_public_mcp(request: Request, mcp_id: str,
                           user=Depends(require_admin)) -> dict:
    """恢复默认：删除数据目录覆盖，回退仓库内置版。

    Raises:
        HTTPException: 无覆盖副本（409）。
    """
    if not is_overlaid(request.app.state.settings, mcp_id):
        raise HTTPException(409, "该条目没有覆盖副本")
    delete_public(request.app.state.settings, mcp_id)
    await _reload_catalog(request)
    return {"ok": True}


@router.post("/{mcp_id}/test")
async def test_public_mcp(request: Request, mcp_id: str,
                          user=Depends(require_admin)) -> dict:
    """测试公共 MCP 连接（stdio 真握手 / http initialize），结果写状态缓存。

    Raises:
        HTTPException: 条目不存在（404）、连接失败（502）。
    """
    _get_pkg(request, mcp_id)
    try:
        return await request.app.state.mcp_service.test_public_connection(mcp_id)
    except Exception as exc:
        raise HTTPException(502, f"MCP 连接失败: {exc}") from exc
