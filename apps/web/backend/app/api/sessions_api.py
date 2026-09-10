"""会话 API：CRUD、事件回放、SSE 对话端点与运行取消。

SSE 端点行为：event=事件类型（turn/start 等）、data=事件 JSON、id=seq；
断连不 cancel（run 由 AgentService._drive 后台执行完落盘，客户端经
events?after_seq=N 补齐）；cancel 仅 POST /runs/{run_id}/cancel。
"""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse
from synlys_harness import ModelProviderConfig

from app.api.deps import Repos, get_current_user, get_repos
from app.services.agent_service import AgentService, TooManyRuns

router = APIRouter(prefix="/api/v1", tags=["sessions"])


def _agent_service(request: Request) -> AgentService:
    """从 app.state 取编排服务。

    Args:
        request: 当前请求。

    Returns:
        AgentService 实例。
    """
    return request.app.state.agent_service


async def _own_session(sid: str, user: dict, repos: Repos) -> dict:
    """取当前用户自己的会话（不存在/非本人统一 404，不泄露存在性）。

    Args:
        sid: 会话 id。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        会话文档。
    """
    doc = await repos.session.get(sid)
    if doc is None or doc.get("user_id") != user["sub"]:
        raise HTTPException(404, "会话不存在")
    return doc


async def _resolve_provider(assistant: dict, repos: Repos) -> ModelProviderConfig:
    """解析助手关联的模型服务为后端配置（解密 api_key）。

    Args:
        assistant: 助手文档。
        repos: repo 集中访问对象。

    Returns:
        ModelProviderConfig。

    Raises:
        HTTPException: 未关联/不存在/已停用/解密失败（422）。
    """
    pid = assistant.get("model_provider_id")
    if not pid:
        raise HTTPException(
            422, f"助手未关联模型服务: {assistant.get('name', assistant.get('_id', ''))}")
    try:
        decrypted = await repos.provider.get_decrypted(pid)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    if decrypted is None:
        raise HTTPException(422, f"模型服务不存在: {pid}")
    if not decrypted.get("enabled"):
        raise HTTPException(422, f"模型服务已停用: {decrypted.get('name', pid)}")
    return ModelProviderConfig(
        name=str(decrypted.get("name", "")),
        base_url=str(decrypted.get("base_url", "")),
        api_key=str(decrypted.get("api_key", "")),
        model_id=str(decrypted.get("model_id", "")),
    )


class SessionCreateBody(BaseModel):
    """新建会话请求体。"""

    assistant_id: str
    title: str = ""


class SessionUpdateBody(BaseModel):
    """更新会话请求体（改名/归档）。"""

    title: str | None = None
    archived: bool | None = None


class MessageIn(BaseModel):
    """发消息请求体。"""

    text: str

    @field_validator("text")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        """去首尾空白且拒绝空白串。"""
        if not v.strip():
            raise ValueError("text 不能为空")
        return v.strip()


@router.get("/sessions")
async def list_sessions(user=Depends(get_current_user),
                        repos=Depends(get_repos)) -> list[dict]:
    """当前用户会话列表（按 updated_at 倒序；时间戳浮点存 TEXT，Python 端排序）。"""
    docs = await repos.session.list(filters={"user_id": user["sub"]})
    return sorted(docs, key=lambda d: float(d.get("updated_at") or 0), reverse=True)


@router.post("/sessions", status_code=201)
async def create_session(body: SessionCreateBody, user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """新建会话。

    Raises:
        HTTPException: 助手不存在（404）。
    """
    if await repos.assistant.get(body.assistant_id) is None:
        raise HTTPException(404, "助手不存在")
    return await repos.session.create({
        "user_id": user["sub"],
        "assistant_id": body.assistant_id,
        "title": body.title.strip(),
        "archived": False,
        "message_count": 0,
    })


@router.patch("/sessions/{sid}")
async def update_session(sid: str, body: SessionUpdateBody,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """改名/归档（归属校验 404）。"""
    doc = await _own_session(sid, user, repos)
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.archived is not None:
        fields["archived"] = body.archived
    return await repos.session.update(doc["_id"], fields)


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """删除会话：session doc + 全部事件副本 + JSONL 目录（忽略不存在）。"""
    doc = await _own_session(sid, user, repos)
    for ev in await repos.event.list(filters={"session_id": sid}):
        await repos.event.delete(ev["_id"])
    jsonl_dir = request.app.state.settings.data_root / "sessions" / sid
    shutil.rmtree(jsonl_dir, ignore_errors=True)
    await repos.session.delete(doc["_id"])
    return {"ok": True}


@router.get("/sessions/{sid}/events")
async def list_events(sid: str, request: Request, after_seq: int = -1,
                      user=Depends(get_current_user),
                      repos=Depends(get_repos)) -> list[dict]:
    """事件回放（SSE 断连后经 after_seq 增量补齐；数据源为 DB 副本）。

    after_seq 语义为"客户端已收到的最大 seq"，默认 -1 返回全部（seq 从 0 起）。
    """
    await _own_session(sid, user, repos)
    events = await _agent_service(request).events_after(sid, after_seq)
    return [{"seq": e.seq, "type": e.type.value, "payload": e.payload, "ts": e.ts}
            for e in events]


@router.post("/sessions/{sid}/messages")
async def send_message(sid: str, body: MessageIn, request: Request,
                       user=Depends(get_current_user),
                       repos=Depends(get_repos)) -> EventSourceResponse:
    """发消息并以 SSE 流式返回本轮事件（event=事件类型、data=JSON、id=seq）。

    Raises:
        HTTPException: 会话/助手不存在（404）、provider 未关联/停用（422）、
            并发超限（429）。
    """
    doc = await _own_session(sid, user, repos)
    assistant = await repos.assistant.get(doc.get("assistant_id", ""))
    if assistant is None:
        raise HTTPException(404, "助手不存在")
    cfg = await _resolve_provider(assistant, repos)
    service = _agent_service(request)

    count = int(doc.get("message_count") or 0)
    fields: dict = {"message_count": count + 1}
    if count == 0 and not doc.get("title"):
        fields["title"] = body.text[:24]  # 首条消息自动生成标题（前 24 字）
    await repos.session.update(sid, fields)
    try:
        run_id = await service.chat(sid, user, assistant, cfg, body.text)
    except TooManyRuns as exc:
        # 429 回滚消息计数/标题，拒绝的消息不落痕迹
        revert: dict = {"message_count": count}
        if "title" in fields:
            revert["title"] = doc.get("title", "")
        await repos.session.update(sid, revert)
        raise HTTPException(429, str(exc)) from exc

    active = service.get_active(run_id)  # 紧随 chat 返回（其间无 await），run 必在注册表
    if active is None:  # 防御：仅当未来改动在 chat 与此处之间插入 await 才可能触发
        raise HTTPException(500, "运行句柄丢失")

    async def sse_gen():
        """SSE 事件生成器：消费 ActiveRun.queue，None 哨兵结束（断连不 cancel run）。"""
        while True:
            ev = await active.queue.get()
            if ev is None:
                break
            yield {"event": ev.type.value, "data": ev.model_dump_json(), "id": str(ev.seq)}

    return EventSourceResponse(sse_gen())


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request,
                     user=Depends(get_current_user),
                     repos=Depends(get_repos)) -> dict:
    """取消运行（用户显式停止；run 不存在或非本人 404）。"""
    run = await repos.run.get(run_id)
    if run is None or run.get("user_id") != user["sub"]:
        raise HTTPException(404, "运行不存在")
    ok = await _agent_service(request).cancel(run_id)
    return {"ok": ok}
