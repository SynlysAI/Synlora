"""后台任务查询与本人取消 API。"""
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


# 列表项保留的字段：前端的任务面板据此渲染状态点/名称/状态/时长与异常提示。
# 刻意剔除 result（可达 4000 字）/params/user_identity/workspace_root——
# 面板是轮询拉取的（秒级），带原文等于每次白搬几十 KB 且把内部字段送到浏览器；
# 需要原文的场景走 GET /jobs/{job_id}。
_LIST_FIELDS = ("_id", "kind", "label", "status", "session_id", "created_at",
                "updated_at", "ended_at", "error", "poll_failures", "backend",
                "cancel_requested")

_DETAIL_FIELDS = (*_LIST_FIELDS, "result", "exit_code", "timed_out", "truncated",
                  "error_code", "workspace_owner")


def _brief(doc: dict) -> dict:
    """任务文档 → 列表项（只留 `_LIST_FIELDS` 里存在的字段）。

    Args:
        doc: 任务文档。

    Returns:
        精简后的任务条目。
    """
    return {k: doc[k] for k in _LIST_FIELDS if k in doc}


@router.get("/jobs")
async def list_jobs(request: Request, session_id: str | None = None,
                    user=Depends(get_current_user)) -> list[dict]:
    """当前用户的后台任务列表（可按会话过滤，创建时间升序）。

    Args:
        request: 当前请求。
        session_id: 可选会话过滤。
        user: 当前用户 payload。

    Returns:
        任务条目列表（字段见 `_LIST_FIELDS`，不含 result 原文）。
    """
    service = _job_service(request)
    if session_id:
        docs = await service.list_for_session(session_id)
        return [_brief(d) for d in docs if str(d.get("user_id")) == user["sub"]]
    return [_brief(d) for d in await service.list_for_user(user["sub"])]


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
    return {key: doc[key] for key in _DETAIL_FIELDS if key in doc}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request,
                     user=Depends(get_current_user)) -> dict:
    """取消本人后台任务，非本人按不存在处理。"""
    result = await _job_service(request).cancel(job_id, user_id=user["sub"])
    if result.error == "not_found":
        raise HTTPException(404, "任务不存在")
    if not result.ok:
        raise HTTPException(409, result.content)
    return {"ok": True, **result.data}
