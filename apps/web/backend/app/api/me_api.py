"""「我的」端点：用户自建技能与用户自建专家，以及已安装插件的个人视图。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_current_user, validate_tool_whitelist
from app.catalog.api import get_capability_service
from app.services.skill_service import SkillNameTaken, SkillNotFound

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class MySkillBody(BaseModel):
    """自建技能字段（content 为 SKILL.md 正文，不含 frontmatter）。"""

    name: str
    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


class MySkillUpdateBody(BaseModel):
    """自建技能更新体（技能名以路径参数为准）。"""

    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


def _skill_service(request: Request):
    """取 SkillService（未就绪 503）。

    Args:
        request: FastAPI 请求。

    Returns:
        SkillService 实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "skill_service", None)
    if service is None:
        raise HTTPException(503, "技能服务未就绪")
    return service


@router.get("/skills")
async def list_my_skills(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """我的技能 = 自建 ∪ 已安装的内置技能。

    Args:
        request: FastAPI 请求。
        user: 当前用户。

    Returns:
        条目列表（source = mine|installed，含 installed/enabled/builtin；
        installed 条目额外带 revoked：管理员已下架该条目）。

    Raises:
        HTTPException: 能力服务未就绪（503）。
    """
    svc = _skill_service(request)
    caps = get_capability_service(request)
    user_id = user["sub"]
    rows = [
        {"name": s["name"], "description": s["description"],
         "version": s.get("version", "1.0"), "source": "mine",
         "installed": False, "enabled": True, "builtin": False}
        for s in svc.list_own_skills(user_id)
    ]
    # 一次取回安装状态（{id: enabled}），同时得到"装没装"与"启没启用"
    states = await caps.installs.install_states(user_id, "skill")
    for item in caps.catalog.list_items("skill"):
        if item.id not in states:
            continue
        pol = await caps.policy.get("skill", item.id)
        # 内置条目（default_enabled=True）全员自动可用、用户侧只读，
        # 不进"我的"（历史安装记录被判定层忽略，这里同口径跳过）
        if pol["default_enabled"]:
            continue
        rows.append({
            "name": item.id, "description": item.description,
            "version": "1.0", "source": "installed",
            "installed": True, "enabled": states[item.id], "builtin": True,
            "revoked": pol["visibility"] == "hidden",
        })
    return rows


@router.post("/skills", status_code=201)
async def create_my_skill(request: Request, body: MySkillBody,
                          user=Depends(get_current_user)) -> dict:
    """新建自建技能。

    Args:
        request: FastAPI 请求。
        body: 技能字段。
        user: 当前用户。

    Returns:
        写入后的技能字典。

    Raises:
        HTTPException: 422 名字不合法；409 与已有/公共/内置技能同名。
    """
    try:
        return _skill_service(request).write_user_skill(
            user["sub"], name=body.name, description=body.description,
            content=body.content, version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except SkillNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/skills/import", status_code=201)
async def import_my_skill(request: Request, file: UploadFile = File(...),
                          user=Depends(get_current_user)) -> dict:
    """上传 ZIP 技能目录包。

    Args:
        request: FastAPI 请求。
        file: 包含 SKILL.md 的 ZIP 文件。
        user: 当前用户。

    Returns:
        导入后的技能字典。
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(422, "仅支持 .zip 技能压缩包")
    try:
        return _skill_service(request).import_user_skill_zip(
            user["sub"], await file.read())
    except SkillNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/skills/{name}/files")
async def list_my_skill_files(request: Request, name: str,
                              user=Depends(get_current_user)) -> list[dict]:
    """列出当前用户可见技能的目录树。"""
    files = _skill_service(request).list_skill_files(name, user_id=user["sub"])
    if not files:
        raise HTTPException(404, "技能不存在或没有可预览文件")
    return files


@router.get("/skills/{name}/file")
async def read_my_skill_file(request: Request, name: str,
                             path: str = Query(...),
                             user=Depends(get_current_user)) -> dict:
    """读取当前用户可见技能中的文本文件。"""
    content = _skill_service(request).read_skill_file(
        name, path, user_id=user["sub"])
    if content is None:
        raise HTTPException(404, "文件不存在或不支持文本预览")
    return content


@router.get("/skills/{name}/raw")
async def raw_my_skill_file(request: Request, name: str,
                            path: str = Query(...),
                            user=Depends(get_current_user)) -> FileResponse:
    """返回当前用户可见技能中的原始文件，供图片预览。"""
    target = _skill_service(request).skill_file_path(
        name, path, user_id=user["sub"])
    if target is None:
        raise HTTPException(404, "文件不存在或不可预览")
    return FileResponse(target)


@router.patch("/skills/{name}")
async def update_my_skill(request: Request, name: str, body: MySkillUpdateBody,
                          user=Depends(get_current_user)) -> dict:
    """覆盖写自建技能（非自建 → 403）。

    Args:
        request: FastAPI 请求。
        name: 技能名（路径参数，技能目录名）。
        body: 更新字段。
        user: 当前用户。

    Returns:
        写入后的技能字典。

    Raises:
        HTTPException: 403 该技能不是你创建的；422 名字不合法。
    """
    try:
        # 「只许改自己的」不变量在服务层：update_user_skill 要求用户目录下确有该技能
        return _skill_service(request).update_user_skill(
            user["sub"], name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except SkillNotFound as exc:  # 必须排在 ValueError 之前（它是其子类）
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/skills/{name}")
async def delete_my_skill(request: Request, name: str,
                          user=Depends(get_current_user)) -> dict:
    """删除自建技能。

    Args:
        request: FastAPI 请求。
        name: 技能名（路径参数）。
        user: 当前用户。

    Returns:
        {"ok": True}。

    Raises:
        HTTPException: 404 不存在（含内置/公共技能——它们不在用户目录里）。
    """
    if not _skill_service(request).delete_user_skill(user["sub"], name):
        raise HTTPException(404, "技能不存在")
    return {"ok": True}


class MyExpertBody(BaseModel):
    """自建专家字段。"""

    name: str
    avatar: str = ""
    description: str = ""
    system_prompt: str
    tool_whitelist: list[str] = []
    skill_refs: list[str] = []
    mcp_refs: list[str] = []
    suggested_prompts: list[str] = []


def _expert_service(request: Request):
    """取 UserExpertService（未就绪 503）。

    Args:
        request: FastAPI 请求。

    Returns:
        UserExpertService 实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "expert_service", None)
    if service is None:
        raise HTTPException(503, "专家服务未就绪")
    return service


async def _validate_expert_refs(request: Request, user_id: str,
                                skill_refs: list[str],
                                mcp_refs: list[str]) -> None:
    """校验专家引用的技能与 MCP 属于当前用户可用范围。

    Args:
        request: FastAPI 请求。
        user_id: 当前用户 ID。
        skill_refs: 专家绑定的技能名列表。
        mcp_refs: 专家绑定的 MCP ID 列表。

    Raises:
        HTTPException: 引用不存在或当前用户不可用。
    """
    skill_service = _skill_service(request)
    capability_service = get_capability_service(request)
    hidden_skills = await capability_service.hidden_skill_names(user_id)
    available_skills = {
        str(item.get("name"))
        for item in skill_service.list_skills(user_id)
        if item.get("name") and item.get("name") not in hidden_skills
    }
    invalid_skills = sorted(set(skill_refs) - available_skills)
    if invalid_skills:
        raise HTTPException(422, f"不可用的技能: {', '.join(invalid_skills)}")

    mcp_service = getattr(request.app.state, "mcp_service", None)
    if mcp_refs and mcp_service is None:
        raise HTTPException(503, "MCP 服务未就绪")
    if mcp_service is not None:
        available_mcps = {
            str(item.get("id"))
            for item in await mcp_service.list_for_user(user_id)
        }
        invalid_mcps = sorted(set(mcp_refs) - available_mcps)
        if invalid_mcps:
            raise HTTPException(422, f"不存在的 MCP: {', '.join(invalid_mcps)}")


def _validate_expert_tools(tool_whitelist: list[str]) -> None:
    """校验专家可用工具，允许引用运行期发现的 MCP 工具。

    Args:
        tool_whitelist: 专家配置的工具名列表。

    Raises:
        HTTPException: 包含未注册的内置工具名。
    """
    builtin_tools = [name for name in tool_whitelist
                     if not str(name).startswith("mcp.")]
    validate_tool_whitelist(builtin_tools)


@router.get("/experts")
async def list_my_experts(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """我的专家 = 自建 ∪ 已安装的内置专家。

    Args:
        request: FastAPI 请求。
        user: 当前用户。

    Returns:
        条目列表（source = mine|installed，含 installed/enabled/builtin；
        installed 条目额外带 revoked：管理员已下架该条目）。

    Raises:
        HTTPException: 能力服务未就绪（503）。
    """
    svc = _expert_service(request)
    caps = get_capability_service(request)
    user_id = user["sub"]
    own = await svc.list_own(user_id)
    rows = [
        {"id": e["_id"], "name": e["name"], "avatar": e["avatar"],
         "description": e["description"], "source": "mine",
         "installed": False, "enabled": True, "builtin": False}
        for e in own
    ]
    # 一次取回安装状态（{id: enabled}），同时得到"装没装"与"启没启用"
    states = await caps.installs.install_states(user_id, "expert")
    for item in caps.catalog.list_items("expert"):
        if item.id not in states:
            continue
        pol = await caps.policy.get("expert", item.id)
        # 内置条目（default_enabled=True）全员自动可用、用户侧只读，
        # 不进"我的"（与 list_my_skills 同口径）
        if pol["default_enabled"]:
            continue
        # 头像取自目录专家包（与市场行同源）；包缺失时留空，由前端回退首字母
        pkg = caps.catalog.experts.get(item.id)
        rows.append({
            "id": item.id, "name": item.name,
            "avatar": pkg.avatar if pkg is not None else "",
            "description": item.description, "source": "installed",
            "installed": True, "enabled": states[item.id], "builtin": True,
            # 与 list_my_skills 同口径：hidden 即"管理员已下架"，前端据此禁启停
            "revoked": pol["visibility"] == "hidden",
        })
    return rows


@router.post("/experts", status_code=201)
async def create_my_expert(request: Request, body: MyExpertBody,
                           user=Depends(get_current_user)) -> dict:
    """新建自建专家。

    `dir_name` 取显示名 sanitize 后的结果，因此「化学 助手」与「化学_助手」这类
    只差在非法字符的名字会折叠到同一目录，后者按同一专家的覆盖写处理（静默覆盖
    前者的内容），本端点不做"重名自动加后缀"。

    Args:
        request: FastAPI 请求。
        body: 专家字段。
        user: 当前用户。

    Returns:
        {"id": 专家 id, "name": 显示名}。

    Raises:
        HTTPException: 422 名字不合法（sanitize 后为空）或工具白名单含未注册工具名。
    """
    svc = _expert_service(request)
    # 自建专家与管理员助手共用同一份工具白名单校验：白名单是替换语义，
    # 未注册的工具名会被运行期静默丢弃，最终专家一个工具都没有
    _validate_expert_tools(body.tool_whitelist)
    await _validate_expert_refs(
        request, user["sub"], body.skill_refs, body.mcp_refs)
    try:
        expert = await svc.write(
            user["sub"], dir_name=body.name, name=body.name, avatar=body.avatar,
            description=body.description, system_prompt=body.system_prompt,
            tool_whitelist=body.tool_whitelist,
            skill_refs=body.skill_refs,
            mcp_refs=body.mcp_refs,
            suggested_prompts=body.suggested_prompts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": expert["_id"], "name": expert["name"]}


@router.patch("/experts/{expert_id}")
async def update_my_expert(request: Request, expert_id: str, body: MyExpertBody,
                           user=Depends(get_current_user)) -> dict:
    """覆盖写自建专家（目录名沿用原专家的，不随显示名漂移）。

    Args:
        request: FastAPI 请求。
        expert_id: 专家 id（形如 {user_id}:{dir}）。
        body: 专家字段。
        user: 当前用户。

    Returns:
        {"id": 专家 id, "name": 显示名}。

    Raises:
        HTTPException: 404 不是自建专家；422 名字不合法或工具白名单含未注册工具名。
    """
    svc = _expert_service(request)
    user_id = user["sub"]
    current = await svc.get_own(user_id, expert_id)
    if current is None:
        raise HTTPException(404, "专家不存在或不可编辑")
    _validate_expert_tools(body.tool_whitelist)
    await _validate_expert_refs(
        request, user_id, body.skill_refs, body.mcp_refs)
    try:
        expert = await svc.write(
            user_id, dir_name=current["dir_name"], name=body.name,
            avatar=body.avatar, description=body.description,
            system_prompt=body.system_prompt, tool_whitelist=body.tool_whitelist,
            skill_refs=body.skill_refs,
            mcp_refs=body.mcp_refs,
            suggested_prompts=body.suggested_prompts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": expert["_id"], "name": expert["name"]}


@router.delete("/experts/{expert_id}")
async def delete_my_expert(request: Request, expert_id: str,
                           user=Depends(get_current_user)) -> dict:
    """删除自建专家。

    Args:
        request: FastAPI 请求。
        expert_id: 专家 id。
        user: 当前用户。

    Returns:
        {"ok": True}。

    Raises:
        HTTPException: 404 不存在（含内置专家——它们不在用户目录里）。
    """
    if not await _expert_service(request).delete(user["sub"], expert_id):
        raise HTTPException(404, "专家不存在")
    return {"ok": True}


@router.get("/plugins")
async def list_my_plugins(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """我的插件 = 用户已安装的非内置插件（插件无"自建"，恒只有 installed 行）。

    存在的意义：管理员把某插件置为 hidden 后，市场行对该用户消失，但安装记录
    还在（且启动装配仍按记录挂载该插件）。若没有本端点，这行就成了孤儿——
    用户既看不见也卸不掉。故此处**不按可见性过滤**，hidden 行照常返回并标
    revoked，对齐 list_my_skills / list_my_experts 的口径。

    Args:
        request: FastAPI 请求。
        user: 当前用户。

    Returns:
        条目列表（source 恒为 installed，含 installed/enabled/builtin；
        额外带 revoked：管理员已下架该条目）。

    Raises:
        HTTPException: 能力服务未就绪（503）。
    """
    caps = get_capability_service(request)
    user_id = user["sub"]
    rows: list[dict] = []
    # 一次取回安装状态（{id: enabled}），同时得到"装没装"与"启没启用"
    states = await caps.installs.install_states(user_id, "plugin")
    # 遍历目录（而非遍历安装记录）：目录里已不存在的残留记录天然被跳过，
    # 不会因取不到条目而 500（与 list_my_skills / list_my_experts 同构）
    for item in caps.catalog.list_items("plugin"):
        if item.id not in states:
            continue
        pol = await caps.policy.get("plugin", item.id)
        # 内置插件（default_enabled=True）全员自动可用、用户侧只读，不进"我的"
        # （历史安装记录被判定层忽略，这里同口径跳过；只读视图由市场行的
        # default_enabled 标记另行给出）
        if pol["default_enabled"]:
            continue
        rows.append({
            "id": item.id, "name": item.name,
            "description": item.description, "source": "installed",
            "installed": True, "enabled": states[item.id], "builtin": True,
            # 与 list_my_skills 同口径：hidden 即"管理员已下架"，前端据此标已下架、
            # 收掉启停按钮，只留详情页的卸载入口把记录清掉
            "revoked": pol["visibility"] == "hidden",
        })
    return rows
