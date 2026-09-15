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

from app.api.assistants_api import _validate_provider
from app.api.deps import Repos, get_current_user, get_repos
from app.services import workspace
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


async def _resolve_provider(provider_id: str | None, owner_desc: str,
                            repos: Repos) -> ModelProviderConfig:
    """解析模型服务 id 为后端配置（解密 api_key）。

    Args:
        provider_id: 模型服务 id（会话级覆盖或助手绑定，调用方已按优先级取好）。
        owner_desc: 归属描述（助手/会话名，未指定 id 时的 422 提示用）。
        repos: repo 集中访问对象。

    Returns:
        ModelProviderConfig。

    Raises:
        HTTPException: 未指定/不存在/已停用/解密失败（422）。
    """
    if not provider_id:
        raise HTTPException(422, f"未指定模型服务: {owner_desc}")
    try:
        decrypted = await repos.provider.get_decrypted(provider_id)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    if decrypted is None:
        raise HTTPException(422, f"模型服务不存在: {provider_id}")
    if not decrypted.get("enabled"):
        raise HTTPException(422, f"模型服务已停用: {decrypted.get('name', provider_id)}")
    return ModelProviderConfig(
        name=str(decrypted.get("name", "")),
        base_url=str(decrypted.get("base_url", "")),
        api_key=str(decrypted.get("api_key", "")),
        model_id=str(decrypted.get("model_id", "")),
        multimodal=bool(decrypted.get("multimodal")),
    )


async def _normalize_attachments(request: Request, user: dict, repos: Repos,
                                 file_ids: list[str], project: dict) -> list[dict]:
    """把附件 file_id 归一化为「文件在本会话项目内」的元数据列表。

    附件在草稿态上传时落的是**当时选中的目标项目**，而 agent 只在会话绑定的
    项目目录里跑（workspace_root）：文件躺在别的项目时复制一份进本会话项目
    files/ 并新落一条记录（原文件不动，仍属原项目），保证 agent 用 file 工具
    按相对路径一定能读到。

    Args:
        request: FastAPI 请求（取 project_service）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。
        file_ids: 附件文件记录 id 列表。
        project: 会话解析出的目标项目文档。

    Returns:
        [{file_id, filename, path}]（path 为相对项目根的存储路径）。

    Raises:
        HTTPException: 任一附件不存在/非本人（404）或磁盘文件缺失（422）。
    """
    service = request.app.state.project_service
    root = service.root_for(project)
    out: list[dict] = []
    for fid in file_ids:
        doc = await repos.file.get(fid)
        if doc is None or doc.get("user_id") != user["sub"]:
            raise HTTPException(404, f"附件不存在: {fid}")
        stored_path = str(doc.get("stored_path") or "")
        filename = str(doc.get("filename") or "")
        # 已在本项目内：直接引用
        if str(doc.get("project_id") or "") == str(project["_id"]):
            out.append({"file_id": fid, "filename": filename, "path": stored_path,
                        "size": int(doc.get("size") or 0)})
            continue
        # 跨项目：定位原文件（按记录归属解析）并复制进本会话项目 files/
        owner = None
        if doc.get("project_id"):
            owner = await service.get(user["sub"], str(doc["project_id"]))
        src = service.root_for(owner) / stored_path if owner else None
        if src is None or not src.is_file():
            raise HTTPException(422, f"附件文件已丢失: {filename or fid}")
        target = workspace.unique_target(root / "files", src.name)
        target.write_bytes(src.read_bytes())
        clone = await repos.file.create({
            "user_id": user["sub"],
            "project_id": project["_id"],
            "filename": filename or src.name,
            "stored_path": target.relative_to(root).as_posix(),
            "size": int(doc.get("size") or target.stat().st_size),
            "mime": doc.get("mime") or "application/octet-stream",
        })
        out.append({"file_id": clone["_id"], "filename": clone["filename"],
                    "path": clone["stored_path"], "size": int(clone.get("size") or 0)})
    return out


class SessionCreateBody(BaseModel):
    """新建会话请求体。

    assistant_id 可选：缺省/空表示不选专家，agent 只走平台默认提示词且
    放开全部内置工具（jiuwen 的 "" 卸载专家语义）。
    """

    assistant_id: str | None = None
    title: str = ""
    model_provider_id: str | None = None
    project_id: str | None = None


class SessionUpdateBody(BaseModel):
    """更新会话请求体（改名/归档/切换模型/切换专家）。

    model_provider_id 显式传 null 恢复助手默认；assistant_id 显式传 ""/null
    表示卸载专家（无 persona、工具放开全部内置工具）；两者都用 model_fields_set
    区分"未提供该字段"（不动原值）与"显式传空"（清空）——切换只影响后续轮次
    （每轮 chat 重新从会话文档取助手）。
    """

    title: str | None = None
    archived: bool | None = None
    model_provider_id: str | None = None
    assistant_id: str | None = None


class AttachmentIn(BaseModel):
    """随消息发送的附件引用（file_id 指向已上传到工作区的文件记录）。"""

    file_id: str


class MessageIn(BaseModel):
    """发消息请求体（skills 为本轮勾选的技能名；None/空表示用全部可用技能）。

    attachments 为随消息发送的附件（已上传文件的 file_id 列表）；None/空 =
    无附件，老前端不带该字段时行为不变。
    """

    text: str
    skills: list[str] | None = None
    attachments: list[AttachmentIn] | None = None

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
async def create_session(body: SessionCreateBody, request: Request,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """新建会话（助手可选；不传 assistant_id 表示不选专家）。

    Args:
        body: 请求体；assistant_id 非空才校验助手存在。
        request: 当前请求（取 project_service）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        新建的会话文档。

    Raises:
        HTTPException: 助手不存在（404）、项目不存在或非本人（404）、
            model_provider_id 非法（422）。
    """
    if body.assistant_id and await repos.assistant.get(body.assistant_id) is None:
        raise HTTPException(404, "助手不存在")
    # 绑定项目须属本人且存在：否则会话带着他人的 project_id 落库（运行时虽会回落
    # 到本人项目、不会串目录，但脏数据会让前端按它渲染出不属于该用户的项目）
    if body.project_id and await request.app.state.project_service.get(
            user["sub"], body.project_id) is None:
        raise HTTPException(404, "项目不存在")
    if body.model_provider_id:
        await _validate_provider(body.model_provider_id, repos)
    return await repos.session.create({
        "user_id": user["sub"],
        "assistant_id": body.assistant_id,
        "title": body.title.strip(),
        "archived": False,
        "message_count": 0,
        "model_provider_id": body.model_provider_id,
        "project_id": body.project_id,
    })


@router.patch("/sessions/{sid}")
async def update_session(sid: str, body: SessionUpdateBody,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """改名/归档/切换模型/切换专家（归属校验 404）。

    Args:
        sid: 会话 id。
        body: 请求体。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        更新后的会话文档。

    model_provider_id：传 id 校验后生效；显式传 null 恢复助手默认；
    未提供该字段不动原值（靠 model_fields_set 区分"未提供"与"显式 null"）。
    assistant_id：传非空 id 校验助手存在后写回；显式传 ""/null 卸载专家；
    未提供该字段不动原值（只影响后续轮次，不改历史事件）。

    Raises:
        HTTPException: 会话不存在或非本人（404）、助手不存在（404）、
            model_provider_id 非法（422）。
    """
    doc = await _own_session(sid, user, repos)
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.archived is not None:
        fields["archived"] = body.archived
    if "assistant_id" in body.model_fields_set:
        aid = body.assistant_id
        if aid:
            if await repos.assistant.get(aid) is None:
                raise HTTPException(404, "助手不存在")
            fields["assistant_id"] = aid
        else:
            fields["assistant_id"] = None
    if "model_provider_id" in body.model_fields_set:
        pid = body.model_provider_id
        if pid:
            await _validate_provider(pid, repos)
            fields["model_provider_id"] = pid
        else:
            fields["model_provider_id"] = None
    return await repos.session.update(doc["_id"], fields)


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """删除会话：session doc + 全部事件副本 + JSONL 目录（忽略不存在）。"""
    doc = await _own_session(sid, user, repos)
    for ev in await repos.event.list(filters={"session_id": sid}):
        await repos.event.delete(ev["_id"])
    # 事件目录口径由 workspace 提供（与写入侧 AgentService._jsonl_path 同源）；
    # uid 取会话记录自己的 owner（_own_session 已校验其属当前用户），
    # 用会话记录的 owner，保证与写入侧口径同一个 id
    jsonl_dir = (workspace.user_sessions_root(
        request.app.state.settings.data_root, str(doc["user_id"])) / sid)
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

    Args:
        sid: 会话 id。
        body: 消息体；skills 为本轮勾选的技能名，透传给 AgentService.chat 的
            requested_skills（None/空 = 用全部可用技能，老前端不带该字段时行为不变）。
        request: 当前请求（取 agent_service / project_service）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        SSE 事件流响应。

    会话未绑定专家（或专家已被删）时按 agent 传 None：只走平台默认提示词、
    放开全部内置工具；模型须由会话级 model_provider_id 提供。

    Raises:
        HTTPException: 会话不存在或非本人（404）、provider 未关联/停用（422）、
            并发超限（429）。
    """
    doc = await _own_session(sid, user, repos)
    # 助手指针失效（未选专家/专家已删）不报错，交 chat 走无 persona 路径
    assistant = await repos.assistant.get(doc.get("assistant_id") or "")
    # 模型优先级：会话级覆盖 > 助手绑定 > 第一个启用模型（回落）。回落只作用于
    # 本次发送、**不写回会话**——查看会话必须零写入（否则 updated_at 变"刚刚"，
    # 侧栏时间/排序漂移）；口径与前端 ModelPicker 的 explicitId 一致
    pid = doc.get("model_provider_id") or (assistant or {}).get("model_provider_id")
    if not pid:
        enabled = [p for p in await repos.provider.list() if p.get("enabled")]
        if enabled:
            pid = enabled[0]["_id"]
    owner = doc.get("title") or (assistant or {}).get("name") or sid
    cfg = await _resolve_provider(pid, str(owner), repos)
    service = _agent_service(request)
    # 项目解析（回落路径整体在 ProjectService 的 per-user 锁内，见 resolve_active_project）：
    # 会话绑定的项目优先，失效/未绑定则回落到本人第一个项目，一个都没有就补种默认项目
    project = await request.app.state.project_service.resolve_active_project(
        user["sub"], doc.get("project_id"))
    workspace_root = request.app.state.project_service.root_for(project)
    # 回落后把选中的项目写回会话文档：list_for_user 按 updated_at 倒序，projects[0]
    # 会随用户在其他项目里的改动（改名/上传）而漂移；不写回则未绑定会话每一轮可能
    # 跑进不同目录，上一轮的 output/ 产物看似凭空消失
    if doc.get("project_id") != project["_id"]:
        await repos.session.update(sid, {"project_id": project["_id"]})

    # 附件归一化：文件复制进本会话项目（跨项目时），agent 按相对路径可读
    attachments_meta = None
    if body.attachments:
        attachments_meta = await _normalize_attachments(
            request, user, repos, [a.file_id for a in body.attachments], project)

    # 先 chat（可能 429）：被拒消息不计数、不生成标题、不建 run 记录，无需回滚；
    # 但前面的项目解析可能已按需创建默认工作区 / 回写 project_id——
    # 这些是用户可见的引导副作用（项目列表、会话绑定都会变），注释勿再声称"零副作用"
    try:
        run_id = await service.chat(sid, user, assistant, cfg, body.text,
                                    workspace_root=workspace_root,
                                    requested_skills=body.skills,
                                    attachments=attachments_meta)
    except TooManyRuns as exc:
        raise HTTPException(429, str(exc)) from exc

    active = service.get_active(run_id)  # 紧随 chat 返回（其间无 await），run 必在注册表
    if active is None:  # 防御：仅当未来改动在 chat 与此处之间插入 await 才可能触发
        raise HTTPException(500, "运行句柄丢失")

    # chat 成功后原子累加 message_count（并发请求不互相覆盖）；
    # 累加到 1 的请求负责生成自动标题（首条消息，前 24 字）
    updated = await repos.session.bump_message_count(sid)
    if updated and int(updated["message_count"]) == 1 and not updated.get("title"):
        await repos.session.update(sid, {"title": body.text[:24]})

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


class SteerIn(BaseModel):
    """运行中插话请求体（steering：不打断当前步骤，下一步生效）。"""

    text: str

    @field_validator("text")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        """去首尾空白且拒绝空白串。"""
        if not v.strip():
            raise ValueError("text 不能为空")
        return v.strip()


@router.post("/runs/{run_id}/steer")
async def steer_run(run_id: str, body: SteerIn, request: Request,
                    user=Depends(get_current_user),
                    repos=Depends(get_repos)) -> dict:
    """运行中插话（下一个 step 边界注入为 user 消息并经事件流可见）。

    Raises:
        HTTPException: run 不存在或非本人（404）、run 已结束（409）。
    """
    run = await repos.run.get(run_id)
    if run is None or run.get("user_id") != user["sub"]:
        raise HTTPException(404, "运行不存在")
    ok = await _agent_service(request).steer(run_id, body.text)
    if not ok:
        raise HTTPException(409, "运行已结束，无法插话")
    return {"ok": True}


@router.post("/runs/{run_id}/answer")
async def answer_run(run_id: str, body: SteerIn, request: Request,
                     user=Depends(get_current_user),
                     repos=Depends(get_repos)) -> dict:
    """回答运行中 ask_user 提出的问题（resolve 等待中的 future，工具随即返回）。

    Raises:
        HTTPException: run 不存在或非本人（404）、当前无待回答问题（409）。
    """
    run = await repos.run.get(run_id)
    if run is None or run.get("user_id") != user["sub"]:
        raise HTTPException(404, "运行不存在")
    ok = await _agent_service(request).answer(run_id, body.text)
    if not ok:
        raise HTTPException(409, "当前没有等待回答的问题")
    return {"ok": True}
