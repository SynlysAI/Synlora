"""能力目录 API：用户侧市场（列表/安装/卸载）+ 管理员策略（列表/配置）。

普通用户视角的列表与安装一律经过可见性判定：hidden 条目对用户不存在；
public + 非默认启用 的条目要安装后才进入该用户的能力集。平台缺省即后者：
内置条目在市场可见（可被安装），但需安装后才可用。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin
from app.catalog.items import KINDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def get_capability_service(request: Request):
    """从 app.state 取 CapabilityService。

    Args:
        request: FastAPI 请求。

    Returns:
        CapabilityService 实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "capability_service", None)
    if service is None:
        raise HTTPException(503, "能力目录未就绪")
    return service


async def _require_installable(service, user_id: str, kind: str, item_id: str) -> None:
    """前置判定：条目不可安装则 404（新旧安装路径共用，保证文案只有一处）。

    Args:
        service: 能力服务。
        user_id: 用户 sub。
        kind: 条目类型。
        item_id: 条目 id。

    Raises:
        HTTPException: 条目不可安装（404）。
    """
    if not await service.can_install(user_id, kind, item_id):
        raise HTTPException(404, f"条目不可安装: {kind}:{item_id}")


async def _install_core(request: Request, service, user_id: str, kind: str,
                        item_id: str) -> None:
    """落安装记录并挂载插件（调用方须已先过 `_require_installable`）。

    Args:
        request: FastAPI 请求（取插件服务）。
        service: 能力服务。
        user_id: 用户 sub。
        kind: 条目类型。
        item_id: 条目 id。
    """
    await service.installs.install(user_id, kind, item_id)
    if kind == "plugin":
        # 安装即挂载（进程级能力可用性）：插件包的工具/技能根不依赖"管理员是否
        # 公共安装过"，否则用户自装后仍用不了（可见性由 CapabilityService 另算）
        plugin_service = getattr(request.app.state, "plugin_service", None)
        if plugin_service is not None:
            plugin_service.ensure_attached(item_id)
        else:
            logger.warning("插件服务未就绪，用户 %s 安装 %s 后未挂载", user_id, item_id)


class PolicyBody(BaseModel):
    """管理员策略请求体。

    缺省与平台缺省一致（public + 非默认启用）：只带 visibility 的 PUT
    不应意外写成"默认启用"。
    """

    visibility: str = "public"
    default_enabled: bool = False


class InstallBody(BaseModel):
    """安装请求体（插件可带个人配置；其它类型忽略 config）。"""

    config: dict[str, Any] = {}


class CapabilitySwitchBody(BaseModel):
    """能力开关请求体（两个字段都可选，缺省表示不改）。"""

    installed: bool | None = None
    enabled: bool | None = None


@router.post("/catalog/{kind}/{item_id}/install", status_code=201)
async def install_capability(kind: str, item_id: str, request: Request,
                             body: InstallBody | None = None,
                             user=Depends(get_current_user),
                             service=Depends(get_capability_service)) -> dict:
    """用户自行安装某条目（写安装记录，不复制任何文件）。

    插件条目可带 `config`（个人配置）：非空时按插件 schema 校验必填，
    通过后写用户维度配置；校验不通过不产生安装记录（fail-closed）。

    Args:
        kind: 条目类型。
        item_id: 条目 id。
        request: FastAPI 请求（取插件包与配置存储）。
        body: 安装请求体（可选）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        {"kind", "id", "installed"}。

    Raises:
        HTTPException: 类型非法/条目不可安装（404）、必填配置缺失（422）、
            插件配置存储未就绪（503）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    # 先判可安装再校验 config：hidden 插件必须被 404 挡住，不能借 422 回显其配置字段名
    await _require_installable(service, user["sub"], kind, item_id)
    values = dict(body.config) if body is not None else {}
    if kind == "plugin" and values:
        packages = getattr(request.app.state, "plugin_packages", None) or {}
        package = packages.get(item_id)
        if package is None:
            raise HTTPException(404, f"插件不存在: {item_id}")
        missing = [
            f["key"] for f in package.config_schema
            if f.get("required") and not str(values.get(f["key"]) or "").strip()
        ]
        if missing:
            raise HTTPException(422, f"缺少必填配置: {', '.join(missing)}")
        store = getattr(request.app.state, "plugin_config_store", None)
        if store is None:
            raise HTTPException(503, "插件配置存储未就绪")
        await store.save_for_user(user["sub"], item_id, values, package.config_schema)
    await _install_core(request, service, user["sub"], kind, item_id)
    return {"kind": kind, "id": item_id, "installed": True}


@router.delete("/catalog/{kind}/{item_id}/install")
async def uninstall_capability(kind: str, item_id: str,
                               user=Depends(get_current_user),
                               service=Depends(get_capability_service)) -> dict:
    """卸载（删除该用户的安装记录）。

    Args:
        kind: 条目类型。
        item_id: 条目 id。
        user: 当前用户。
        service: 能力服务。

    Returns:
        {"kind", "id", "installed", "removed"}。

    Raises:
        HTTPException: 类型非法（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    removed = await service.installs.uninstall(user["sub"], kind, item_id)
    return {"kind": kind, "id": item_id, "installed": False, "removed": removed}


@router.get("/admin/catalog")
async def admin_list_catalog(kind: str | None = None,
                             user=Depends(require_admin),
                             service=Depends(get_capability_service)) -> list[dict]:
    """管理员视角的目录（含 hidden 条目；visible 字段仍按普通用户语义计算）。

    Args:
        kind: 可选类型过滤。
        user: 当前用户（须为管理员）。
        service: 能力服务。

    Returns:
        条目列表。
    """
    kinds = (kind,) if kind in KINDS else KINDS
    rows: list[dict] = []
    for k in kinds:
        rows.extend(await service.market_items(user["sub"], k, admin=True))
    return rows


@router.put("/admin/catalog/{kind}/{item_id}/policy")
async def set_catalog_policy(kind: str, item_id: str, body: PolicyBody,
                             user=Depends(require_admin),
                             service=Depends(get_capability_service)) -> dict:
    """配置条目的可见性与默认启用。

    Args:
        kind: 条目类型。
        item_id: 条目 id。
        body: 策略请求体。
        user: 当前用户（须为管理员）。
        service: 能力服务。

    Returns:
        {"kind", "id", "visibility", "default_enabled"}。

    Raises:
        HTTPException: 类型非法或条目不存在（404）、visibility 非法（422）。
    """
    if kind not in KINDS or not await service.exists(kind, item_id):
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")
    try:
        policy = await service.policy.set(kind, item_id, visibility=body.visibility,
                                          default_enabled=body.default_enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"kind": kind, "id": item_id, **policy}


@router.put("/me/capabilities/{kind}/{item_id}")
async def switch_capability(kind: str, item_id: str, body: CapabilitySwitchBody,
                            request: Request,
                            user=Depends(get_current_user),
                            service=Depends(get_capability_service)) -> dict:
    """安装/卸载/启用/停用某条目（用户维度）。

    `installed=true` 等价安装（先过 can_install 判定），`installed=false` 等价卸载
    （删记录）；`enabled` 只对已安装条目有效，未安装时返回 422。

    注意：请求同时带 `installed=false` 时，`enabled` 一律忽略（不校验、不报错），
    因为卸载后已无记录可改；这使 `{"installed": false, "enabled": false}` 这类
    自洽请求不会落入 422。

    Args:
        kind: 条目类型。
        item_id: 条目 id。
        body: 开关请求体。
        request: FastAPI 请求。
        user: 当前用户。
        service: 能力服务。

    Returns:
        {"kind", "id", "installed", "enabled"}。

    Raises:
        HTTPException: 类型非法或条目不可安装（404）、内置条目（409，
            全员自动可用，用户无启停/卸载概念）、未安装却要改启用态（422）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    user_id = user["sub"]
    # hidden 优先 404（不泄露存在性）；内置条目（default_enabled=True）全员
    # 强制可用：用户的安装/启停/卸载一律拒绝——判定层已忽略其安装记录，
    # 这里收口防止 API 侧留下无意义记录
    pol = await service.policy.get(kind, item_id)
    if pol["visibility"] == "hidden":
        raise HTTPException(404, f"条目不可安装: {kind}:{item_id}")
    if pol["default_enabled"]:
        raise HTTPException(409, "内置条目全员自动可用，无需安装或启停")
    if body.installed is True:
        await _require_installable(service, user_id, kind, item_id)
        await _install_core(request, service, user_id, kind, item_id)
    elif body.installed is False:
        await service.installs.uninstall(user_id, kind, item_id)
    # 卸载请求里的 enabled 一并视为无效：先卸载就没有记录可改，否则
    # {"installed": false, "enabled": false} 这种自洽请求会被误判成 422
    if body.enabled is not None and body.installed is not False:
        if not await service.installs.set_enabled(user_id, kind, item_id, body.enabled):
            raise HTTPException(422, f"未安装，无法设置启用态: {kind}:{item_id}")
    return {
        "kind": kind, "id": item_id,
        "installed": await service.installs.is_installed(user_id, kind, item_id),
        "enabled": await service.installs.is_enabled(user_id, kind, item_id),
    }


@router.get("/market/{kind}")
async def market(kind: str, user=Depends(get_current_user),
                 service=Depends(get_capability_service)) -> list[dict]:
    """市场列表（某类型下当前用户可见的可安装条目）。

    按 kind 分片返回单类条目，供用户侧「能力中心」渲染；与 `/catalog` 的区别
    是后者把三类混排且 kind 非法时静默退化为全类型，而本端点类型非法即 404。

    Args:
        kind: 条目类型（expert/skill/plugin）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        条目列表（含 installed/enabled/default_enabled/visibility）。

    Raises:
        HTTPException: 类型非法（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    return await service.market_items(user["sub"], kind)
