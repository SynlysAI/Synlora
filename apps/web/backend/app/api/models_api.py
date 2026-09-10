"""模型服务（provider）管理 API：CRUD + 连通性测试，api_key 永不出明文。"""
from __future__ import annotations

import contextlib
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from synlys_harness import Message, ModelProviderConfig, OpenAICompatibleBackend, Role

from app.api.deps import get_current_user, get_repos, require_admin

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _public(doc: dict) -> dict:
    """provider 公共视图（显式投影，杜绝任何 key 字段外泄）。

    Args:
        doc: 存储层完整文档。

    Returns:
        形如 {_id, name, base_url, model_id, enabled, has_key} 的字典。
    """
    return {
        "_id": doc["_id"],
        "name": doc.get("name", ""),
        "base_url": doc.get("base_url", ""),
        "model_id": doc.get("model_id", ""),
        "enabled": bool(doc.get("enabled")),
        "has_key": bool(doc.get("api_key_enc")),
    }


def _check_base_url(url: str) -> str:
    """校验并规范化 base_url。

    Args:
        url: 去空白后的 URL。

    Returns:
        原样返回。

    Raises:
        HTTPException: 非 http(s):// 前缀（422）。
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(422, "base_url 必须以 http:// 或 https:// 开头")
    return url


class ProviderCreateBody(BaseModel):
    """新建 provider 请求体（api_key 加密入库）。"""

    name: str
    base_url: str
    api_key: str = ""
    model_id: str
    enabled: bool = True

    @field_validator("name", "base_url", "model_id")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        """去首尾空白且拒绝空白串。"""
        if not v.strip():
            raise ValueError("不能为空")
        return v.strip()


class ProviderUpdateBody(BaseModel):
    """更新 provider 请求体（全字段可选；api_key 提供时重加密覆写）。"""

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    enabled: bool | None = None


@router.get("")
async def list_models(all: bool = False, user=Depends(get_current_user),
                      repos=Depends(get_repos)) -> list[dict]:
    """模型列表：全员只回 enabled；管理员 all=true 看全部（无 key 字段）。"""
    docs = await repos.provider.list()
    if not (all and user.get("role") == "admin"):
        docs = [d for d in docs if d.get("enabled")]
    return [_public(d) for d in docs]


@router.post("", status_code=201)
async def create_provider(body: ProviderCreateBody, user=Depends(require_admin),
                          repos=Depends(get_repos)) -> dict:
    """新建模型服务（require_admin）。

    Raises:
        HTTPException: base_url 非法（422）、同名（409）。
    """
    _check_base_url(body.base_url)
    if await repos.provider.list(filters={"name": body.name}):
        raise HTTPException(409, f"同名模型服务已存在: {body.name}")
    doc = await repos.provider.create({
        "name": body.name,
        "base_url": body.base_url,
        "api_key": body.api_key,
        "model_id": body.model_id,
        "enabled": body.enabled,
    })
    return _public(doc)


@router.patch("/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdateBody,
                          user=Depends(require_admin),
                          repos=Depends(get_repos)) -> dict:
    """更新模型服务（require_admin；body 含 api_key 时走重加密流程）。

    Raises:
        HTTPException: 不存在（404）、字段非法（422）、改名撞名（409）。
    """
    doc = await repos.provider.get(provider_id)
    if doc is None:
        raise HTTPException(404, "模型服务不存在")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
        if not fields["name"]:
            raise HTTPException(422, "name 不能为空")
    if body.base_url is not None:
        fields["base_url"] = _check_base_url(body.base_url.strip())
    if body.model_id is not None:
        fields["model_id"] = body.model_id.strip()
        if not fields["model_id"]:
            raise HTTPException(422, "model_id 不能为空")
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if fields.get("name") and fields["name"] != doc.get("name"):
        if await repos.provider.list(filters={"name": fields["name"]}):
            raise HTTPException(409, f"同名模型服务已存在: {fields['name']}")
    updated = await repos.provider.update_with_key(provider_id, fields,
                                                   api_key=body.api_key)
    return _public(updated)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str, user=Depends(require_admin),
                          repos=Depends(get_repos)) -> dict:
    """删除模型服务（require_admin；被助手引用时 409）。

    Raises:
        HTTPException: 不存在（404）、被助手引用（409）。
    """
    if await repos.provider.get(provider_id) is None:
        raise HTTPException(404, "模型服务不存在")
    # model_provider_id 非索引列，取全量后 Python 侧过滤
    assistants = await repos.assistant.list()
    refs = [a for a in assistants if a.get("model_provider_id") == provider_id]
    if refs:
        names = ", ".join(str(a.get("name", a["_id"])) for a in refs)
        raise HTTPException(409, f"模型服务被助手引用（{names}），先解除关联再删除")
    await repos.provider.delete(provider_id)
    return {"ok": True}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, user=Depends(require_admin),
                        repos=Depends(get_repos)) -> dict:
    """连通性测试：解密 key 构造后端，发 "ping" 取首个流事件即断开。

    Returns:
        {ok, latency_ms, error}；上游异常转业务结果（不 500）。

    Raises:
        HTTPException: provider 不存在（404）。
    """
    start = time.perf_counter()
    try:
        decrypted = await repos.provider.get_decrypted(provider_id)
        if decrypted is None:
            raise HTTPException(404, "模型服务不存在")
        cfg = ModelProviderConfig(
            name=str(decrypted.get("name", "")),
            base_url=str(decrypted.get("base_url", "")),
            api_key=str(decrypted.get("api_key", "")),
            model_id=str(decrypted.get("model_id", "")),
        )
        backend = OpenAICompatibleBackend(cfg)
        messages = [Message(role=Role.USER, content="ping")]
        async with contextlib.aclosing(backend.stream(messages)) as stream:
            async for _ in stream:
                break  # 首个事件即认为连通，立即断开
        return {"ok": True, "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "error": None}
    except HTTPException:
        raise
    except Exception as exc:  # 连接失败/认证失败/解密失败 → 业务结果而非 500
        return {"ok": False, "latency_ms": None, "error": str(exc)}
