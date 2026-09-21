"""用户 MCP 扩展 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_user

router = APIRouter(prefix="/api/v1/me/mcps", tags=["mcp"])


class McpCreateBody(BaseModel):
    """Streamable HTTP MCP 创建请求。"""

    id: str
    name: str
    description: str = ""
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token: str = ""
    enabled: bool = True


class McpUpdateBody(BaseModel):
    """Streamable HTTP MCP 更新请求。"""

    name: str | None = None
    description: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    bearer_token: str | None = None
    enabled: bool | None = None


def _service(request: Request):
    """获取 MCP 服务，未就绪时返回 503。"""
    service = getattr(request.app.state, "mcp_service", None)
    if service is None:
        raise HTTPException(503, "MCP 服务未就绪")
    return service


@router.get("")
async def list_mcps(request: Request,
                    user=Depends(get_current_user)) -> list[dict]:
    """列出当前用户的 MCP 扩展。"""
    return await _service(request).list_for_user(user["sub"])


@router.post("", status_code=201)
async def create_mcp(request: Request, body: McpCreateBody,
                     user=Depends(get_current_user)) -> dict:
    """创建 Streamable HTTP MCP 扩展。"""
    try:
        return await _service(request).create(user["sub"], body.model_dump())
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(409 if "已存在" in message else 422, message) from exc


@router.get("/{mcp_id}")
async def get_mcp(request: Request, mcp_id: str,
                  user=Depends(get_current_user)) -> dict:
    """读取当前用户的 MCP 安全详情。"""
    item = await _service(request).get(user["sub"], mcp_id)
    if item is None:
        raise HTTPException(404, "MCP 不存在")
    return item


@router.patch("/{mcp_id}")
async def update_mcp(request: Request, mcp_id: str, body: McpUpdateBody,
                     user=Depends(get_current_user)) -> dict:
    """更新当前用户的 MCP 扩展。"""
    try:
        return await _service(request).update(
            user["sub"], mcp_id,
            body.model_dump(exclude_unset=True),
        )
    except KeyError as exc:
        raise HTTPException(404, "MCP 不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{mcp_id}")
async def delete_mcp(request: Request, mcp_id: str,
                     user=Depends(get_current_user)) -> dict:
    """删除当前用户的 MCP 扩展。"""
    if not await _service(request).delete(user["sub"], mcp_id):
        raise HTTPException(404, "MCP 不存在")
    return {"ok": True}


@router.post("/{mcp_id}/test")
async def test_mcp(request: Request, mcp_id: str,
                   user=Depends(get_current_user)) -> dict:
    """测试 MCP 连接并刷新工具列表。"""
    try:
        return await _service(request).test_connection(user["sub"], mcp_id)
    except KeyError as exc:
        raise HTTPException(404, "MCP 不存在") from exc
    except Exception as exc:
        raise HTTPException(502, f"MCP 连接失败: {exc}") from exc
