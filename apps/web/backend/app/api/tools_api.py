"""平台工具清单端点：供专家/助手编辑器实取注册表工具列表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.services.tool_registry import REGISTRY

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


@router.get("")
async def list_tools(user=Depends(get_current_user)) -> list[dict]:
    """列出注册表全部已注册工具（内置 + 宿主 + 已装插件）。

    与白名单校验（deps.validate_tool_whitelist）同源同一个 REGISTRY 实例，
    保证界面可选集与 422 校验口径一致。

    Args:
        user: 当前用户（仅要求登录，清单不分角色）。

    Returns:
        工具定义列表（name/description，按名称排序）。
    """
    return [
        {"name": definition.name, "description": definition.description}
        for name in REGISTRY.names
        if (definition := REGISTRY.find(name)) is not None
    ]
