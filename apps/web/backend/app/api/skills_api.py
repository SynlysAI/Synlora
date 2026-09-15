"""技能 API：全局技能目录的增删改查 + SKILL.md 导入导出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin
from app.services.skill_service import parse_skill_md, render_skill_md

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillBody(BaseModel):
    """技能字段（content 为 SKILL.md 正文，不含 frontmatter）。"""

    name: str
    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


class SkillUpdateBody(BaseModel):
    """更新请求体（技能名以路径参数为准，故此处不带 name）。"""

    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


class SkillImportBody(BaseModel):
    """导入请求体。"""

    text: str


@router.get("")
async def list_skills(request: Request, user=Depends(get_current_user)):
    """列出技能（普通用户按可见性过滤，管理员看全部）。

    Args:
        request: FastAPI 请求（取 app.state.skill_service 与 capability_service）。
        user: 当前登录用户。

    Returns:
        技能字典列表。
    """
    skills = request.app.state.skill_service.list_skills(user_id=user["sub"])
    caps = getattr(request.app.state, "capability_service", None)
    if caps is None or user.get("role") == "admin":
        return skills
    # 黑名单口径：只剔除策略隐藏的内置技能与不可见插件的技能；公共目录里管理员
    # 自建/导入的技能不属能力目录条目，始终可见（否则普通用户看不到它们）
    hidden = await caps.hidden_skill_names(user["sub"])
    return [s for s in skills if s["name"] not in hidden]


@router.get("/{name}/export", response_class=PlainTextResponse)
async def export_skill(request: Request, name: str, user=Depends(get_current_user)):
    """导出 SKILL.md 原文。

    Args:
        request: FastAPI 请求。
        name: 技能名。
        user: 当前登录用户。

    Returns:
        SKILL.md 文本。

    Raises:
        HTTPException: 404 表示技能不存在。
    """
    for skill in request.app.state.skill_service.list_skills(user_id=user["sub"]):
        if skill["name"] == name:
            return PlainTextResponse(render_skill_md(skill))
    raise HTTPException(status_code=404, detail="技能不存在")


@router.post("", status_code=201)
async def create_skill(request: Request, body: SkillBody, user=Depends(require_admin)):
    """新建技能（写 {data_root}/skills/<name>/SKILL.md）。

    Args:
        request: FastAPI 请求。
        body: 技能字段。
        user: 当前登录用户（须为管理员）。

    Returns:
        新建的技能字典。

    Raises:
        HTTPException: 422 表示技能名不合法。
    """
    try:
        return request.app.state.skill_service.write_skill(
            name=body.name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{name}")
async def update_skill(request: Request, name: str, body: SkillUpdateBody,
                       user=Depends(require_admin)):
    """覆盖写技能（技能名不可改，改名前请另建再删）。

    Args:
        request: FastAPI 请求。
        name: 技能名（路径参数，以它为准；请求体不带 name）。
        body: 技能字段（不含 name）。
        user: 当前登录用户（须为管理员）。

    Returns:
        更新后的技能字典。

    Raises:
        HTTPException: 422 表示技能名不合法。
    """
    try:
        return request.app.state.skill_service.write_skill(
            name=name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{name}")
async def delete_skill(request: Request, name: str, user=Depends(require_admin)):
    """删除技能目录（只作用于可写公共层）。

    Args:
        request: FastAPI 请求。
        name: 技能名。
        user: 当前登录用户（须为管理员）。

    Returns:
        {"ok": True}。

    Raises:
        HTTPException: 404 表示技能不存在；内置技能来自只读根
            （catalog/skills），不落公共层目录，删除同样返回 404。
    """
    if not request.app.state.skill_service.delete_skill(name):
        raise HTTPException(status_code=404, detail="技能不存在")
    return {"ok": True}


@router.post("/import", status_code=201)
async def import_skill(request: Request, body: SkillImportBody,
                       user=Depends(require_admin)):
    """从 SKILL.md 文本导入。

    Args:
        request: FastAPI 请求。
        body: 含 SKILL.md 全文。
        user: 当前登录用户（须为管理员）。

    Returns:
        导入后的技能字典。

    Raises:
        HTTPException: 422 表示文本不是合法的 SKILL.md。
    """
    try:
        parsed = parse_skill_md(body.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return request.app.state.skill_service.write_skill(
        name=parsed["name"], description=parsed["description"], content=parsed["content"],
        version=parsed["version"], author=parsed["author"],
        tags=parsed["tags"], allowed_tools=parsed["allowed_tools"])
