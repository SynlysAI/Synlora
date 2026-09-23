"""平台工具清单端点：供专家/助手编辑器实取注册表工具列表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user
from app.services.tool_registry import REGISTRY

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """列出注册表工具（内置 + 宿主；插件工具不进候选）。

    与白名单校验（deps.validate_tool_whitelist）同源同一个 REGISTRY 实例，
    保证界面可选集与 422 校验口径一致。插件工具随会话级插件开关装配
    （默认全关，勾选不能放大可见性），作为助手/专家白名单候选只会产生
    死引用或反被排他白名单砍掉会话插件能力，故按插件归属整体排除。

    Args:
        request: FastAPI 请求（取能力服务读插件工具映射）。
        user: 当前用户（仅要求登录，清单不分角色）。

    Returns:
        工具定义列表（name/description，按名称排序）。
    """
    plugin_tools: set[str] = set()
    caps = getattr(request.app.state, "capability_service", None)
    if caps is not None:
        # 活引用实时求值：运行期新装插件也能立即反映
        plugin_tools = {
            name for names in caps.tool_names_by_plugin.values() for name in names
        }
    return [
        {"name": definition.name, "description": definition.description}
        for name in REGISTRY.names
        if (definition := REGISTRY.find(name)) is not None
        and name not in plugin_tools
    ]
