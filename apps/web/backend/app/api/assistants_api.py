"""助手管理 API：builtin 不可删、工具白名单与 provider 引用校验、名称联查。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.api.deps import Repos, get_current_user, get_repos, require_admin
from app.services.tool_registry import REGISTRY as _REGISTRY

router = APIRouter(prefix="/api/v1/assistants", tags=["assistants"])


def _validate_tool_whitelist(whitelist: list[str]) -> None:
    """校验工具白名单是内置工具名集合的子集。

    Args:
        whitelist: 请求提供的工具名列表。

    Raises:
        HTTPException: 含未注册工具名（422，detail 列出非法项）。
    """
    invalid = sorted(set(whitelist) - set(_REGISTRY.names))
    if invalid:
        raise HTTPException(422, f"未注册的工具名: {', '.join(invalid)}")


async def _validate_provider(provider_id: str, repos: Repos) -> None:
    """校验引用的模型服务存在且 enabled。

    Args:
        provider_id: 模型服务 id。
        repos: repo 集中访问对象。

    Raises:
        HTTPException: 不存在或已停用（422）。
    """
    doc = await repos.provider.get(provider_id)
    if doc is None:
        raise HTTPException(422, f"模型服务不存在: {provider_id}")
    if not doc.get("enabled"):
        raise HTTPException(422, f"模型服务已停用: {doc.get('name', provider_id)}")


class AssistantCreateBody(BaseModel):
    """新建助手请求体。"""

    name: str
    avatar: str = ""
    description: str = ""
    system_prompt: str
    tool_whitelist: list[str] = []
    model_provider_id: str | None = None
    knowledge_base_ids: list[str] = []

    @field_validator("name", "system_prompt")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        """去首尾空白且拒绝空白串。"""
        if not v.strip():
            raise ValueError("不能为空")
        return v.strip()


class AssistantUpdateBody(BaseModel):
    """更新助手请求体（全字段可选，仅校验提供的字段）。"""

    name: str | None = None
    avatar: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tool_whitelist: list[str] | None = None
    model_provider_id: str | None = None
    knowledge_base_ids: list[str] | None = None


async def _visible_assistants(app_state, user: dict, docs: list[dict]) -> list[dict]:
    """按可见性过滤助手列表（普通用户视角）。

    规则：插件播种专家（有 plugin_id）跟随其插件可见性；目录内置专家按
    expert 策略判定；其余（用户自建，builtin=False 且无 plugin_id）始终保留。

    Args:
        app_state: app.state（取 capability_service，缺失时不过滤）。
        user: 当前用户 payload。
        docs: 助手文档列表。

    Returns:
        过滤后的助手文档列表。
    """
    caps = getattr(app_state, "capability_service", None)
    if caps is None or user.get("role") == "admin":
        return docs
    expert_ids = {i.id for i in caps.catalog.list_items("expert")}
    out: list[dict] = []
    for a in docs:
        plugin_id = a.get("plugin_id")
        if plugin_id:
            if await caps.is_visible(user["sub"], "plugin", str(plugin_id)):
                out.append(a)
            continue
        assistant_id = str(a.get("_id") or "")
        if assistant_id in expert_ids:
            if await caps.is_visible(user["sub"], "expert", assistant_id):
                out.append(a)
            continue
        out.append(a)  # 用户自建助手：不属目录条目，不过滤
    return out


@router.get("")
async def list_assistants(request: Request, user=Depends(get_current_user),
                          repos=Depends(get_repos)) -> list[dict]:
    """全部助手（含 builtin 标记），联查模型服务名。

    provider 停用标 "(已停用)"，已被删除标 "(已删除)"，未关联为 None。
    普通用户视角下不可见的目录专家/插件专家被过滤，管理员不过滤。
    """
    providers = await repos.provider.list()
    by_id = {p["_id"]: p for p in providers}
    docs = await _visible_assistants(request.app.state, user,
                                     await repos.assistant.list())
    out: list[dict] = []
    for a in docs:
        item = dict(a)
        pid = a.get("model_provider_id")
        if not pid:
            item["model_name"] = None
        elif pid not in by_id:
            item["model_name"] = "(已删除)"
        elif not by_id[pid].get("enabled"):
            item["model_name"] = "(已停用)"
        else:
            item["model_name"] = by_id[pid].get("name")
        out.append(item)
    return out


@router.post("", status_code=201)
async def create_assistant(body: AssistantCreateBody, user=Depends(require_admin),
                           repos=Depends(get_repos)) -> dict:
    """新建助手（require_admin；builtin 恒为 False）。

    Raises:
        HTTPException: 工具白名单/模型服务引用非法（422）。
    """
    _validate_tool_whitelist(body.tool_whitelist)
    if body.model_provider_id:
        await _validate_provider(body.model_provider_id, repos)
    return await repos.assistant.create({
        "name": body.name,
        "avatar": body.avatar,
        "description": body.description,
        "system_prompt": body.system_prompt,
        "tool_whitelist": body.tool_whitelist,
        "model_provider_id": body.model_provider_id,
        "knowledge_base_ids": body.knowledge_base_ids,
        "builtin": False,
    })


@router.patch("/{assistant_id}")
async def update_assistant(assistant_id: str, body: AssistantUpdateBody,
                           user=Depends(require_admin),
                           repos=Depends(get_repos)) -> dict:
    """更新助手（require_admin；仅校验提供的字段）。

    Raises:
        HTTPException: 不存在（404）、字段非法（422）。
    """
    if await repos.assistant.get(assistant_id) is None:
        raise HTTPException(404, "助手不存在")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
        if not fields["name"]:
            raise HTTPException(422, "name 不能为空")
    if body.avatar is not None:
        fields["avatar"] = body.avatar
    if body.description is not None:
        fields["description"] = body.description
    if body.system_prompt is not None:
        fields["system_prompt"] = body.system_prompt.strip()
        if not fields["system_prompt"]:
            raise HTTPException(422, "system_prompt 不能为空")
    if body.tool_whitelist is not None:
        _validate_tool_whitelist(body.tool_whitelist)
        fields["tool_whitelist"] = body.tool_whitelist
    if body.model_provider_id is not None:
        await _validate_provider(body.model_provider_id, repos)
        fields["model_provider_id"] = body.model_provider_id
    if body.knowledge_base_ids is not None:
        # 不在此处校验知识库 id 是否仍存在于 WeKnora：库可能被删后重建，
        # 失效 id 只会导致检索无结果（工具层有明确报错），不阻塞保存
        fields["knowledge_base_ids"] = body.knowledge_base_ids
    return await repos.assistant.update(assistant_id, fields)


@router.delete("/{assistant_id}")
async def delete_assistant(assistant_id: str, user=Depends(require_admin),
                           repos=Depends(get_repos)) -> dict:
    """删除助手（require_admin；builtin 409）。

    Raises:
        HTTPException: 不存在（404）、内置助手（409）。
    """
    doc = await repos.assistant.get(assistant_id)
    if doc is None:
        raise HTTPException(404, "助手不存在")
    if doc.get("builtin"):
        raise HTTPException(409, "内置助手不可删除")
    await repos.assistant.delete(assistant_id)
    return {"ok": True}
