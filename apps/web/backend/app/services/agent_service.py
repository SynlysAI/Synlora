"""AgentService：harness 组装、事件持久化与 SSE 内存队列。

事件事实源：DB 事件为查询/回放事实源（GET events、seed 历史均读 DB），
JSONL 为审计副本（当前无人消费，漂移可接受）。

瞬态事件不落盘（流式性能）：llm/delta、reasoning/delta 每 token 一条，
逐条落 JSONL/DB 等于每 token 一次 fsync，拖慢流式；故两类 delta 仅推
SSE，不写 JSONL/DB（seq 仍由 EventLog 分配，持久层出现 seq 洞，回放由
assistant/message、assistant/reasoning 等结构事件承载全文）。

遵守 Plan 2 harness 接入契约：
- sink 不得抛异常（宿主 sink 全包 try/except，持久化失败不杀对话）；
- SSE 推送只 put 内存队列，绝不等待消费者（慢客户端不拖 LLM 流）；
- 消费 run() 用 contextlib.aclosing 包裹；
- SSE 断连不 cancel：run 由 _drive 独立 asyncio task 后台执行完落盘，
  重连经 GET /sessions/{id}/events?after_seq=N 补齐；cancel 仅用户显式触发。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from synlys_harness import (
    AgentConfig,
    CodeExecutor,
    EventLog,
    EventType,
    ExtensionHooks,
    ModelProviderConfig,
    OpenAICompatibleBackend,
    RunSession,
    SessionEvent,
    ToolResult,
    build_system_prompt,
    resolve_executor,
)

from app.services.skill_service import SkillService
from app.services.tool_registry import PIPELINE as _PIPELINE, REGISTRY as _REGISTRY

MAX_RUNS_PER_USER = 2  # 每用户并发运行上限（超出 API 层转 429）

# 技能与平台交互工具：无条件追加到助手白名单（平台能力，不依赖助手自行声明；
# ask_user=问答回路、file.send=产物交付是宿主注入的交互通道，任何助手都可用）
SKILL_TOOLS = ("skill.list", "skill.read", "ask_user", "file.send")

# 瞬态事件：每 token 一条，仅 SSE 推送、不落 JSONL/DB（见模块头注释）
TRANSIENT = {EventType.LLM_DELTA, EventType.REASONING_DELTA}

_LOGGER = logging.getLogger(__name__)

# 工具注册表收口在 app.services.tool_registry（与 assistants_api 共用同一实例）


class TooManyRuns(Exception):
    """并发运行数超限（会话级互斥：同会话已有进行中消息；或用户级上限）。"""


class ActiveRun:
    """一次进行中的对话运行（queue 供 SSE 消费，done 标记收尾完成）。"""

    def __init__(self) -> None:
        """初始化队列与事件。"""
        self.queue: asyncio.Queue = asyncio.Queue()
        self.done = asyncio.Event()
        self.session: RunSession | None = None
        # ask_user 等待中的回答 future（None = 当前无待回答问题）
        self.ask_future: asyncio.Future | None = None


class AgentService:
    """对话运行编排（单例，挂 app.state.agent_service）。"""

    def __init__(self, store: Any, settings: Any, event_repo: Any,
                 skill_service: SkillService, file_repo: Any = None) -> None:
        """保存依赖。

        Args:
            store: DocumentStore（runs 直查直写）。
            settings: 应用配置（数据根/白名单）。
            event_repo: 会话事件 repo（DB 副本写入与回放）。
            skill_service: 技能服务（磁盘扫描目录 + 按名取正文）。
            file_repo: 文件 repo（file.send 登记产物供下载；None 时该工具报不支持）。
        """
        self._store = store
        self._settings = settings
        self._event_repo = event_repo
        self._skill_service = skill_service
        self._file_repo = file_repo
        self._runs: dict[str, ActiveRun] = {}
        # 会话级互斥：session_id → 活跃 run_id 集合（同会话同时只允许一个 run）
        self._active_by_session: dict[str, set[str]] = {}
        # 后台驱动 task 的强引用（事件循环仅持弱引用，防 task 被 GC 中断）
        self._bg: set[asyncio.Task] = set()
        # python.run 沙箱执行器（首次使用时解析一次：local 直返；docker 探测
        # daemon+镜像，失败按 strict 拒绝或弱回退 local-weak 并告警）
        self._executor: CodeExecutor | None = None

    async def _code_executor(self) -> CodeExecutor:
        """解析（一次）沙箱执行器并缓存。

        Returns:
            部署级 CodeExecutor；docker 模式探测失败时为弱回退本机或
            fail-closed 拒绝执行器（取决于 sandbox_strict）。
        """
        if self._executor is None:
            executor, note = await asyncio.to_thread(
                resolve_executor, self._settings.sandbox_mode,
                image=self._settings.sandbox_docker_image,
                strict=self._settings.sandbox_strict,
                mem_limit=self._settings.sandbox_mem_limit,
                cpus=self._settings.sandbox_cpus,
                pids_limit=self._settings.sandbox_pids_limit,
                container_user=self._settings.sandbox_docker_user,
            )
            if executor.sandbox != "docker":
                _LOGGER.warning("python.run 沙箱: %s", note)
            else:
                _LOGGER.info("python.run 沙箱: %s", note)
            self._executor = executor
        return self._executor

    def _jsonl_path(self, session_id: str) -> Path:
        """会话事件文件路径（父目录自动创建）。

        Args:
            session_id: 会话 id。

        Returns:
            {data_root}/sessions/{session_id}/events.jsonl。
        """
        p = self._settings.data_root / "sessions" / session_id / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_active(self, run_id: str) -> ActiveRun | None:
        """取运行中的 ActiveRun（SSE 生成器消费其 queue）。

        Args:
            run_id: 运行 id。

        Returns:
            ActiveRun；run 已结束被摘除时返回 None。
        """
        return self._runs.get(run_id)

    async def chat(self, session_id: str, user: dict, assistant: dict | None,
                   provider_cfg: ModelProviderConfig, text: str,
                   workspace_root: Path,
                   requested_skills: list[str] | None = None,
                   attachments: list[dict] | None = None) -> str:
        """启动一轮对话运行，返回 run_id（事件经 ActiveRun.queue 流出）。

        装配收口：平台默认段 + 专家 persona（可选）+ 技能渐进披露（索引进
        提示词，正文只经 skill.read 工具按需取）。

        Args:
            session_id: 会话 id。
            user: 当前用户 payload（sub）。
            assistant: 助手文档（system_prompt/tool_whitelist）；None 或空 dict
                表示未选专家——无 persona、工具放开全部内置工具。
            provider_cfg: 已解密的模型服务配置。
            text: 用户消息文本。
            workspace_root: 工作区根目录（项目目录，由调用方经
                ProjectService.resolve_active_project + root_for 解析后必传）。
                必填而非回落用户目录：C6 把文件也项目作用域化后，用户目录下不会
                再有 files/，静默回落等于把 run 跑在错误目录。
            requested_skills: 本会话选中的技能名列表；None 或空列表表示全部可用。
            attachments: 随消息发送的附件元数据（[{file_id, filename, path}]，
                path 为相对 workspace_root 的路径；调用方保证文件已在该项目内）。
                None/空 = 无附件。

        Returns:
            run_id。

        Raises:
            TooManyRuns: 该会话已有进行中的消息，或该用户运行中的对话已达上限。
        """
        # 会话级互斥：检查与占位在同一同步段完成（中间无 await，并发请求
        # 串行执行到此即被拒）。同会话两个并发 run 会各自 seed 同一份历史
        # 快照、从相同 seq 起号，DB _id=f"{sid}:{seq}" 碰撞写入被 db_sink
        # 静默吞掉 → 事件拼接错乱/丢失，必须前置拒绝。
        session_runs = self._active_by_session.setdefault(session_id, set())
        if session_runs:
            raise TooManyRuns("该会话已有进行中的消息")
        run_id = uuid.uuid4().hex[:12]
        session_runs.add(run_id)
        try:
            running = await self._store.list("runs", filters={
                "user_id": user["sub"], "status": "running"})
            if len(running) >= MAX_RUNS_PER_USER:
                raise TooManyRuns(f"该用户已有 {MAX_RUNS_PER_USER} 个运行中的对话")
            active = ActiveRun()
            self._runs[run_id] = active

            async def jsonl_sink(event: SessionEvent) -> None:
                """事件追加 JSONL（审计副本；瞬态不落盘；契约：不得抛异常）。"""
                if event.type in TRANSIENT:
                    return
                try:
                    with self._jsonl_path(session_id).open("a", encoding="utf-8") as f:
                        f.write(event.model_dump_json() + "\n")
                except OSError:
                    pass

            async def db_sink(event: SessionEvent) -> None:
                """事件写 DB 副本（瞬态不写）+ SSE 队列只 put 不过滤（契约：不得抛异常、不等待消费者）。"""
                if event.type not in TRANSIENT:
                    try:
                        await self._event_repo.append(session_id, event)
                    except Exception:
                        pass
                try:
                    active.queue.put_nowait(event)
                except Exception:
                    pass

            log = EventLog(sinks=[jsonl_sink, db_sink])

            # 扩展钩子挂载（可观测性：turn 生命周期 + 工具事件审计日志）
            async def _on_session_start(_session: RunSession) -> None:
                _LOGGER.info("turn 开始 run_id=%s session=%s", run_id, session_id)

            async def _on_session_end(_session: RunSession) -> None:
                _LOGGER.info("turn 结束 run_id=%s session=%s", run_id, session_id)

            async def _on_tool_event(ev: SessionEvent) -> None:
                _LOGGER.info("工具事件 %s name=%s run_id=%s",
                             ev.type.value, (ev.payload or {}).get("name", ""), run_id)

            hooks = ExtensionHooks(
                on_session_start=_on_session_start,
                on_session_end=_on_session_end,
                on_tool_event=_on_tool_event,
            )
            # 会话级 seq 续号依赖 seed 恢复：用 DB 历史事件预填充本轮日志，
            # 使 seq 跨轮续号（DB _id=f"{sid}:{seq}" 不碰撞，append 取
            # last.seq+1 不撞洞）、derive_messages 能投影出前几轮消息
            # （LLM 对话记忆）；历史因瞬态过滤带 seq 洞，seed 容忍严格递增；
            # 首轮会话历史为空跳过。
            history = await self._event_repo.list_events(session_id)
            if history:
                log.seed(history)
            backend = OpenAICompatibleBackend(provider_cfg)
            # 工作区根由调用方（sessions_api）按会话所属项目解析后传入；服务自身不拼路径
            workspace_root.mkdir(parents=True, exist_ok=True)
            # 技能：全局目录（磁盘扫描）+ 本会话选中项（None/空 = 全部可用）；
            # 索引进提示词，正文只入 context_extra（渐进披露，由 skill.read 按需取）
            all_skills = self._skill_service.list_skills()
            if requested_skills:
                active_skills = [s for s in all_skills
                                 if s["name"] in set(requested_skills)]
            else:
                active_skills = all_skills
            index = [(s["name"], s["description"]) for s in active_skills]
            bodies = {s["name"]: self._skill_service.read_body(s["name"]) or ""
                      for s in active_skills}
            persona = str((assistant or {}).get("system_prompt") or "").strip()
            whitelist = list((assistant or {}).get("tool_whitelist") or [])
            system_prompt = build_system_prompt(
                persona=persona,
                workspace=workspace_root,
                skills=index,
            )
            # 无专家（或专家没限定工具）时放开全部内置工具；技能工具无条件
            # 追加（技能是平台能力），set 去重防助手白名单已列
            tool_names = (
                list(dict.fromkeys([*whitelist, *SKILL_TOOLS]))
                if whitelist
                else list(_REGISTRY.names)
            )
            # 图片阅读双重门控：模型多模态 +（助手未限白名单或白名单显式包含）
            if provider_cfg.multimodal and (not whitelist or "file.read_image" in whitelist):
                tool_names = list(dict.fromkeys([*tool_names, "file.read_image"]))
            else:
                tool_names = [t for t in tool_names if t != "file.read_image"]

            # ask_user：发 ask/user 事件（落盘+SSE）并等待前端回答 future
            async def ask_handler(payload: dict) -> str:
                future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                active.ask_future = future
                try:
                    await log.append(EventType.ASK_USER, payload)
                    return await future
                finally:
                    active.ask_future = None

            # 管线强制审批（Permission.ASK_USER）：复用 ask_user 的 future 回路，
            # payload 加 kind=approval 供前端渲染审批卡（允许/拒绝按钮）；用户
            # 答复仍走同一 answer API（固定文案"允许"/"拒绝"，管线按文本判定）
            async def approval_handler(payload: dict) -> str:
                tool = str(payload.get("tool", ""))
                preview = json.dumps(payload.get("args", {}), ensure_ascii=False)
                if len(preview) > 600:
                    preview = preview[:600] + "…"
                return await ask_handler({
                    "kind": "approval",
                    "tool_call_id": str(payload.get("tool_call_id", "")),
                    "tool": tool,
                    "query": f"请求执行工具 {tool}，是否允许？",
                    "args_preview": preview,
                })

            # file.send：复制产物进项目 files/ 沙箱 → 登记 files 集合 → 发事件
            async def send_file_handler(payload: dict) -> ToolResult:
                return await self._deliver_file(active, log, workspace_root,
                                                user["sub"], payload)

            session = RunSession(
                config=AgentConfig(
                    system_prompt=system_prompt,
                    tool_names=tool_names,
                    max_steps=(assistant or {}).get("max_steps", 25),
                ),
                registry=_REGISTRY, pipeline=_PIPELINE, backend=backend,
                event_log=log, user_id=user["sub"], run_id=run_id,
                hooks=hooks,
                workspace_root=workspace_root,
                context_extra={
                    "http_allowed_hosts": self._settings.allowed_hosts,
                    # python.run 执行器（部署级注入，缺省工具回落本机执行）
                    "code_executor": await self._code_executor(),
                    "skills": bodies,
                    "skill_meta": {s["name"]: s["description"] for s in active_skills},
                    # WeKnora 知识检索：连接配置 + 助手绑定的知识库范围（None/空 =
                    # 未绑定，knowledge.search 工具会给出明确报错）
                    "weknora_base_url": self._settings.weknora_base_url,
                    "weknora_api_key": self._settings.weknora_api_key,
                    "knowledge_base_ids": list(
                        (assistant or {}).get("knowledge_base_ids") or []),
                    # 联网搜索（SearXNG）：地址空 = 未启用，web.search 工具报明确错误
                    "web_search_endpoint": self._settings.assistant_web_search_endpoint,
                    "web_search_api_key": self._settings.assistant_web_search_api_key,
                    # 用户交互工具的宿主回调（ask_user / file.send）与管线强制审批
                    "ask_user_handler": ask_handler,
                    "send_file_handler": send_file_handler,
                    "approval_handler": approval_handler,
                },
            )
            active.session = session
            t = asyncio.create_task(
                self._drive(run_id, session, text, user["sub"], session_id,
                            attachments=attachments))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        except Exception:
            # 初始化任何一步失败（用户超限/DB 历史空洞/组装异常）都回滚注册，
            # 防止 _runs/_active_by_session 残留失败 run（泄漏句柄 + 会话被
            # 永久卡 429 + SSE 哨兵永不投递）
            self._runs.pop(run_id, None)
            session_runs.discard(run_id)
            raise
        return run_id

    async def _drive(self, run_id: str, session: RunSession, text: str,
                     user_id: str, session_id: str,
                     attachments: list[dict] | None = None) -> None:
        """后台驱动 run 至完成并落盘终态（独立于 SSE 消费者，断连不中断）。

        Args:
            run_id: 运行 id。
            session: harness 运行会话。
            text: 用户消息文本。
            user_id: 用户 sub。
            session_id: 会话 id。
            attachments: 随消息发送的附件元数据（None/空 = 无附件）。
        """
        try:
            await self._store.insert("runs", {
                "_id": run_id, "session_id": session_id, "user_id": user_id,
                "status": "running", "started_at": time.time(),
            })
        except Exception:
            pass  # run 记录失败不阻断对话流（并发限制会暂时失效，属存储故障降级）
        final_status = "completed"
        try:
            pending_text, pending_attachments = text, attachments
            while True:
                stream = session.run(pending_text, attachments=pending_attachments)
                async with contextlib.aclosing(stream):
                    async for _event in stream:
                        pass  # 事件已由 sinks 持久化并入队
                # 插话兜底：turn 正常结束但队列残留插话（模型收尾窗口入队、
                # 无下一个 step 可消费）→ 转为下一轮用户输入自动续跑，防静默
                # 丢失；取消/LLM 失败路径 take_queued_turn 内部已丢弃返回 None
                pending_text = session.take_queued_turn()
                pending_attachments = None
                if pending_text is None:
                    break
        except Exception:
            final_status = "failed"
        # harness 取消旗标（未暴露公共 API，接入契约确认可读）：用户显式 cancel 优先于异常归类
        if session._cancel.is_set():  # noqa: SLF001
            final_status = "aborted"
        try:
            await self._store.update("runs", run_id, {
                "status": final_status, "ended_at": time.time()})
        except Exception:
            # 终态落盘失败仅记录（run 记录停留 running，属存储故障降级），
            # 不向上抛——抛出会杀掉本 task 且触发未处理异常告警
            _LOGGER.warning("run 终态落盘失败: run_id=%s status=%s",
                            run_id, final_status, exc_info=True)
        finally:
            active = self._runs.pop(run_id, None)
            if active:
                active.queue.put_nowait(None)  # SSE 结束哨兵（任何路径都必须放，防 SSE 挂死）
                active.done.set()
            session_runs = self._active_by_session.get(session_id)
            if session_runs is not None:
                session_runs.discard(run_id)  # 释放会话占位（空集合保留，量级=会话数）

    async def cancel(self, run_id: str) -> bool:
        """取消运行（仅用户显式停止；SSE 断连不走此路径）。

        Args:
            run_id: 运行 id。

        Returns:
            是否找到仍在内存注册表中的运行并发出取消。
        """
        active = self._runs.get(run_id)
        if active and active.session is not None:
            active.session.cancel()
            return True
        return False

    async def steer(self, run_id: str, text: str) -> bool:
        """运行中插话（harness steering：下一个 step 边界注入为 user 消息）。

        插话经 session.steer 入队后由 loop 落成带 steering 标记的
        user/message 事件，随事件流持久化——所以队列本身无需单独持久化。

        Args:
            run_id: 运行 id。
            text: 插话文本（非空由 API 层校验）。

        Returns:
            是否找到仍在内存注册表中的运行并完成入队。
        """
        active = self._runs.get(run_id)
        if active and active.session is not None:
            active.session.steer(text)
            return True
        return False

    async def answer(self, run_id: str, text: str) -> bool:
        """回答运行中 ask_user 的待答问题（resolve future，工具随即返回）。

        Args:
            run_id: 运行 id。
            text: 用户回答文本。

        Returns:
            是否找到等待中的问题并完成投递。
        """
        active = self._runs.get(run_id)
        if active and active.ask_future is not None and not active.ask_future.done():
            active.ask_future.set_result(text)
            return True
        return False

    async def _deliver_file(self, active: "ActiveRun", log: EventLog,
                            workspace_root: Path, user_id: str,
                            payload: dict) -> ToolResult:
        """file.send 宿主侧：产物复制进项目 files/ → 登记 files 集合 → 发 file/send 事件。

        复制而非登记原路径：下载端点的 stored_path 安全校验要求文件落在
        files/ 沙箱内（与上传同一约束），登记任意路径会被 404 拒绝。

        Args:
            active: 所属运行（未用，签名对称保留）。
            log: 本轮事件日志（事件落盘 + SSE）。
            workspace_root: 工作区根。
            user_id: 用户 sub。
            payload: 工具入参（path/note/tool_call_id）。

        Returns:
            工具结果（成功 content 为给 LLM 的确认文本）。
        """
        del active  # 未用
        rel = str(payload.get("path", "")).strip()
        try:
            src = (workspace_root / rel).resolve()
            if workspace_root.resolve() not in src.parents:
                return ToolResult(ok=False, content="路径越界", error="path_escape")
        except OSError:
            return ToolResult(ok=False, content=f"路径非法: {rel}", error="path_escape")
        if not src.is_file():
            return ToolResult(ok=False, content=f"文件不存在: {rel}", error="not_found")
        if self._file_repo is None:
            return ToolResult(ok=False, content="文件交付未配置（缺 file repo）", error="no_handler")
        files_dir = workspace_root / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / src.name
        stem, suffix = target.stem, target.suffix
        n = 1
        while target.exists():
            target = files_dir / f"{stem}-{n}{suffix}"
            n += 1
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            return ToolResult(ok=False, content=f"复制失败: {exc}", error="io_error")
        size = target.stat().st_size
        doc = await self._file_repo.create({
            "user_id": user_id,
            "filename": target.name,
            "stored_path": target.relative_to(workspace_root).as_posix(),
            "size": size,
            "mime": "application/octet-stream",
        })
        await log.append(EventType.FILE_SEND, {
            "tool_call_id": payload.get("tool_call_id", ""),
            "file_id": doc["_id"],
            "filename": target.name,
            "size": size,
            "path": rel,
            "note": payload.get("note", ""),
        })
        return ToolResult(ok=True, content=f"已把文件 {target.name}（{size // 1024}KB）发送给用户")

    async def events_after(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        """取 seq 大于 after_seq 的会话事件（SSE 断连重连增量补齐用）。

        Args:
            session_id: 会话 id。
            after_seq: 客户端已收到的最大 seq。

        Returns:
            seq 升序的事件列表。
        """
        events = await self._event_repo.list_events(session_id)
        return [e for e in events if e.seq > after_seq]
