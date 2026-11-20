"""Plane v1 Sessions 兼容层：会话映射、run 提交、审批桥与 durable 事件投影。

对外契约对齐老平台：v1 session = yuxi Conversation(thread_id)；v1 run id 是
兼容层生成的稳定 id（= 提交 request_id），审批 resume 后内部 AgentRun 更新而
对外 id 不变；事件按 Synlora v1 字典投影到 plane_session_events（session 级
单调 seq，token 级增量不落库）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.plane_account_link_repository import PlaneAccountLinkRepository
from yuxi.repositories.plane_session_compat_repository import (
    PlaneCompatRunRepository,
    PlaneSessionEventRepository,
)
from yuxi.services.agent_request_service import AgentRequestInput, RunOrigin, submit_agent_request
from yuxi.services.agent_run_service import create_resume_run_view, request_cancel_agent_run
from yuxi.services.conversation_service import create_thread_view
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.plane_auth_service import plane_config
from yuxi.services.plane_mcp_client import store_plane_mcp_token
from yuxi.services.plane_context_service import (
    PlaneMcpRuntime,
    ResearchContextError,
    ResearchContextMetadata,
    validate_plane_mcp_scope,
    validate_plane_metadata_scope,
    validate_research_context,
)
from yuxi.services.project_service import create_project_record
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.workspace.paths import ensure_bound_user_workdir
from yuxi.storage.redis import get_async_redis_client
from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message, Project, User
from yuxi.storage.postgres.models_business import PlaneCompatRun as PlaneCompatRunModel
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

PLANE_SESSION_SOURCE = "plane"
V1_TERMINAL_TURN_EVENTS = ("turn/end", "turn/aborted")
ARGS_PREVIEW_LIMIT = 600


def _approval_mode_to_yuxi(mode: str) -> str:
    """v1 审批模式映射为 yuxi 工具审批模式。"""
    return "always_trust" if mode == "always_allow" else "default"


def _yuxi_status_to_v1(status: str) -> str:
    """内部 run 状态映射为 v1 会话语义状态。"""
    return {
        "pending": "running",
        "running": "running",
        "cancel_requested": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "aborted",
        "interrupted": "running",
    }.get(status, status)


class PlaneSessionCompatService:
    """v1 Sessions 兼容用例（会话、run、审批、事件）。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.runs = PlaneCompatRunRepository(db)
        self.events = PlaneSessionEventRepository(db)

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    async def ensure_plane_project(self, user, plane_meta: dict[str, Any] | None) -> str | None:
        """按 Plane 工作区确定性建共享项目（对齐老平台 ensure_plane_project）。"""
        workspace = (plane_meta or {}).get("workspace") or {}
        workspace_id = str(workspace.get("id") or "")
        if not workspace_id:
            return None
        # Project id 必须是合法 UUID（workspace/paths 按 uuid.UUID 规范化）；
        # uuid5 保证同 (uid, workspace) 确定性复用
        project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"plane:{user.uid}:{workspace_id}"))
        result = await self.db.execute(select(Project).where(Project.id == project_id, Project.uid == user.uid))
        existing = result.scalar_one_or_none()
        if existing is not None and existing.status == "active":
            if existing.name != f"Plane · {workspace.get('name') or workspace.get('slug')}"[:120]:
                existing.name = f"Plane · {workspace.get('name') or workspace.get('slug')}"[:120]
                await self.db.commit()
            return project_id
        if existing is not None:  # 已删除：恢复
            existing.status = "active"
            existing.deleted_at = None
            await self.db.commit()
            return project_id
        try:
            project = await create_project_record(
                uid=user.uid,
                name=f"Plane · {workspace.get('name') or workspace.get('slug')}"[:120],
                directory_mode="managed",
                selection_status="selectable",
                db=self.db,
                project_id=project_id,
            )
            await self.db.commit()
            # selectable Project 在会话绑定时要求目录已物化（Workdir.open_existing）
            ensure_bound_user_workdir(str(user.uid), project.workdir_path)
        except IntegrityError:
            await self.db.rollback()  # 并发建：幂等返回，目录由先到者物化
        return project_id

    async def create_session(self, *, user, body: dict[str, Any]) -> dict:
        """创建 v1 会话：落 yuxi Conversation，plane 专属字段进 metadata。"""
        request_id = str(uuid.uuid4())
        if not body.get("approval_mode"):
            body = {
                **body,
                "approval_mode": getattr(plane_config, "plane_default_approval_mode", "always_allow"),
            }
        metadata = {
            "source": PLANE_SESSION_SOURCE,
            "plane": {
                key: value
                for key, value in body.items()
                if key
                in (
                    "approval_mode",
                    "enabled_plugins",
                    "enabled_mcp",
                    "research_context",
                    "plane_mcp",
                    "plane_meta",
                    "model_provider_id",
                )
            },
        }
        plane_project_id = await self.ensure_plane_project(user, body.get("plane_meta"))
        result = await create_thread_view(
            agent_slug=body.get("assistant_id") or "default-chatbot",
            request_id=request_id,
            title=(body.get("title") or "").strip() or None,
            metadata=metadata,
            project_id=plane_project_id or body.get("project_id") or None,
            db=self.db,
            current_uid=user.uid,
        )
        conversation = await ConversationRepository(self.db).get_conversation_by_thread_id(result["id"])
        return self._session_doc(conversation)

    async def get_own_session(self, thread_id: str, user) -> Conversation:
        """读取归属会话；别名身份只读，写操作由调用方拒绝。"""
        conversation = await ConversationRepository(self.db).get_conversation_by_thread_id(thread_id)
        if conversation is None or conversation.status == "deleted":
            raise HTTPException(404, "会话不存在")
        if conversation.uid == user.uid:
            return conversation
        if user.id in await self._alias_user_ids(user):
            raise HTTPException(409, "历史 Plane 会话为只读记录，请发起新会话")
        raise HTTPException(404, "会话不存在")

    async def _alias_user_ids(self, user) -> list[int]:
        """当前用户经绑定连通的别名账户 id 集合（含自身）。"""
        links = await PlaneAccountLinkRepository(self.db).list_for_synlora_user(user.id)
        alias_ids = [user.id]
        for link in links:
            plane_uid = f"plane:{link.plane_user_id}"
            result = await self.db.execute(select(User.id).where(User.uid == plane_uid, User.is_deleted == 0))
            row = result.scalar_one_or_none()
            if row is not None:
                alias_ids.append(row)
        return alias_ids

    async def list_sessions(self, user) -> list[dict]:
        """列出本人及别名身份的 v1 会话（updated_at 倒序）。"""
        repo = ConversationRepository(self.db)
        docs: list[Conversation] = []
        seen: set[str] = set()
        for user_id in await self._alias_user_ids(user):
            result = await self.db.execute(select(User.uid).where(User.id == user_id))
            uid = result.scalar_one_or_none()
            if uid is None:
                continue
            for conversation in await repo.list_conversations(uid=uid, status="active", limit=500):
                if conversation.thread_id not in seen:
                    seen.add(conversation.thread_id)
                    docs.append(conversation)
        docs.sort(key=lambda item: item.updated_at or utc_now_naive(), reverse=True)
        return [self._session_doc(item) for item in docs]

    def _session_doc(self, conversation: Conversation) -> dict:
        """会话投影为 v1 形状（`_id`/`id` 双键兼容 Plane 读取）。"""
        plane_meta = dict(((conversation.extra_metadata or {}).get("plane")) or {})
        return {
            "_id": conversation.thread_id,
            "id": conversation.thread_id,
            "user_id": conversation.uid,
            "assistant_id": conversation.agent_id,
            "title": conversation.title,
            "archived": conversation.status == "archived",
            "message_count": conversation.message_count if hasattr(conversation, "message_count") else None,
            "model_provider_id": plane_meta.get("model_provider_id"),
            "project_id": conversation.project_id,
            "approval_mode": plane_meta.get("approval_mode") or "ask_for_dangerous",
            "enabled_plugins": plane_meta.get("enabled_plugins"),
            "enabled_mcp": plane_meta.get("enabled_mcp"),
            "research_context": plane_meta.get("research_context"),
            "plane_mcp": plane_meta.get("plane_mcp"),
            "plane_meta": plane_meta.get("plane_meta"),
            "origin": PLANE_SESSION_SOURCE
            if (conversation.extra_metadata or {}).get("source") == PLANE_SESSION_SOURCE
            else "SYNLORA",
            "updated_at": (conversation.updated_at or conversation.created_at).isoformat()
            if conversation.updated_at or conversation.created_at
            else None,
        }

    async def patch_session(self, thread_id: str, user, body: dict[str, Any]) -> dict:
        """更新 v1 会话（标题/归档/审批模式）。"""
        conversation = await self.get_own_session(thread_id, user)
        repo = ConversationRepository(self.db)
        updates: dict[str, Any] = {}
        plane = dict((conversation.extra_metadata or {}).get("plane") or {})
        if body.get("title") is not None:
            updates["title"] = str(body["title"]).strip()
        if body.get("archived") is not None:
            updates["status"] = "archived" if body["archived"] else "active"
        if body.get("approval_mode") is not None:
            mode = body["approval_mode"]
            if mode not in ("always_allow", "ask_for_dangerous"):
                raise HTTPException(422, "approval_mode 非法")
            plane["approval_mode"] = mode
            updates["metadata"] = {**(conversation.extra_metadata or {}), "plane": plane}
        if updates:
            await repo.update_conversation(thread_id, **updates)
        conversation = await repo.get_conversation_by_thread_id(thread_id)
        return self._session_doc(conversation)

    async def delete_session(self, thread_id: str, user) -> dict:
        """软删会话（best-effort，与老平台一致返回 ok）。"""
        await self.get_own_session(thread_id, user)
        await ConversationRepository(self.db).delete_conversation(thread_id, soft_delete=True)
        await self.db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------
    # run 提交与审批桥
    # ------------------------------------------------------------------

    async def submit_run(
        self,
        *,
        thread_id: str,
        user,
        text: str,
        external_run_id: str = "",
        page_context: dict | None = None,
        context_token: str = "",
        mcp_token: str = "",
        research_context: ResearchContextMetadata | None = None,
        plane_mcp: PlaneMcpRuntime | None = None,
        plane_meta: dict | None = None,
    ) -> dict:
        """提交 v1 run：上下文校验、幂等映射、FIFO 提交与 user/message 事件。"""
        conversation = await self.get_own_session(thread_id, user)
        plane_meta_store = dict((conversation.extra_metadata or {}).get("plane") or {})
        stored_context = plane_meta_store.get("research_context")
        effective_context = research_context or (
            ResearchContextMetadata.model_validate(stored_context) if stored_context else None
        )
        if plane_meta is not None and effective_context is None:
            raise HTTPException(403, "Plane 元数据需要有效的科研上下文")
        if effective_context is not None:
            try:
                await validate_research_context(token=context_token, expected=effective_context)
            except ResearchContextError as exc:
                raise HTTPException(exc.status_code, exc.message)
            effective_mcp = plane_mcp or (
                PlaneMcpRuntime.model_validate(plane_meta_store["plane_mcp"])
                if plane_meta_store.get("plane_mcp")
                else None
            )
            if effective_mcp is not None:
                try:
                    validate_plane_mcp_scope(effective_mcp, effective_context)
                except ResearchContextError as exc:
                    raise HTTPException(exc.status_code, exc.message)
                if not mcp_token:
                    raise HTTPException(401, "缺少 Plane MCP 令牌")
                await store_plane_mcp_token(thread_id, mcp_token)
            if plane_meta is not None:
                try:
                    validate_plane_metadata_scope(plane_meta, effective_context)
                except ResearchContextError as exc:
                    raise HTTPException(exc.status_code, exc.message)
                plane_project_id = await self.ensure_plane_project(user, plane_meta)
                if plane_project_id and conversation.project_id != plane_project_id:
                    conversation.project_id = plane_project_id
                    await self.db.commit()
        elif plane_mcp is not None:
            raise HTTPException(403, "Plane MCP 需要有效的科研上下文")
        if plane_meta is not None:
            plane_meta_store["plane_meta"] = plane_meta

        if external_run_id:
            existing = await self.runs.get_by_external_run_id(external_run_id)
            if existing is not None and existing.thread_id == thread_id:
                return {"session_id": thread_id, "run_id": existing.v1_run_id}

        v1_run_id = str(uuid.uuid4())
        # PLANE_DEFAULT_APPROVAL_MODE=always_allow 时为覆盖语义（Plane 会显式传
        # ask_for_dangerous，跨页面审批闭环未启用前一律完全信任）
        if getattr(plane_config, "plane_default_approval_mode", "always_allow") == "always_allow":
            approval_mode = "always_trust"
        else:
            approval_mode = _approval_mode_to_yuxi(plane_meta_store.get("approval_mode") or "ask_for_dangerous")
        origin_metadata = {
            "v1_run_id": v1_run_id,
            "external_run_id": external_run_id or None,
            "plane": {
                "page_context": page_context,
                "plane_meta": plane_meta,
                "research_context": effective_context.model_dump(mode="json") if effective_context else None,
                "plane_mcp": plane_mcp.model_dump(mode="json") if plane_mcp else None,
            },
        }
        request_view = await submit_agent_request(
            request_input=AgentRequestInput(
                agent_slug=conversation.agent_id,
                thread_id=thread_id,
                request_id=v1_run_id,
                input_message=build_chat_input_message(text),
                origin=RunOrigin(
                    source=PLANE_SESSION_SOURCE,
                    channel="plane",
                    external_id=external_run_id or v1_run_id,
                    metadata=origin_metadata,
                ),
                request_metadata={"message_type": "text"},
                model_spec=plane_meta_store.get("model_provider_id") or None,
                tool_approval_mode=approval_mode,
                # 同会话并发提交按老平台口径拒绝（429），不排队：
                # 排队派发无法同步回填 current_run_id，且 Plane 依赖 202 立即可对账
                queue_policy="reject",
            ),
            current_user=user,
            db=self.db,
        )
        if request_view.get("status") == "rejected":
            raise HTTPException(429, "会话请求被拒绝")
        try:
            await self.runs.create(
                {
                    "v1_run_id": v1_run_id,
                    "thread_id": thread_id,
                    "uid": user.uid,
                    "external_run_id": external_run_id or None,
                    "current_run_id": request_view.get("run_id"),
                    "label": text[:120],
                }
            )
            await self.db.commit()
        except IntegrityError:
            # 并发重放撞 external_run_id 唯一索引：收敛为幂等返回
            await self.db.rollback()
            existing = await self.runs.get_by_external_run_id(external_run_id) if external_run_id else None
            if existing is not None and existing.thread_id == thread_id:
                return {"session_id": thread_id, "run_id": existing.v1_run_id}
            raise
        await self._append_turn_start(thread_id, v1_run_id)
        await self._append_user_message_event(thread_id, v1_run_id, text)
        await self._maybe_set_title(conversation, text)
        return {"session_id": thread_id, "run_id": v1_run_id}

    async def _maybe_set_title(self, conversation: Conversation, text: str) -> None:
        """首条消息且无标题时以消息前 24 字为题（老平台口径）。"""
        if conversation.title:
            return
        result = await self.db.execute(
            select(Conversation.thread_id).where(Conversation.thread_id == conversation.thread_id)
        )
        if result.scalar_one_or_none() is None:
            return
        await ConversationRepository(self.db).update_conversation(conversation.thread_id, title=text[:24])
        await self.db.commit()

    async def _dynamic_current_run_id(
        self, thread_id: str, uid: str, agent_slug: str, fallback: str | None
    ) -> str | None:
        """按线程最新 chat/resume run 解析当前内部 run（Synlora 原生审批/续跑会换 run）。

        映射表记录的 current_run_id 只反映兼容层可见的换 run（answer 桥）；
        Synlora 页面直接点允许产生的 resume run 不经过兼容层，必须动态解析，
        否则投影与审批桥会停在旧 run 上。
        """
        del agent_slug  # 顶层 run 以 thread+uid 唯一定位，无需 slug 过滤
        try:
            result = await self.db.execute(
                select(AgentRun.id)
                .where(
                    AgentRun.conversation_thread_id == thread_id,
                    AgentRun.uid == str(uid),
                    AgentRun.run_type.in_(("chat", "resume")),
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
            run_id = result.scalar_one_or_none()
        except Exception:
            run_id = None
        return run_id or fallback

    async def answer_run(self, v1_run_id: str, user, text: str) -> dict:
        """审批/提问应答：映射为内部 resume run，v1 run id 保持不变。"""
        mapping = await self._own_mapping(v1_run_id, user)
        current_run_id = (
            await self._dynamic_current_run_id(mapping.thread_id, user.uid, mapping.uid, mapping.current_run_id) or ""
        )
        repo = AgentRunRepository(self.db)
        run = await repo.get_run_for_user(current_run_id, str(user.uid)) if current_run_id else None
        if run is None or run.status != "interrupted":
            raise HTTPException(409, "当前没有等待回答的问题")
        if run.error_type == "human_approval_required":
            resume: dict[str, Any] = {"decisions": [{"type": "approve" if text.strip() == "允许" else "reject"}]}
        elif run.error_type == "ask_user_question_required":
            resume = {"answers": [{"answer": text}]}
        else:
            raise HTTPException(409, "当前没有等待回答的问题")
        view = await create_resume_run_view(
            agent_slug=run.agent_slug,
            thread_id=mapping.thread_id,
            meta={},
            current_uid=user.uid,
            db=self.db,
            resume=resume,
            created_by_run_id=current_run_id,
            source=PLANE_SESSION_SOURCE,
            channel="plane",
            external_id=v1_run_id,
        )
        await self.runs.set_current_run_id(mapping, view["run_id"])
        await self.db.commit()
        return {"ok": True}

    async def cancel_run(self, v1_run_id: str, user) -> dict:
        """取消 run；审批等待中的 run 以拒绝工具恢复，释放线程。"""
        mapping = await self._own_mapping(v1_run_id, user)
        current_run_id = (
            await self._dynamic_current_run_id(mapping.thread_id, user.uid, mapping.uid, mapping.current_run_id) or ""
        )
        repo = AgentRunRepository(self.db)
        run = await repo.get_run_for_user(current_run_id, str(user.uid)) if current_run_id else None
        if run is None:
            return {"ok": False}
        if run.status == "interrupted":
            if run.error_type != "human_approval_required":
                raise HTTPException(409, "当前没有等待审批的问题，无法取消")
            view = await create_resume_run_view(
                agent_slug=run.agent_slug,
                thread_id=mapping.thread_id,
                meta={},
                current_uid=user.uid,
                db=self.db,
                resume={"decisions": [{"type": "reject"}]},
                created_by_run_id=current_run_id,
                source=PLANE_SESSION_SOURCE,
                channel="plane",
                external_id=v1_run_id,
            )
            await self.runs.set_current_run_id(mapping, view["run_id"])
            await self.db.commit()
            return {"ok": True}
        if run.status in ("completed", "failed", "cancelled"):
            return {"ok": False}
        await request_cancel_agent_run(run_id=current_run_id, current_uid=user.uid, db=self.db, cascade_children=True)
        return {"ok": True}

    async def _own_mapping(self, v1_run_id: str, user) -> PlaneCompatRunModel:
        mapping = await self.runs.get_by_v1_run_id(v1_run_id)
        if mapping is None or mapping.uid != user.uid:
            raise HTTPException(404, "运行不存在")
        return mapping

    # ------------------------------------------------------------------
    # 读取：run 列表与事件
    # ------------------------------------------------------------------

    async def list_runs(self, thread_id: str, user, status: str | None = None) -> list[dict]:
        """v1 run 列表：映射行 + 内部 run 状态与事件计数。"""
        await self.get_own_session(thread_id, user)
        rows = await self.runs.list_for_thread(thread_id)
        counts = await self.events.event_counts(thread_id)
        docs: list[dict] = []
        for row in reversed(rows):
            run = None
            if row.current_run_id:
                result = await self.db.execute(select(AgentRun).where(AgentRun.id == row.current_run_id))
                run = result.scalar_one_or_none()
            v1_status = _yuxi_status_to_v1(run.status) if run is not None else "completed"
            if status and v1_status != status:
                continue
            started = run.created_at if run is not None else row.created_at
            ended = run.finished_at if run is not None else None
            docs.append(
                {
                    "_id": row.v1_run_id,
                    "session_id": thread_id,
                    "user_id": row.uid,
                    "status": v1_status,
                    "origin": "PLANE",
                    "label": row.label,
                    "external_run_id": row.external_run_id,
                    "started_at": started.isoformat() if started else None,
                    "ended_at": ended.isoformat() if ended else None,
                    "duration_ms": int((ended - started).total_seconds() * 1000)
                    if started and ended and ended >= started
                    else None,
                    "event_count": counts.get(row.v1_run_id, 0),
                }
            )
        return docs

    async def current_stream_context(self, thread_id: str) -> tuple[str, str | None]:
        """返回会话当前 (v1_run_id, 内部 run id)，供 SSE 实时增量转发。"""
        rows = await self.runs.list_for_thread(thread_id)
        if not rows:
            return "", None
        mapping = rows[-1]
        run_id = await self._dynamic_current_run_id(thread_id, mapping.uid, "", mapping.current_run_id)
        return mapping.v1_run_id, run_id

    async def events_after(self, thread_id: str, user, after_seq: int) -> list[dict]:
        """durable 事件回放（先做懒投影补齐最新事件；投影失败不阻断回放）。"""
        await self.get_own_session(thread_id, user)
        try:
            await self.project_pending_events(thread_id)
        except Exception as exc:
            logger.warning(f"plane lazy projection failed: thread={thread_id} type={type(exc).__name__}")
        rows = await self.events.events_after(thread_id, after_seq)
        return [
            {"seq": row.seq, "type": row.event_type, "payload": row.payload, "ts": row.ts.isoformat()} for row in rows
        ]

    # ------------------------------------------------------------------
    # 事件投影（yuxi run 事件 → v1 durable 事件）
    # ------------------------------------------------------------------

    async def _append_turn_start(self, thread_id: str, v1_run_id: str) -> None:
        await self._append_event(
            thread_id=thread_id,
            event_type="turn/start",
            run_id=v1_run_id,
            payload={"run_id": v1_run_id},
            source_key=f"submit:{v1_run_id}:turn",
        )

    async def _append_user_message_event(self, thread_id: str, v1_run_id: str, text: str) -> None:
        await self._append_event(
            thread_id=thread_id,
            event_type="user/message",
            run_id=v1_run_id,
            payload={"text": text, "kind": "user", "run_id": v1_run_id},
            source_key=f"submit:{v1_run_id}:user",
        )

    async def _append_event(
        self,
        *,
        thread_id: str,
        event_type: str,
        run_id: str,
        payload: dict[str, Any],
        source_key: str | None = None,
    ) -> bool:
        """按 session 级 seq 追加 durable 事件；源键重复视为已投影返回 False。

        Raises:
            RuntimeError: 冲突重试耗尽（调用方应中止本轮投影，保持游标不推进）。
        """
        for _ in range(5):
            if source_key is not None and await self.events.source_exists(thread_id, source_key):
                return False
            seq = (await self.events.max_seq(thread_id)) + 1
            try:
                await self.events.append(
                    thread_id=thread_id,
                    seq=seq,
                    event_type=event_type,
                    run_id=run_id,
                    source_key=source_key,
                    payload=payload,
                )
                await self.db.commit()
                return True
            except IntegrityError:
                await self.db.rollback()
        raise RuntimeError(f"plane event append conflict exhausted: {thread_id} {event_type}")

    async def project_pending_events(self, thread_id: str) -> None:
        """把会话当前内部 run 的新事件投影为 durable v1 事件。

        每处理完一个信标即推进 Redis 游标；源键幂等保证游标未推进的整批
        重放不产生重复事件。中途失败抛出并保持游标，等待下轮重试。
        """
        rows = await self.runs.list_for_thread(thread_id)
        if not rows:
            return
        mapping = rows[-1]
        current_run_id = await self._dynamic_current_run_id(thread_id, mapping.uid, "", mapping.current_run_id)
        if current_run_id and current_run_id != mapping.current_run_id:
            # 自愈：Synlora 原生 resume 换了 run，同步映射，投影/审批桥随最新 run 走
            await self.runs.set_current_run_id(mapping, current_run_id)
            await self.db.commit()
        if not current_run_id:
            return
        redis = await get_async_redis_client()
        cursor_key = f"plane:proj:{current_run_id}"
        raw_cursor = await redis.get(cursor_key)
        cursor = raw_cursor.decode() if isinstance(raw_cursor, bytes) else str(raw_cursor or "0-0")
        await self._project_live_tool_results(thread_id, mapping.v1_run_id, current_run_id, redis)
        envelopes = await list_run_stream_events(current_run_id, after_seq=cursor, limit=500)
        for row in envelopes:
            event = row.get("event_type")
            inner = (row.get("payload") or {}).get("payload") or {}
            source = f"{current_run_id}:{row['seq']}"
            if event == "messages":
                await self._project_tool_calls(thread_id, mapping.v1_run_id, inner, source)
            elif event == "interrupt":
                await self._project_interrupt(thread_id, mapping.v1_run_id, inner, source)
            elif event == "error":
                chunk = inner.get("chunk") or {}
                await self._append_event(
                    thread_id=thread_id,
                    event_type="error",
                    run_id=mapping.v1_run_id,
                    payload={
                        "message": chunk.get("error_message") or "运行失败",
                        "code": chunk.get("error_type"),
                        "run_id": mapping.v1_run_id,
                    },
                    source_key=source,
                )
            elif event == "end":
                # 终态按 run 只投影一次：worker 终态与 SSE 兜底可能各发一条 end 信标
                await self._project_end(
                    thread_id, mapping, str(inner.get("status") or ""), f"{current_run_id}:terminal"
                )
            await redis.set(cursor_key, row["seq"], ex=7 * 24 * 3600)

    async def _project_tool_calls(self, thread_id: str, v1_run_id: str, payload: dict, source: str) -> None:
        """把 run 流中的 tool_call 终态块投影为 tool/call（Plane 按开始/结束事件算耗时）。"""
        chunks = payload.get("items") or ([payload["chunk"]] if payload.get("chunk") else [])
        for chunk in chunks:
            if isinstance(chunk, str):
                try:
                    chunk = json.loads(chunk)
                except ValueError:
                    continue
            if not isinstance(chunk, dict):
                continue
            stream_event = chunk.get("stream_event")
            if not isinstance(stream_event, dict) or stream_event.get("type") != "tool_call":
                continue
            tool_call_id = str(stream_event.get("tool_call_id") or "")
            if not tool_call_id:
                continue
            await self._append_event(
                thread_id=thread_id,
                event_type="tool/call",
                run_id=v1_run_id,
                payload={
                    "tool_call": {
                        "name": str(stream_event.get("name") or ""),
                        "id": tool_call_id,
                        "arguments": stream_event.get("args") or {},
                    },
                    "run_id": v1_run_id,
                },
                source_key=f"{source}:call:{tool_call_id}",
            )

    async def _project_interrupt(self, thread_id: str, v1_run_id: str, payload: dict, source: str) -> None:
        chunk = payload.get("chunk") or {}
        approval = chunk.get("approval") or {}
        requests = approval.get("action_requests") or []
        if requests:
            first = requests[0] or {}
            args = first.get("args") or first.get("arguments") or {}
            try:
                args_preview = json.dumps(args, ensure_ascii=False)[:ARGS_PREVIEW_LIMIT]
            except (TypeError, ValueError):
                args_preview = ""
            # Plane 前端按 tool_call_id 渲染审批按钮，缺失即不显示；
            # yuxi 中断负载的 action_requests 不带 id，用确定性合成 id（源键保证重投影稳定）
            tool_call_id = str(first.get("id") or first.get("tool_call_id") or f"{v1_run_id}:{source}")
            await self._append_event(
                thread_id=thread_id,
                event_type="ask/user",
                run_id=v1_run_id,
                payload={
                    "kind": "approval",
                    "tool": first.get("name") or first.get("tool"),
                    "tool_call_id": tool_call_id,
                    "query": f"请求执行工具 {first.get('name') or first.get('tool') or ''}，是否允许？",
                    "args_preview": args_preview,
                    "run_id": v1_run_id,
                },
                source_key=source,
            )
        elif chunk.get("questions"):
            await self._append_event(
                thread_id=thread_id,
                event_type="ask/user",
                run_id=v1_run_id,
                payload={"kind": "question", "questions": chunk["questions"], "run_id": v1_run_id},
                source_key=source,
            )

    async def _project_end(self, thread_id: str, mapping: PlaneCompatRunModel, end_status: str, source: str) -> None:
        """终态投影：interrupted 补当轮工具结果不收尾；完成补最终回复后收尾。"""
        v1_run_id = mapping.v1_run_id
        if await self.events.source_exists(thread_id, f"{source}:assistant") or await self.events.source_exists(
            thread_id, f"{source}:end"
        ):
            return  # 该 run 的终态已投影（重复 end 信标）
        if end_status == "interrupted":
            # 审批等待前已执行的工具结果仍需可见（老平台按发生时落盘）
            await self._project_tool_results(thread_id, v1_run_id, mapping.current_run_id, source)
            return
        if end_status == "completed":
            current_run_id = mapping.current_run_id
            result = await self.db.execute(select(AgentRun).where(AgentRun.id == current_run_id))
            run = result.scalar_one_or_none()
            output = None
            if run is not None and run.output_message_id:
                output_result = await self.db.execute(select(Message).where(Message.id == run.output_message_id))
                output = output_result.scalar_one_or_none()
            await self._append_event(
                thread_id=thread_id,
                event_type="assistant/message",
                run_id=v1_run_id,
                payload={"content": output.content if output is not None else "", "run_id": v1_run_id},
                source_key=f"{source}:assistant",
            )
            await self._project_tool_results(thread_id, v1_run_id, current_run_id, source)
            await self._append_event(
                thread_id=thread_id,
                event_type="turn/end",
                run_id=v1_run_id,
                payload={"run_id": v1_run_id},
                source_key=f"{source}:end",
            )
            return
        reason = "cancelled" if end_status == "cancelled" else "failed"
        await self._append_event(
            thread_id=thread_id,
            event_type="turn/aborted",
            run_id=v1_run_id,
            payload={"reason": reason, "run_id": v1_run_id},
            source_key=f"{source}:aborted",
        )

    async def _project_live_tool_results(self, thread_id: str, v1_run_id: str, current_run_id: str, redis) -> None:
        """按消息表轮询投影新完成的工具结果（工具完成即刻可见，不等终态）。"""
        marker_key = f"plane:proj:tools:{current_run_id}"
        raw_marker = await redis.get(marker_key)
        marker = int(raw_marker) if raw_marker else 0
        result = await self.db.execute(
            select(Message)
            .where(Message.run_id == current_run_id, Message.role == "tool", Message.id > marker)
            .order_by(Message.id.asc())
            .limit(50)
        )
        messages = list(result.scalars().all())
        if not messages:
            return
        for message in messages:
            metadata = message.extra_metadata or {}
            await self._append_event(
                thread_id=thread_id,
                event_type="tool/result",
                run_id=v1_run_id,
                payload={
                    "name": metadata.get("tool_name") or "tool",
                    "tool_call_id": metadata.get("tool_call_id"),
                    "ok": True,
                    "content": message.content,
                    "run_id": v1_run_id,
                },
                source_key=f"{current_run_id}:terminal:tool:{message.id}",
            )
            marker = max(marker, message.id)
        await redis.set(marker_key, marker, ex=7 * 24 * 3600)

    async def _project_tool_results(
        self, thread_id: str, v1_run_id: str, current_run_id: str | None, source: str
    ) -> None:
        """把该内部 run 的工具消息投影为 tool/result（按消息 id 幂等）。"""
        if not current_run_id:
            return
        tools = await self.db.execute(
            select(Message).where(Message.run_id == current_run_id, Message.role == "tool").limit(200)
        )
        for message in tools.scalars():
            metadata = message.extra_metadata or {}
            await self._append_event(
                thread_id=thread_id,
                event_type="tool/result",
                run_id=v1_run_id,
                payload={
                    "name": metadata.get("tool_name") or "tool",
                    "tool_call_id": metadata.get("tool_call_id"),
                    "ok": True,
                    "content": message.content,
                    "run_id": v1_run_id,
                },
                source_key=f"{source}:tool:{message.id}",
            )
