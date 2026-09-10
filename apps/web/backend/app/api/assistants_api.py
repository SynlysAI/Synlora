"""助手管理 API：builtin 不可删、工具白名单与 provider 引用校验、名称联查。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from synlys_harness import ToolRegistry, register_builtin_tools

from app.api.deps import Repos, get_current_user, get_repos, require_admin

router = APIRouter(prefix="/api/v1/assistants", tags=["assistants"])

_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)


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
    description: str = ""
    system_prompt: str
    tool_whitelist: list[str] = []
    model_provider_id: str | None = None

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
    description: str | None = None
    system_prompt: str | None = None
    tool_whitelist: list[str] | None = None
    model_provider_id: str | None = None


@router.get("")
async def list_assistants(user=Depends(get_current_user),
                          repos=Depends(get_repos)) -> list[dict]:
    """全部助手（含 builtin 标记），联查模型服务名。

    provider 停用标 "(已停用)"，已被删除标 "(已删除)"，未关联为 None。
    """
    providers = await repos.provider.list()
    by_id = {p["_id"]: p for p in providers}
    out: list[dict] = []
    for a in await repos.assistant.list():
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
        "description": body.description,
        "system_prompt": body.system_prompt,
        "tool_whitelist": body.tool_whitelist,
        "model_provider_id": body.model_provider_id,
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
