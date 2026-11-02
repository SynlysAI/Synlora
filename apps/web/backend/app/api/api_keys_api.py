"""个人 yxkey 访问令牌管理 API。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.api.deps import get_current_user, get_repos
from app.db.repos import APIKeyRepo
from app.services.api_keys import generate_api_key, public_api_key

router = APIRouter(prefix="/api/v1/user/apikeys", tags=["api-keys"])


class APIKeyCreateBody(BaseModel):
    """创建访问令牌请求。"""

    name: str
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """去除首尾空白并拒绝空名称。"""
        value = value.strip()
        if not value:
            raise ValueError("名称不能为空")
        return value[:100]


class APIKeyUpdateBody(BaseModel):
    """更新访问令牌请求。"""

    is_enabled: bool


async def _own_key(repo: APIKeyRepo, key_id: str, user: dict) -> dict:
    """读取并校验当前用户拥有的令牌。

    Args:
        repo: 访问令牌仓库。
        key_id: 令牌标识。
        user: 当前用户 payload。

    Returns:
        令牌文档。
    """
    doc = await repo.get(key_id)
    if doc is None or doc.get("user_id") != user["sub"]:
        raise HTTPException(404, "访问令牌不存在")
    return doc


@router.get("")
async def list_keys(user=Depends(get_current_user), repos=Depends(get_repos)) -> dict:
    """列出当前用户的访问令牌。"""
    docs = await repos.api_key.list_for_user(str(user["sub"]))
    return {"items": [public_api_key(doc) for doc in docs]}


@router.post("", status_code=201)
async def create_key(
    body: APIKeyCreateBody, user=Depends(get_current_user), repos=Depends(get_repos)
) -> dict:
    """创建访问令牌；明文 key 只在本响应中出现一次。"""
    raw, key_hash, key_prefix = generate_api_key()
    now = time.time()
    doc = await repos.api_key.create({
        "_id": uuid.uuid4().hex,
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "name": body.name,
        "user_id": str(user["sub"]),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or "user"),
        "is_enabled": True,
        "expires_at": body.expires_at.timestamp() if body.expires_at else None,
        "last_used_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return {"key": raw, "item": public_api_key(doc)}


@router.patch("/{key_id}")
async def update_key(
    key_id: str,
    body: APIKeyUpdateBody,
    user=Depends(get_current_user),
    repos=Depends(get_repos),
) -> dict:
    """启用或停用访问令牌。"""
    await _own_key(repos.api_key, key_id, user)
    doc = await repos.api_key.update(key_id, {
        "is_enabled": body.is_enabled,
        "updated_at": time.time(),
    })
    return public_api_key(doc or {})


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: str, user=Depends(get_current_user), repos=Depends(get_repos)
) -> None:
    """删除访问令牌。"""
    await _own_key(repos.api_key, key_id, user)
    await repos.api_key.delete(key_id)
