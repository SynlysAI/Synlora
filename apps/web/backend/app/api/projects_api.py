"""项目 API：列表/新建/删除 + 目录树懒加载。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import get_current_user

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreateBody(BaseModel):
    """新建项目请求体。"""

    name: str


@router.get("")
async def list_projects(request: Request, user=Depends(get_current_user)):
    """列出当前用户的项目（首次访问会迁移旧布局并补种默认项目）。

    Args:
        request: FastAPI 请求（取 app.state.project_service）。
        user: 当前登录用户。

    Returns:
        未归档项目文档列表。
    """
    return await request.app.state.project_service.list_projects(user["sub"])


@router.post("", status_code=201)
async def create_project(request: Request, body: ProjectCreateBody,
                         user=Depends(get_current_user)):
    """新建项目（重名自动加目录后缀，不失败）。

    Args:
        request: FastAPI 请求。
        body: 请求体（项目名）。
        user: 当前登录用户。

    Returns:
        新建的项目文档。

    Raises:
        HTTPException: 422 表示项目名不合法。
    """
    try:
        return await request.app.state.project_service.create_project(user["sub"], body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{pid}")
async def delete_project(request: Request, pid: str, user=Depends(get_current_user)):
    """删除项目（记录 + 磁盘目录）。

    Args:
        request: FastAPI 请求。
        pid: 项目 id。
        user: 当前登录用户。

    Returns:
        {"ok": True}。

    Raises:
        HTTPException: 404 表示项目不存在或不属于该用户。
    """
    ok = await request.app.state.project_service.delete_project(user["sub"], pid)
    if not ok:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True}


@router.get("/{pid}/tree")
async def list_tree(request: Request, pid: str, path: str = Query(""),
                    user=Depends(get_current_user)):
    """列出项目内某目录的一级条目（path 为空表示项目根）。

    Args:
        request: FastAPI 请求。
        pid: 项目 id。
        path: 相对项目根的目录路径。
        user: 当前登录用户。

    Returns:
        条目列表（name/path/is_dir/size/mtime）。

    Raises:
        HTTPException: 404 表示项目不存在、路径越界或目标不是目录。
    """
    try:
        return await request.app.state.project_service.list_dir(user["sub"], pid, path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
