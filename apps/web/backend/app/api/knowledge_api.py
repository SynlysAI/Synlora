"""知识库代理 API：助手绑定知识库的选项来源（管理页消费）。

只读代理 WeKnora 列表；对知识库的增删改仍在 WeKnora 自身界面操作。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import require_admin
from app.services.weknora_service import WeKnoraError

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge"])


@router.get("")
async def list_knowledge_bases(request: Request,
                               user=Depends(require_admin)) -> list[dict]:
    """知识库列表（代理 WeKnora；admin）。

    Raises:
        HTTPException: WeKnora 未配置（503）或调用失败（502）。
    """
    service = request.app.state.weknora_service
    try:
        return await service.list_kbs()
    except WeKnoraError as exc:
        code = 503 if "未配置" in str(exc) else 502
        raise HTTPException(code, str(exc)) from exc
