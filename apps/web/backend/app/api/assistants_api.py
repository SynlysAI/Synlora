"""助手管理 API：builtin 不可删、工具白名单与 provider 引用校验、名称联查。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.api.deps import (
    Repos,
    get_current_user,
    get_repos,
    require_admin,
    validate_tool_whitelist,
)

router = APIRouter(prefix="/api/v1/assistants", tags=["assistants"])


async def _validate_provider(provider_id: str, repos: Repos) -> None:
    """校验引用的模型服务存在且 enabled。

    只做存在性校验、不解密、不构造运行时配置；运行期真正要用模型时走
    session_runtime._resolve_provider（那里才解密出 ModelProviderConfig），
    两者数据源与职责不同，故不合并。

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


def _validate_skill_refs(request: Request, skill_refs: list[str]) -> None:
    """校验助手绑定的技能引用均来自当前技能目录。

    Args:
        request: FastAPI 请求，用于读取技能服务。
        skill_refs: 助手绑定的技能名列表。

    Raises:
        HTTPException: 存在不存在的技能引用时返回 422。
    """
    skill_service = getattr(request.app.state, "skill_service", None)
    if skill_service is None:
        return
    available = {str(item.get("name")) for item in skill_service.list_skills()}
    invalid = sorted({str(item).strip() for item in skill_refs if str(item).strip()}
                     - available)
    if invalid:
        raise HTTPException(422, f"技能不存在: {', '.join(invalid)}")


class AssistantCreateBody(BaseModel):
    """新建助手请求体。"""

    name: str
    avatar: str = ""
    description: str = ""
    system_prompt: str
    tool_whitelist: list[str] = []
    skill_refs: list[str] = []
    mcp_refs: list[str] = []
    suggested_prompts: list[str] = []
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
    skill_refs: list[str] | None = None
    mcp_refs: list[str] | None = None
    suggested_prompts: list[str] | None = None
    model_provider_id: str | None = None
    knowledge_base_ids: list[str] | None = None


async def _visible_assistants(app_state, user: dict, docs: list[dict]) -> list[dict]:
    """按可见性过滤 assistants 集合文档（普通用户视角）。

    集合只装管理员资产（目录播种/插件播种/管理员自建）。规则：插件播种专家
    （有 plugin_id）跟随其插件可见性；目录内置专家按 expert 策略判定；其余
    （管理员在助手页新建的非目录助手）始终保留。用户自建专家不在此集合
    （纯文件事实源），由 list_assistants 另行从作者文件根拼装。

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
        out.append(a)
    return out


async def _own_expert_rows(app_state, user: dict) -> list[dict]:
    """当前用户的自建专家 → 助手列表行（与助手文档同构的投影）。

    Args:
        app_state: app.state（取 expert_service，未就绪返回空）。
        user: 当前用户 payload。

    Returns:
        投影后的专家行列表（model_name 恒为 None：不关联模型服务）。
    """
    svc = getattr(app_state, "expert_service", None)
    if svc is None:
        return []
    return [
        {
            "_id": e["_id"], "name": e["name"], "avatar": e["avatar"],
            "description": e["description"], "system_prompt": e["system_prompt"],
            "model_provider_id": None, "tool_whitelist": e["tool_whitelist"],
            "skill_refs": e["skill_refs"], "mcp_refs": e["mcp_refs"],
            "suggested_prompts": e["suggested_prompts"],
            "knowledge_base_ids": [], "builtin": False, "model_name": None,
        }
        for e in await svc.list_own(user["sub"])
    ]


@router.get("")
async def list_assistants(request: Request, user=Depends(get_current_user),
                          repos=Depends(get_repos)) -> list[dict]:
    """对当前用户可见的全部助手 = 集合管理员资产 ∪ 本人自建专家（文件）。

    provider 停用标 "(已停用)"，已被删除标 "(已删除)"，未关联为 None。
    普通用户视角下不可见的目录专家/插件专家被过滤，管理员不过滤。
    """
    providers = await repos.provider.list()
    by_id = {p["_id"]: p for p in providers}
    docs = await _visible_assistants(request.app.state, user,
                                     await repos.assistant.list())
    docs = [*docs, *await _own_expert_rows(request.app.state, user)]
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
async def create_assistant(request: Request, body: AssistantCreateBody,
                           user=Depends(require_admin),
                           repos=Depends(get_repos)) -> dict:
    """新建助手（require_admin；builtin 恒为 False）。

    Raises:
        HTTPException: 工具白名单/模型服务引用非法（422）。
    """
    validate_tool_whitelist(body.tool_whitelist)
    _validate_skill_refs(request, body.skill_refs)
    if body.model_provider_id:
        await _validate_provider(body.model_provider_id, repos)
    return await repos.assistant.create({
        "name": body.name,
        "avatar": body.avatar,
        "description": body.description,
        "system_prompt": body.system_prompt,
        "tool_whitelist": body.tool_whitelist,
        "skill_refs": body.skill_refs,
        "mcp_refs": body.mcp_refs,
        "suggested_prompts": body.suggested_prompts,
        "model_provider_id": body.model_provider_id,
        "knowledge_base_ids": body.knowledge_base_ids,
        "builtin": False,
    })


@router.patch("/{assistant_id}")
async def update_assistant(request: Request, assistant_id: str,
                           body: AssistantUpdateBody,
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
        validate_tool_whitelist(body.tool_whitelist)
        fields["tool_whitelist"] = body.tool_whitelist
    if body.skill_refs is not None:
        _validate_skill_refs(request, body.skill_refs)
        fields["skill_refs"] = body.skill_refs
    if body.mcp_refs is not None:
        fields["mcp_refs"] = body.mcp_refs
    if body.suggested_prompts is not None:
        fields["suggested_prompts"] = body.suggested_prompts
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
