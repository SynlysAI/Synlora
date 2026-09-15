"""插件管理 API：列表、安装、更新配置（均限管理员）。

安全约定：状态回报永不包含敏感字段明文（只给 secrets_set 布尔映射）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


def get_plugin_service(request: Request):
    """从 app.state 取 PluginService。

    Args:
        request: FastAPI 请求。

    Returns:
        PluginService 实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "plugin_service", None)
    if service is None:
        raise HTTPException(503, "插件服务未就绪")
    return service


class PluginConfigBody(BaseModel):
    """插件配置请求体（字段由插件 schema 定义）。"""

    config: dict[str, Any] = {}
    # 待清除的敏感字段名（仅 PUT /config 使用；install 无"已存值"可清，传了也忽略）
    clear_secrets: list[str] = []


@router.get("")
async def list_plugins(user=Depends(require_admin),
                       service=Depends(get_plugin_service)) -> list[dict]:
    """全部可用插件及其安装/配置状态（不含敏感值）。"""
    return service.list_states()


@router.post("/{plugin_id}/install", status_code=201)
async def install_plugin(plugin_id: str, body: PluginConfigBody,
                         user=Depends(require_admin),
                         service=Depends(get_plugin_service)) -> dict:
    """安装插件（填写配置即安装：注册工具、挂技能、播种专家）。

    Raises:
        HTTPException: 插件不存在（404）、配置解密失败（409）、必填配置缺失（422）。
    """
    try:
        return await service.install(plugin_id, body.config)
    except KeyError:
        raise HTTPException(404, f"插件不存在: {plugin_id}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        # 配置解密失败（FERNET_KEY 轮换/缺失）：需到插件页重新填写凭证
        raise HTTPException(409, str(exc))


@router.put("/{plugin_id}/config")
async def update_plugin_config(plugin_id: str, body: PluginConfigBody,
                               user=Depends(require_admin),
                               service=Depends(get_plugin_service)) -> dict:
    """更新已安装插件的配置（敏感字段留空 = 保持原值，clear_secrets = 显式清除）。

    Raises:
        HTTPException: 插件不存在（404）、未安装（409）、解密失败（409）、必填缺失（422）。
    """
    try:
        return await service.update_config(plugin_id, body.config,
                                           clear_secrets=body.clear_secrets)
    except KeyError:
        raise HTTPException(404, f"插件不存在: {plugin_id}")
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(409 if detail.startswith("插件未安装") else 422, detail)
    except RuntimeError as exc:
        # 配置解密失败（FERNET_KEY 轮换/缺失）：需到插件页重新填写凭证
        raise HTTPException(409, str(exc))
