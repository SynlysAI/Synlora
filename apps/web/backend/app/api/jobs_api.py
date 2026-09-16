"""后台任务查询 API（只读：提交/取消经由对话工具，不单独开口子）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _job_service(request: Request):
    """从 app.state 取任务服务。

    Args:
        request: 当前请求。

    Returns:
        JobService 实例。
    """
    return request.app.state.job_service


@router.get("/jobs")
async def list_jobs(request: Request, session_id: str | None = None,
                    user=Depends(get_current_user)) -> list[dict]:
    """当前用户的后台任务列表（可按会话过滤，创建时间升序）。

    Args:
        request: 当前请求。
        session_id: 可选会话过滤。
        user: 当前用户 payload。

    Returns:
        任务文档列表。
    """
    service = _job_service(request)
    if session_id:
        docs = await service.list_for_session(session_id)
        return [d for d in docs if str(d.get("user_id")) == user["sub"]]
    return await service.list_for_user(user["sub"])


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request,
                  user=Depends(get_current_user)) -> dict:
    """任务详情（非本人 404，不泄露存在性）。

    Args:
        job_id: 任务 id。
        request: 当前请求。
        user: 当前用户 payload。

    Returns:
        任务文档。

    Raises:
        HTTPException: 任务不存在或非本人（404）。
    """
    doc = await _job_service(request).get(job_id)
    if doc is None or str(doc.get("user_id")) != user["sub"]:
        raise HTTPException(404, "任务不存在")
    return doc
