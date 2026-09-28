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

from app.api.assistants_api import _validate_provider
from app.api.deps import Repos, get_current_user, get_repos
from app.services import workspace
from app.services.agent_service import AgentService, TooManyRuns
from app.services.research_context import ResearchContextError, ResearchContextMetadata
from app.services.session_runtime import NoUsableProvider, resolve_session_runtime

router = APIRouter(prefix="/api/v1", tags=["sessions"])


async def _ensure_assistant_selectable(request: Request, user: dict,
                                       assistant_id: str, repos: Repos) -> None:
    """校验会话可绑定的助手：本人自建专家（文件）或集合里的管理员资产。

    他人自建专家不进集合、文件也只存在作者目录，天然查不到 → 统一 404
    （不泄露存在性）。目录内置/插件专家的"需安装才可用"不在此校验
    （与列表可见性口径解耦，历史行为是仅查存在性）。

    Args:
        request: 当前请求（取 expert_service）。
        user: 当前用户 payload。
        assistant_id: 待绑定的助手 id。
        repos: repo 集中访问对象。

    Raises:
        HTTPException: 助手不存在（404）。
    """
    svc = getattr(request.app.state, "expert_service", None)
    if svc is not None and await svc.get_own(user["sub"], assistant_id) is not None:
        return
    if await repos.assistant.get(assistant_id) is None:
        raise HTTPException(404, "助手不存在")


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


async def _normalize_attachments(request: Request, user: dict, repos: Repos,
                                 file_ids: list[str], root,
                                 ownership: dict) -> list[dict]:
    """把附件 file_id 归一化为「文件在本会话工作根内」的元数据列表。

    附件在草稿态上传时落的是**当时选中的目标项目**，而 agent 只在会话的工作根里跑
    （workspace_root：绑定项目 = 项目目录，无绑定 = sessions/{sid}）：文件躺在别的
    根时复制一份进本会话根 files/ 并新落一条记录（原文件不动，仍属原处），保证
    agent 用 file 工具按相对路径一定能读到。

    Args:
        request: FastAPI 请求（取 project_service）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。
        file_ids: 附件文件记录 id 列表。
        root: 本会话的工作根（项目根或会话根）。
        ownership: 副本记录的归属字段（{"project_id": pid} 或 {"session_id": sid}）。

    Returns:
        [{file_id, filename, path}]（path 为相对工作根的存储路径）。

    Raises:
        HTTPException: 任一附件不存在/非本人（404）或磁盘文件缺失（422）。
    """
    service = request.app.state.project_service
    out: list[dict] = []
    for fid in file_ids:
        doc = await repos.file.get(fid)
        if doc is None or doc.get("user_id") != user["sub"]:
            raise HTTPException(404, f"附件不存在: {fid}")
        stored_path = str(doc.get("stored_path") or "")
        filename = str(doc.get("filename") or "")
        # 已在本会话根内（归属字段一致）：直接引用
        if all(str(doc.get(k) or "") == str(v) for k, v in ownership.items()):
            out.append({"file_id": fid, "filename": filename, "path": stored_path,
                        "size": int(doc.get("size") or 0)})
            continue
        # 跨根：定位原文件（按记录归属解析）并复制进本会话根 files/
        if doc.get("session_id"):
            src = workspace.session_root(
                request.app.state.settings.data_root, user["sub"],
                str(doc["session_id"])) / stored_path
        else:
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
            **ownership,
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
    enabled_plugins 为会话级插件开关（**默认全关**，照 jiuwen「+ 扩展面板」）：
    缺省 None 与空列表同为"本会话不启用任何插件"；列表 = 只启用这些插件的
    工具、配置、技能与播种专家。开关切换即 PATCH 落库，刷新不丢。
    enabled_mcp 为会话级 MCP 附加（语义同 enabled_plugins）：缺省 None 与空
    列表同为"本会话不附加任何 MCP"；列表 = 附加这些（用户自建 ∪ 可见公共）。
    """

    assistant_id: str | None = None
    title: str = ""
    model_provider_id: str | None = None
    project_id: str | None = None
    enabled_plugins: list[str] | None = None
    enabled_mcp: list[str] | None = None
    research_context: ResearchContextMetadata | None = None


class SessionUpdateBody(BaseModel):
    """更新会话请求体（改名/归档/切换模型/切换专家/插件开关）。

    model_provider_id 显式传 null 恢复助手默认；assistant_id 显式传 ""/null
    表示卸载专家（无 persona、工具放开全部内置工具）；enabled_plugins 显式传
    null 恢复"跟随用户级可见集"；三者都用 model_fields_set 区分"未提供该字段"
    （不动原值）与"显式传空"（清空）——切换只影响后续轮次（每轮 chat 重新从
    会话文档取值）。
    """

    title: str | None = None
    archived: bool | None = None
    model_provider_id: str | None = None
    assistant_id: str | None = None
    enabled_plugins: list[str] | None = None
    enabled_mcp: list[str] | None = None


async def _validate_plugin_ids(request: Request, plugin_ids: list[str]) -> None:
    """校验会话级插件开关里的插件 id 都真实存在（不存在即 404，防脏数据落库）。

    Args:
        request: FastAPI 请求（取能力服务）。
        plugin_ids: 插件 id 列表。

    Raises:
        HTTPException: 任一插件不存在（404）。
    """
    service = request.app.state.capability_service
    for pid in plugin_ids:
        if not await service.exists("plugin", pid):
            raise HTTPException(404, f"插件不存在: {pid}")


def _validate_research_plugin_scope(plugin_ids: list[str], metadata: ResearchContextMetadata) -> None:
    """确保科研会话插件不超出 Plane Context 授权范围。

    Args:
        plugin_ids: 请求启用的会话插件 ID。
        metadata: Plane 签发的 agent-context.v2 元数据。

    Raises:
        HTTPException: 任一插件不在 ``allowed_plugins`` 内时返回 403。
    """
    invalid = sorted(set(plugin_ids) - set(metadata.allowed_plugins or []))
    if invalid:
        raise HTTPException(403, f"科研上下文未授权插件: {', '.join(invalid)}")


async def _validate_mcp_ids(request: Request, user_id: str, mcp_ids: list[str]) -> None:
    """校验会话级 MCP 附加里的 id 可用（勾选不能放大可见性）。

    每个 id 必须是：本人自建 MCP（存在即校验通过，启用与否运行期再判），
    或对该用户可见的公共 MCP（内置或已装且启用）。

    Args:
        request: FastAPI 请求（取 MCP 与能力服务）。
        user_id: 用户 sub。
        mcp_ids: MCP id 列表。

    Raises:
        HTTPException: 任一 id 不可用（404，不泄露存在性）。
    """
    mcp_service = getattr(request.app.state, "mcp_service", None)
    capability = getattr(request.app.state, "capability_service", None)
    for mcp_id in mcp_ids:
        if mcp_service is not None and await mcp_service.get(user_id, mcp_id):
            continue
        if capability is not None and await capability.is_visible(user_id, "mcp", mcp_id):
            continue
        raise HTTPException(404, f"MCP 不存在或不可用: {mcp_id}")


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
    research_context: ResearchContextMetadata | None = None

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
    if body.assistant_id:
        await _ensure_assistant_selectable(request, user, body.assistant_id, repos)
    # 绑定项目须属本人且存在：否则会话带着他人的 project_id 落库（运行时虽会回落
    # 到本人项目、不会串目录，但脏数据会让前端按它渲染出不属于该用户的项目）
    if body.project_id and await request.app.state.project_service.get(
            user["sub"], body.project_id) is None:
        raise HTTPException(404, "项目不存在")
    if body.model_provider_id:
        await _validate_provider(body.model_provider_id, repos)
    if body.research_context is not None:
        context_token = request.headers.get("X-Research-Context-Token", "")
        if not context_token:
            raise HTTPException(401, "缺少科研上下文令牌")
        try:
            await request.app.state.research_context_adapter.validate(
                token=context_token, expected=body.research_context)
        except ResearchContextError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
    if body.enabled_plugins is not None:
        await _validate_plugin_ids(request, body.enabled_plugins)
        if body.research_context is not None:
            _validate_research_plugin_scope(body.enabled_plugins, body.research_context)
    if body.enabled_mcp is not None:
        await _validate_mcp_ids(request, user["sub"], body.enabled_mcp)
    return await repos.session.create({
        "user_id": user["sub"],
        "assistant_id": body.assistant_id,
        "title": body.title.strip(),
        "archived": False,
        "message_count": 0,
        "model_provider_id": body.model_provider_id,
        "project_id": body.project_id,
        "enabled_plugins": body.enabled_plugins,
        "enabled_mcp": body.enabled_mcp,
        "research_context": (
            body.research_context.model_dump(mode="json")
            if body.research_context is not None else None
        ),
    })


@router.patch("/sessions/{sid}")
async def update_session(sid: str, body: SessionUpdateBody, request: Request,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """改名/归档/切换模型/切换专家/插件开关（归属校验 404）。

    Args:
        sid: 会话 id。
        body: 请求体。
        request: 当前请求（校验插件 id 用）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        更新后的会话文档。

    model_provider_id：传 id 校验后生效；显式传 null 恢复助手默认；
    未提供该字段不动原值（靠 model_fields_set 区分"未提供"与"显式 null"）。
    assistant_id：传非空 id 校验助手存在后写回；显式传 ""/null 卸载专家；
    未提供该字段不动原值（只影响后续轮次，不改历史事件）。
    enabled_plugins：传列表校验插件存在后写回（会话级插件开关，默认全关）；
    显式传 null 与空列表同义（本会话不启用任何插件）；未提供该字段不动原值。

    Raises:
        HTTPException: 会话不存在或非本人（404）、助手/插件不存在（404）、
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
            await _ensure_assistant_selectable(request, user, aid, repos)
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
    if "enabled_plugins" in body.model_fields_set:
        if body.enabled_plugins is not None:
            await _validate_plugin_ids(request, body.enabled_plugins)
            research_context = doc.get("research_context")
            if research_context is not None:
                _validate_research_plugin_scope(
                    body.enabled_plugins,
                    ResearchContextMetadata.model_validate(research_context),
                )
        # 显式 null 与空列表同义：本会话不启用任何插件（默认全关）
        fields["enabled_plugins"] = body.enabled_plugins
    if "enabled_mcp" in body.model_fields_set:
        if body.enabled_mcp is not None:
            await _validate_mcp_ids(request, user["sub"], body.enabled_mcp)
        # 显式 null 与空列表同义：本会话不附加任何 MCP
        fields["enabled_mcp"] = body.enabled_mcp
    return await repos.session.update(doc["_id"], fields)


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request,
                         user=Depends(get_current_user),
                         repos=Depends(get_repos)) -> dict:
    """删除会话：session doc + 全部事件副本 + 会话目录整树 + 会话文件记录。

    会话目录（sessions/{sid}）对无工作区会话而言装着工作区（workspace/ 下的
    files/output/tmp）与事件日志（events.jsonl），整目录移除即"会话删了产物一并
    删"；workspace 里上传文件的记录（session_id 归属）同步删除，不留孤儿记录。
    """
    doc = await _own_session(sid, user, repos)
    async def remove_session_data() -> None:
        """删除当前会话的事件、文件记录、目录和主记录。"""
        for ev in await repos.event.list(filters={"session_id": sid}):
            await repos.event.delete(ev["_id"])
        for file_doc in await repos.file.list(filters={"user_id": user["sub"]}):
            if str(file_doc.get("session_id") or "") == sid:
                await repos.file.delete(file_doc["_id"])
        jsonl_dir = (workspace.user_sessions_root(
            request.app.state.settings.data_root, str(doc["user_id"])) / sid)
        shutil.rmtree(jsonl_dir, ignore_errors=True)
        await repos.session.delete(doc["_id"])

    guard = getattr(request.app.state, "workspace_job_guard", None)
    if guard is None:
        await remove_session_data()
    else:
        if doc.get("project_id"):
            project = await request.app.state.project_service.get(
                user["sub"], str(doc["project_id"])
            )
            root = request.app.state.project_service.root_for(project) if project else \
                workspace.session_root(
                    request.app.state.settings.data_root, user["sub"], sid
                )
            ownership = {
                "session_id": sid,
                "project_id": str(doc["project_id"]),
            }
        else:
            root = workspace.session_root(
                request.app.state.settings.data_root, user["sub"], sid
            )
            ownership = {"session_id": sid}
        async with guard.hold(user["sub"], root, ownership):
            if await guard.has_active(
                user_id=user["sub"], workspace_root=root, ownership=ownership
            ):
                raise HTTPException(409, "会话仍有后台沙箱任务，请先取消任务")
            await remove_session_data()
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
        request: 当前请求（取 agent_service 与 app.state 上的 settings /
            project_service，后者交给 session_runtime 解析运行装配）。
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
    if doc.get("research_context"):
        metadata = body.research_context or ResearchContextMetadata.model_validate(doc["research_context"])
        context_token = request.headers.get("X-Research-Context-Token", "")
        if not context_token:
            raise HTTPException(401, "缺少科研上下文令牌")
        try:
            await request.app.state.research_context_adapter.validate(
                token=context_token, expected=metadata)
        except ResearchContextError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        doc["research_context"] = metadata.model_dump(mode="json")
        if body.research_context is not None:
            await repos.session.update(sid, {"research_context": doc["research_context"]})
    # 助手 / 模型 / 工作根 / 归属统一由 session_runtime 解析；
    # 服务层抛领域异常，这里映射成对外 422
    try:
        runtime = await resolve_session_runtime(
            request.app.state.settings, request.app.state.project_service,
            repos, doc, user, expert_service=request.app.state.expert_service)
    except NoUsableProvider as exc:
        raise HTTPException(422, str(exc)) from exc
    assistant = runtime.assistant
    cfg = runtime.provider_cfg
    workspace_root = runtime.workspace_root
    ownership = runtime.ownership
    service = _agent_service(request)

    # 附件归一化：文件复制进本会话工作根（跨根时），agent 按相对路径可读
    attachments_meta = None
    if body.attachments:
        attachments_meta = await _normalize_attachments(
            request, user, repos, [a.file_id for a in body.attachments],
            workspace_root, ownership)

    # 先 chat（可能 429）：被拒消息不计数、不生成标题、不建 run 记录，无需回滚。
    # 前置步骤按需创建会话目录 / 清理失效绑定，均幂等、可安全重试。
    try:
        run_id = await service.chat(sid, user, assistant, cfg, body.text,
                                    workspace_root=workspace_root,
                                    requested_skills=body.skills,
                                    attachments=attachments_meta,
                                    file_ownership=ownership,
                                    enabled_plugins=doc.get("enabled_plugins"),
                                    enabled_mcp=doc.get("enabled_mcp"),
                                    research_context=doc.get("research_context"))
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


@router.get("/sessions/{sid}/events")
async def subscribe_session(sid: str, request: Request,
                            user=Depends(get_current_user),
                            repos=Depends(get_repos)) -> EventSourceResponse:
    """订阅会话的全部后续事件（会话页常驻，含任务唤醒轮与瞬态流）。

    与 POST /messages 的 per-run 流互补：run 结束后 SSE 已收尾，任务完成
    唤醒自动续跑的轮次经本端点推给打开中的会话页。事件格式与 per-run 流
    完全一致（event=事件类型、data=JSON、id=seq），前端按 seq 去重后可
    与既有处理共用。

    Args:
        sid: 会话 id。
        request: 当前请求（取 agent_service）。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        SSE 事件流响应（断连自动注销订阅，历史事件经 GET /sessions/{sid}/events 拉取）。

    Raises:
        HTTPException: 会话不存在或非本人（404）。
    """
    await _own_session(sid, user, repos)
    service = _agent_service(request)
    queue = service.subscribe_session_events(sid)

    async def sse_gen():
        """消费会话订阅队列直至客户端断连（finally 注销防队列泄漏）。"""
        try:
            while True:
                ev = await queue.get()
                yield {"event": ev.type.value, "data": ev.model_dump_json(),
                       "id": str(ev.seq)}
        finally:
            service.unsubscribe_session_events(sid, queue)

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
