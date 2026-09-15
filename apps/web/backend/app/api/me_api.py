"""「我的」端点：用户自建技能（后续任务在此文件追加专家）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user
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
