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
from app.plugins.config_store import user_doc_id

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


def _state_service(request: Request, name: str):
    """从 app.state 取服务实例（未就绪时 503，与 me_api 同口径）。

    Args:
        request: FastAPI 请求。
        name: app.state 上的服务属性名（skill_service / expert_service / plugin_service）。

    Returns:
        服务实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, name, None)
    if service is None:
        raise HTTPException(503, f"{name} 未就绪")
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


async def _public_ready_keys(request: Request, plugin_id: str) -> set[str]:
    """插件公共配置已就绪的字段 key 集合（管理员配置打底，值不外泄）。

    供两处共用：市场行补 config_ready_keys（前端据此放宽必填并提示"留空用
    系统配置"）、安装必填校验排除已有公共打底的字段。解密失败按"无打底"
    降级（用户填全量仍可安装，不让密钥轮换问题挡住安装路径）。

    Args:
        request: FastAPI 请求（取插件配置存储）。
        plugin_id: 插件 id。

    Returns:
        公共配置非空值的 key 集合；无公共配置/存储未就绪时空集合。
    """
    store = getattr(request.app.state, "plugin_config_store", None)
    if store is None:
        return set()
    try:
        resolved = await store.resolved(plugin_id)
    except RuntimeError:
        return set()
    return {k for k, v in resolved.items() if str(v or "").strip()}


async def _personal_config_snapshot(request: Request, user_id: str, plugin_id: str,
                                    schema: list[dict]) -> tuple[dict, dict]:
    """用户个人配置快照（非敏感值明文 + 敏感字段是否已配置，绝不回传敏感明文）。

    直接读原始个人文档（`user:<uid>:<pid>`）而非运行期缓存：缓存是公共配置，
    个人值只在用户自己填过时才存在。敏感字段在库里是 `{value, encrypted}`
    包装，这里只取"是否真有密文"的布尔——前端据此提示"已配置，留空保持不变"。

    Args:
        request: FastAPI 请求（取插件配置存储）。
        user_id: 用户 sub。
        plugin_id: 插件 id。
        schema: 插件配置 schema（决定哪些 key 是敏感字段）。

    Returns:
        (非敏感字段值, {敏感字段: 是否已配置})；无个人记录或存储未就绪时为
        ({}, {每个敏感字段: False})。
    """
    store = getattr(request.app.state, "plugin_config_store", None)
    schema = schema or []
    secret_keys = [f["key"] for f in schema if f.get("secret")]
    if store is None:
        return {}, {k: False for k in secret_keys}
    doc = await store.get_doc(user_doc_id(user_id, plugin_id)) or {}
    secrets = doc.get("secrets") or {}
    config = {k: v for k, v in (doc.get("config") or {}).items()
              if k not in set(secret_keys)}
    secrets_set = {
        k: bool(isinstance(secrets.get(k), dict)
                and str(secrets[k].get("value") or "").strip())
        for k in secret_keys
    }
    return config, secrets_set


async def _own_skill_detail(request: Request, user_id: str, name: str) -> dict | None:
    """用户自建技能详情（非自建返回 None）。

    Args:
        request: FastAPI 请求（取技能服务）。
        user_id: 用户 sub。
        name: 技能名。

    Returns:
        详情字典（origin='mine'）；该用户没有此名自建技能时 None。

    Raises:
        HTTPException: 技能服务未就绪（503）。
    """
    svc = _state_service(request, "skill_service")
    own = {s["name"]: s for s in svc.list_own_skills(user_id)}
    row = own.get(name)
    if row is None:
        return None
    return {
        "kind": "skill",
        "id": name,
        "name": name,
        "description": str(row.get("description") or ""),
        "origin": "mine",
        "enabled": True,
        "content": svc.read_body(name, user_id) or "",
        "files": svc.list_skill_files(name, user_id),
    }


async def _own_expert_detail(request: Request, user_id: str, expert_id: str) -> dict | None:
    """用户自建专家详情（非自建返回 None）。

    Args:
        request: FastAPI 请求（取专家服务）。
        user_id: 用户 sub。
        expert_id: 专家 id。

    Returns:
        详情字典（origin='mine'）；该用户没有此专家时 None。

    Raises:
        HTTPException: 专家服务未就绪（503）。
    """
    svc = _state_service(request, "expert_service")
    own = await svc.get_own(user_id, expert_id)
    if own is None:
        return None
    return {
        "kind": "expert",
        "id": expert_id,
        "name": str(own.get("name") or expert_id),
        "description": str(own.get("description") or ""),
        "origin": "mine",
        "enabled": True,
        "avatar": str(own.get("avatar") or ""),
        "system_prompt": str(own.get("system_prompt") or ""),
        "tool_whitelist": [str(t) for t in (own.get("tool_whitelist") or [])],
        "skill_refs": [str(t) for t in (own.get("skill_refs") or [])],
        "mcp_refs": [str(t) for t in (own.get("mcp_refs") or [])],
        "suggested_prompts": [
            str(t) for t in (own.get("suggested_prompts") or [])
        ],
    }


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


class UserPluginConfigBody(BaseModel):
    """用户维度插件配置请求体（schema 未声明的 key 由存储层忽略）。"""

    config: dict[str, Any] = {}


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
        # 必填校验按"个人值 ∪ 公共配置打底"判定：管理员公共配置已就绪的字段，
        # 用户可以留空（运行期个人优先、公共兜底）；个人层只存非空值，
        # 空串不落个人层——避免用空串把公共打底覆盖成"未配置"
        ready = await _public_ready_keys(request, item_id)
        missing = [
            f["key"] for f in package.config_schema
            if f.get("required") and f["key"] not in ready
            and not str(values.get(f["key"]) or "").strip()
        ]
        if missing:
            raise HTTPException(422, f"缺少必填配置: {', '.join(missing)}")
        store = getattr(request.app.state, "plugin_config_store", None)
        if store is None:
            raise HTTPException(503, "插件配置存储未就绪")
        values = {k: v for k, v in values.items() if str(v or "").strip()}
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


@router.put("/me/plugins/{plugin_id}/config")
async def update_user_plugin_config(plugin_id: str, body: UserPluginConfigBody,
                                    request: Request,
                                    user=Depends(get_current_user),
                                    service=Depends(get_capability_service)) -> dict:
    """更新当前用户的插件个人配置（已安装插件；敏感字段留空 = 保持原值）。

    与管理员路径（`PUT /plugins/{id}/config`）的分工：本端点只写用户维度文档，
    不碰公共配置。合并语义（非敏感覆盖、敏感留空保持、未声明 key 忽略）全部由
    `PluginConfigStore.save_for_user` 实现，本层不重复实现，只做归属与就绪判定。

    与安装路径同口径地丢弃空值：个人层优先于公共兜底，写空串会把管理员的公共
    配置"覆盖"成未配置，插件运行期直接失效（详情页对系统默认字段不预填个人值，
    留空保存是最常见的一次操作）。

    Args:
        plugin_id: 插件 id。
        body: 配置请求体。
        request: FastAPI 请求（取插件包与配置存储）。
        user: 当前用户。
        service: 能力服务（读安装记录）。

    Returns:
        {"kind": "plugin", "id", "config": {非敏感值}, "secrets_set": {敏感字段:
        是否已配置}}——与详情端点同形，调用方无需二次请求即可刷新表单。

    Raises:
        HTTPException: 插件不存在或该用户未安装（404，两者同一响应，不泄露
            hidden 插件的存在性）、配置存储未就绪（503）。
    """
    packages = getattr(request.app.state, "plugin_packages", None) or {}
    package = packages.get(plugin_id)
    if package is None:
        raise HTTPException(404, f"插件不存在: {plugin_id}")
    # 归属判定以安装记录为准：没装过的人（含"插件是 hidden 因而不可见"的人）
    # 与未知插件得到同一个 404，不构成存在性探测
    if not await service.installs.is_installed(user["sub"], "plugin", plugin_id):
        raise HTTPException(404, f"插件未安装: {plugin_id}")
    store = getattr(request.app.state, "plugin_config_store", None)
    if store is None:
        raise HTTPException(503, "插件配置存储未就绪")
    values = {k: v for k, v in body.config.items() if str(v or "").strip()}
    await store.save_for_user(user["sub"], plugin_id, values, package.config_schema)
    config, secrets_set = await _personal_config_snapshot(
        request, user["sub"], plugin_id, package.config_schema)
    return {"kind": "plugin", "id": plugin_id, "config": config, "secrets_set": secrets_set}


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
        HTTPException: 类型非法或条目不可安装（404，含 hidden 条目；hidden 且
            已安装时卸载除外）、内置条目（409，全员自动可用，用户无启停/卸载
            概念）、未安装却要改启用态（422）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    user_id = user["sub"]
    # hidden 优先 404（不泄露存在性）；内置条目（default_enabled=True）全员
    # 强制可用：用户的安装/启停/卸载一律拒绝——判定层已忽略其安装记录，
    # 这里收口防止 API 侧留下无意义记录
    pol = await service.policy.get(kind, item_id)
    if pol["visibility"] == "hidden":
        # 已安装但被下架的条目：「我的」列表刻意保留该行供用户清理，卸载是它唯一
        # 合法动作，不能与其余 hidden 情形一并 404；未安装的一律 404 不泄露存在性。
        if body.installed is False and await service.installs.is_installed(
                user_id, kind, item_id):
            await service.installs.uninstall(user_id, kind, item_id)
            return {"kind": kind, "id": item_id, "installed": False, "enabled": False}
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
async def market(kind: str, request: Request, user=Depends(get_current_user),
                 service=Depends(get_capability_service)) -> list[dict]:
    """市场列表（某类型下当前用户可见的可安装条目）。

    按 kind 分片返回单类条目，供用户侧「能力中心」渲染；与 `/catalog` 的区别
    是后者把三类混排且 kind 非法时静默退化为全类型，而本端点类型非法即 404。
    插件行额外带 config_ready_keys（管理员公共配置已就绪的字段名）：前端据此
    在安装表单放宽这些字段的必填，并提示"留空使用系统配置"。

    Args:
        kind: 条目类型（expert/skill/plugin）。
        request: FastAPI 请求（取插件配置存储）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        条目列表（含 installed/enabled/default_enabled/visibility）。

    Raises:
        HTTPException: 类型非法（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    rows = await service.market_items(user["sub"], kind)
    if kind == "plugin":
        for row in rows:
            row["config_ready_keys"] = sorted(
                await _public_ready_keys(request, str(row["id"])))
    return rows


@router.get("/me/capabilities/{kind}/{item_id}")
async def capability_detail(kind: str, item_id: str, request: Request,
                            user=Depends(get_current_user),
                            service=Depends(get_capability_service)) -> dict:
    """当前用户视角下某能力的详情（用户侧能力中心详情页）。

    解析顺序：用户自建（技能/专家，落 `users/<uid>/`）优先 → catalog 内置条目。
    内置条目按市场同一可见性口径判定：hidden 且未安装一律 404（不泄露存在性），
    未安装条目也可读（否则用户无法在安装前判断内容）；hidden 但已安装的例外
    ——列表刻意留着该行供用户卸载，详情要能打开才不会成为死路，返回时带
    `revoked: True` 标记。豁免范围严格对齐"用户自己的列表里真的能看见"：
    hidden + 已安装 + 非内置才成立；hidden + 内置（default_enabled）时该行在
    「我的」被跳过、在市场被 hidden 挡住，残留安装记录也不构成可达性。

    `origin` 四态供前端决定渲染与动作：`mine` 自建（可编辑/删除）、
    `installed` 已安装（可启停/卸载）、`builtin` 内置（普通用户只读）、
    `market` 市场可见未安装（可安装）。

    内容一律从 `catalog_roots()` 链上读（repo 的 `catalog/` +
    数据目录 `public/catalog/` 同名覆盖），不在数据库另存副本——服务初始
    状态即与代码仓库一致。本端点只读，管理员编辑走既有管理后台。

    Args:
        kind: 条目类型（expert/skill/plugin）。
        item_id: 条目 id（技能 = 技能名）。
        request: FastAPI 请求（取技能/专家/插件服务）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        详情字典：公共字段 + origin/enabled + 按 kind 的特有字段；下架但已安装且
        非内置的条目额外带 `revoked: True`。插件在「用户自己装过且未下架」时额外
        带 `config`（非敏感字段当前值，供编辑表单预填）与 `secrets_set`
        （{敏感字段: 是否已配置}，供提示"留空保持不变"）；敏感明文一律不回传。

    Raises:
        HTTPException: 类型非法、条目不存在或不可见（404）、
            技能/专家/插件服务未就绪（503）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    user_id = user["sub"]

    # 自建优先：用户自己创建的内容不在 catalog 里
    if kind == "skill":
        own = await _own_skill_detail(request, user_id, item_id)
        if own is not None:
            return own
    elif kind == "expert":
        own = await _own_expert_detail(request, user_id, item_id)
        if own is not None:
            return own

    item = next((i for i in service.catalog.list_items(kind) if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")
    pol = await service.policy.get(kind, item_id)
    installed = await service.installs.is_installed(user_id, kind, item_id)
    # 已安装但被管理员下架（revoked）：列表（/me/skills、/me/experts）刻意保留该行
    # 供用户卸载，详情同样不该 404（存在性本就不算泄露——行本来就在用户自己的列表里）。
    # 内置条目在那些列表里被跳过（全员自动可用、用户侧只读），故 hidden + 内置 +
    # 残留安装记录的组合在任何列表都不可达，不享受豁免，依旧 404。
    revoked = (pol["visibility"] == "hidden" and installed
               and not pol["default_enabled"])
    if pol["visibility"] == "hidden" and not revoked:
        raise HTTPException(404, f"条目不存在: {kind}:{item_id}")

    enabled = await service.installs.is_enabled(user_id, kind, item_id)
    if pol["default_enabled"]:
        origin = "builtin"
    elif installed:
        origin = "installed"
    else:
        origin = "market"
    row: dict[str, Any] = {
        "kind": kind,
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "origin": origin,
        # 内置条目恒为可用态（与 market_items 的 visible 口径一致）
        "enabled": bool(pol["default_enabled"]) or enabled,
    }
    if revoked:
        # 仅下架条目带此标记（前端据此提示"已下架"并只保留卸载动作）；
        # 正常条目省掉该键，与其它 kind 特有字段"有意义才出现"的约定一致
        row["revoked"] = True

    if kind == "expert":
        pkg = service.catalog.experts.get(item_id)
        if pkg is not None:
            row["avatar"] = pkg.avatar
            row["system_prompt"] = pkg.system_prompt
            row["tool_whitelist"] = list(pkg.tool_whitelist)
            row["skill_refs"] = list(pkg.skill_refs)
            row["mcp_refs"] = list(pkg.mcp_refs)
            row["suggested_prompts"] = list(pkg.suggested_prompts)
    elif kind == "skill":
        svc = _state_service(request, "skill_service")
        row["content"] = svc.read_body(item_id, user_id) or ""
        row["files"] = svc.list_skill_files(item_id, user_id)
    elif kind == "plugin":
        row["config_ready_keys"] = sorted(await _public_ready_keys(request, item_id))
        state = _state_service(request, "plugin_service").state(item_id)
        row["config_schema"] = state["config_schema"]
        row["skills"] = state["skills"]
        row["experts"] = state["experts"]
        row["tools"] = state["tools"]
        # 个人配置快照只在"用户自己装过"（origin='installed'）时给出：没有个人层
        # 可编辑的条目（市场未装 / 内置全员可用 / 已下架只留卸载）不给这两个键，
        # 前端就不会渲染出"编辑配置"入口。敏感字段只回布尔，明文永不出库。
        if origin == "installed" and not revoked:
            config, secrets_set = await _personal_config_snapshot(
                request, user_id, item_id, state["config_schema"])
            row["config"] = config
            row["secrets_set"] = secrets_set
    return row
