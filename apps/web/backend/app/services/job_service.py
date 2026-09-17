"""后台任务服务：提交、查询、取消、状态流转与结果持久化。

职责边界：本服务只认 JobConnector 协议（怎么跟外部系统说话）与 JobRepo
（任务文档在哪），不认识任何具体子平台。任务终态只写入 Job 文档，供右侧
运行信息和 ``job.status`` / ``job.list`` 主动查询，不自动发起新的 Agent run。

状态流转与取消（Task 6）已实现：`refresh` 把外部状态原文映射为统一状态并校验
合法流转（查询失败/未映射一律保持原状态、只累计 poll_failures），`cancel` 调
连接器请求取消后本地收敛为 cancelled（终态任务拒绝取消）。

并发安全（`refresh` 与 `cancel` 共用同一把任务级锁）：poller 的 tick、模型调
`job.status`、用户/模型调 `job.cancel` 可能拿到同一份非终态快照，靠任务级锁
串行化并在锁内重读文档——后到者看到已落库的终态即早返回，也不会对同一任务
并发调外部 poll。`cancel` 同样取锁：连接器取消耗时较长
（网络 await），不取锁时轮询在窗口内落地的状态会整字段覆盖取消结果（任务从
cancelled 回退为 running）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import Any

from synlys_harness import (
    ACTIVE_STATUSES,
    JobStatus,
    ToolResult,
    can_transition,
    is_terminal,
)

from app.plugins.contracts import (
    JobConnectorRegistry,
    JobPollFailed,
    JobSubmitFailed,
)
from app.services.job_access import (
    JobSubmissionScope,
    WorkspaceJobGuard,
    prepare_sandbox_job,
)

_LOGGER = logging.getLogger(__name__)

# 摘要文本里注入给 LLM 的结果上限（超出截断，完整结果可由外部系统/后续工具取）
RESULT_PREVIEW_CHARS = 4000

def _new_job_id() -> str:
    """生成任务 id。"""
    return "job-" + uuid.uuid4().hex[:12]


def _clip(text: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    """截断长文本并标注。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字）"


def _signing_identity(user: dict) -> dict:
    """从登录 payload 摘出代签所需的最小身份（落库供轮询路径重签凭证）。

    Args:
        user: 当前用户 payload（sub/username/role/ai4ms_user_id）。

    Returns:
        可直接喂给 `Ai4msIdentityService.token_for` 的身份字典。
    """
    return {
        "sub": str(user.get("sub") or ""),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or ""),
        "ai4ms_user_id": str(user.get("ai4ms_user_id") or ""),
    }


class JobService:
    """后台任务编排（单例，挂 app.state.job_service）。"""

    def __init__(self, repo: Any, connectors: JobConnectorRegistry,
                 plugin_config_store: Any = None,
                 ai4ms_identity: Any = None,
                 settings: Any = None,
                 sandbox_runner: Any = None,
                 workspace_guard: WorkspaceJobGuard | None = None) -> None:
        """保存依赖。

        Args:
            repo: JobRepo（任务文档读写）。
            connectors: 连接器注册表（kind → 连接器）。
            plugin_config_store: 插件配置存储（轮询时按 job 的 user_id 重新解析
                配置；None 时轮询只用空配置）。
            ai4ms_identity: AI⁴MS 代签服务。轮询路径用它按 job 文档里落库的
                身份**现签**用户凭证（提交时那份只有 1 小时有效期，存下来会过期）；
                None 时代签不出凭证，需鉴权的插件只能依赖配置里的服务 token。
        """
        self._repo = repo
        self._connectors = connectors
        self._plugin_config_store = plugin_config_store
        self._ai4ms_identity = ai4ms_identity
        self._settings = settings
        self._sandbox_runner = sandbox_runner
        self._workspace_guard = workspace_guard
        # 每任务的串行锁（防并发 refresh 重复推进状态）；按任务数增长，
        # 进程内小对象，任务总量可控故不回收（回收会引出"旧锁 vs 新锁"的并发窗口）
        self._job_locks: dict[str, asyncio.Lock] = {}

    def set_sandbox_runner(self, runner: Any) -> None:
        """注入平台沙箱后台运行器。"""
        self._sandbox_runner = runner

    # ---------- 查询 ----------

    async def get(self, job_id: str) -> dict | None:
        """按 id 取任务文档。"""
        return await self._repo.get(job_id)

    async def list_for_session(self, session_id: str) -> list[dict]:
        """列出某会话的全部任务（创建时间升序）。"""
        docs = await self._repo.list(filters={"session_id": session_id})
        return sorted(docs, key=lambda d: float(d.get("created_at") or 0))

    async def list_for_user(self, user_id: str) -> list[dict]:
        """列出某用户的全部任务（创建时间升序）。

        Args:
            user_id: 用户 sub。

        Returns:
            任务文档列表。
        """
        docs = await self._repo.list(filters={"user_id": user_id})
        return sorted(docs, key=lambda d: float(d.get("created_at") or 0))

    async def list_active(self) -> list[dict]:
        """列出需要外部轮询的未完成任务。"""
        active_values = {s.value for s in ACTIVE_STATUSES}
        docs = await self._repo.list()
        return [
            doc for doc in docs
            if doc.get("backend") == "external"
            and doc.get("status") in active_values
        ]

    # ---------- 配置解析（提交/轮询共用） ----------

    async def _ctx_for(self, user_id: str, plugin_id: str,
                       ctx_extra: dict | None = None,
                       workspace_root: str = "",
                       identity: dict | None = None) -> dict:
        """构造连接器调用上下文。

        Args:
            user_id: 任务归属用户（轮询路径用它重新解析配置）。
            plugin_id: 归属插件 id。
            ctx_extra: 提交路径可直接给出的运行上下文（含 plugins/ai4ms_token/
                workspace_root）；为 None 时（轮询路径）从插件配置存储与代签服务重建。
            workspace_root: 轮询路径的工作区根（提交路径从 ctx_extra 取，忽略本参数）。
            identity: 轮询路径的用户身份（提交时落库到 job 文档的 user_identity），
                用于现签用户凭证；提交路径忽略本参数。

        Returns:
            {"config": {插件配置}, "ai4ms_token": "<token 或空串>",
             "workspace_root": "<工作区根或空串>"}。
        """
        if ctx_extra is not None:
            plugins = ctx_extra.get("plugins") or {}
            return {
                "config": dict(plugins.get(plugin_id) or {}),
                "ai4ms_token": str(ctx_extra.get("ai4ms_token") or ""),
                "workspace_root": str(ctx_extra.get("workspace_root") or ""),
            }
        config: dict = {}
        if self._plugin_config_store is not None:
            try:
                config = await self._plugin_config_store.resolved_for_user(
                    user_id, plugin_id)
            except Exception:  # noqa: BLE001 解密失败等：按无配置处理，任务照常轮询
                _LOGGER.warning("轮询时解析插件配置失败 plugin=%s user=%s",
                                plugin_id, user_id, exc_info=True)
        return {"config": config,
                "ai4ms_token": await self._resign_token(identity or {}),
                "workspace_root": workspace_root}

    async def _resign_token(self, identity: dict) -> str:
        """按落库的身份现签一个短效用户凭证（签不出时返回空串）。

        每次轮询都重签、而非复用提交时那份：代签凭证只有 1 小时有效期，
        落库复用会让跑满 1 小时的任务集体 401（这正是"任务永远停在 pending"
        的成因）。签不出（sqlite 本地用户/匿名/未注入代签服务）时返回空串，
        插件回落自身配置里的服务 token。

        Args:
            identity: 提交时摘下的代签身份（sub/username/role/ai4ms_user_id）。

        Returns:
            代签 token；无身份或代签失败时空串。
        """
        if self._ai4ms_identity is None or not identity.get("username"):
            return ""
        try:
            return str(await self._ai4ms_identity.token_for(identity) or "")
        except Exception:  # noqa: BLE001 代签失败降级为无凭证，不阻断轮询
            _LOGGER.warning("轮询时代签用户凭证失败 user=%s", identity.get("sub"),
                            exc_info=True)
            return ""

    # ---------- 工具入口 ----------

    async def handle(self, payload: dict, *, user: dict, session_id: str,
                     ctx_extra: dict) -> ToolResult:
        """job_handler 实现：按 action 分发（宿主装配时注入 ctx.extra）。

        Args:
            payload: 工具传来的负载（action/kind/params/label/job_id...）。
            user: 当前用户 payload。
            session_id: 当前会话 id。
            ctx_extra: 本轮运行的工具上下文 extra（取插件配置与代签 token）。

        Returns:
            工具结果。
        """
        action = str(payload.get("action", ""))
        if action == "submit":
            return await self.submit(
                session_id=session_id, user_id=str(user["sub"]),
                kind=str(payload.get("kind", "")),
                params=payload.get("params") or {},
                label=str(payload.get("label", "")), ctx_extra=ctx_extra,
                identity=_signing_identity(user))
        if action == "status":
            return await self.describe(str(payload.get("job_id", "")),
                                       user_id=str(user["sub"]), refresh=True)
        if action == "list":
            return await self.render_list(session_id, user_id=str(user["sub"]))
        if action == "cancel":
            return await self.cancel(str(payload.get("job_id", "")),
                                     user_id=str(user["sub"]))
        return ToolResult(ok=False, content=f"未知的任务操作: {action}",
                          error="invalid_arguments")

    async def submit(self, *, session_id: str, user_id: str, kind: str,
                     params: dict, label: str, ctx_extra: dict,
                     identity: dict | None = None) -> ToolResult:
        """提交任务：调连接器 → 落库 pending → 立即返回（不等待任务完成）。

        Args:
            session_id: 所属会话。
            user_id: 所属用户。
            kind: 任务类型。
            params: 任务参数。
            label: 任务简述（给用户看）。
            ctx_extra: 本轮运行上下文（取插件配置与代签 token）。
            identity: 代签身份（落库供轮询路径现签凭证，见 `_resign_token`）。

        Returns:
            ok=True 且 data["job_id"]；失败时错误码为 unknown_job_kind /
            submit_failed。
        """
        scope = (ctx_extra or {}).get("job_submission_scope")
        if not isinstance(scope, JobSubmissionScope):
            return ToolResult(
                ok=False,
                content="当前运行缺少可信的后台任务授权范围",
                error="job_scope_missing",
            )
        if kind.startswith("sandbox."):
            return await self._submit_sandbox(
                session_id=session_id,
                user_id=user_id,
                kind=kind,
                params=params,
                label=label,
                scope=scope,
            )
        registered = self._connectors.get(kind)
        if registered is None:
            # 不回显 self._connectors.kinds：注册表是进程全局的，清单里可能含该
            # 用户不可见插件的 kind（kind 未按可见性过滤）。技能索引本就按用户
            # 可见性裁剪过，故引导模型去 skill.list 自查可用类型与参数格式。
            return ToolResult(
                ok=False,
                content=(f"未知的任务类型: {kind}。"
                         "请先用 skill.list 查看当前可用的技能，"
                         "从中确认任务类型与参数格式"),
                error="unknown_job_kind")
        connector = registered.connector
        if connector.plugin_id not in scope.allowed_plugins:
            return ToolResult(
                ok=False,
                content=f"当前会话未启用任务类型所属插件: {kind}",
                error="plugin_not_enabled",
            )
        ctx = await self._ctx_for(user_id, connector.plugin_id, ctx_extra)
        try:
            external_id = await connector.submit(params, ctx)
        except JobSubmitFailed as exc:
            return ToolResult(ok=False, content=f"任务提交失败: {exc}",
                              error="submit_failed")
        except Exception as exc:  # noqa: BLE001 连接器未归一化的异常也要对 LLM 可见
            _LOGGER.warning("任务提交异常 kind=%s", kind, exc_info=True)
            return ToolResult(ok=False, content=f"任务提交出错: {exc}",
                              error="submit_failed")
        doc = await self._repo.create({
            "_id": _new_job_id(),
            "kind": kind,
            "backend": "external",
            "plugin_id": connector.plugin_id,
            "status": JobStatus.PENDING.value,
            "session_id": session_id,
            "user_id": user_id,
            "external_id": str(external_id),
            "label": label,
            "params": params,
            # 工作区根落库：轮询路径据此重建 ctx（插件读用户文件上传时用）
            "workspace_root": str((ctx_extra or {}).get("workspace_root") or ""),
            # 代签身份落库：轮询路径据此现签用户凭证（提交时那份 1 小时就过期）
            "user_identity": dict(identity or {}),
            "result": "",
            "error": "",
        })
        return ToolResult(
            ok=True,
            content=(f"已提交后台任务「{label or kind}」，任务 ID: {doc['_id']}。"
                     "任务在后台独立执行，状态会显示在右侧运行信息；"
                     "用户需要时可用 job.status 或 job.list 查询。"),
            data={"job_id": doc["_id"], "status": doc["status"]})

    async def _submit_sandbox(
        self,
        *,
        session_id: str,
        user_id: str,
        kind: str,
        params: dict,
        label: str,
        scope: JobSubmissionScope,
    ) -> ToolResult:
        """登记并交接平台沙箱后台任务。"""
        if self._sandbox_runner is None or self._settings is None:
            return ToolResult(
                ok=False,
                content="平台沙箱后台任务当前不可用",
                error="sandbox_jobs_unavailable",
            )
        try:
            request = prepare_sandbox_job(kind, params, scope, self._settings)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                content=f"后台任务参数或权限非法: {exc}",
                error="invalid_arguments",
            )
        job_id = _new_job_id()
        owner = dict(scope.ownership)
        async def register_and_start() -> dict:
            doc = await self._repo.create({
                "_id": job_id,
                "backend": "sandbox",
                "kind": kind,
                "plugin_id": None,
                "external_id": None,
                "status": JobStatus.PENDING.value,
                "session_id": session_id,
                "user_id": user_id,
                "label": label,
                "params": params,
                "workspace_root": str(scope.workspace_root.resolve()),
                "workspace_owner": owner,
                "result": "",
                "error": "",
                "cancel_requested": False,
            })
            try:
                self._sandbox_runner.start(
                    job_id,
                    replace(request, execution_id=job_id),
                )
            except Exception:
                await self._repo.update(job_id, {
                    "status": JobStatus.FAILED.value,
                    "error": "后台任务启动失败",
                    "error_code": "start_failed",
                    "ended_at": time.time(),
                })
                raise
            return doc

        try:
            if self._workspace_guard is None:
                doc = await register_and_start()
            else:
                async with self._workspace_guard.hold(
                    user_id, scope.workspace_root, owner
                ):
                    if not scope.workspace_root.exists():
                        raise RuntimeError("任务工作区已不存在")
                    doc = await register_and_start()
        except Exception as exc:  # noqa: BLE001 存储失败不得启动容器
            _LOGGER.warning("沙箱任务登记或交接失败 kind=%s", kind, exc_info=True)
            return ToolResult(
                ok=False,
                content=f"后台任务登记或启动失败: {exc}",
                error="job_registration_failed",
            )
        return ToolResult(
            ok=True,
            content=(f"已提交后台任务「{label or kind}」，任务 ID: {job_id}。"
                     "任务已独立交接，停止当前回答不会停止该任务。"),
            data={"job_id": job_id, "status": doc["status"]},
        )

    async def mark_sandbox_running(self, job_id: str) -> None:
        """将已交接平台任务推进为 running。"""
        async with self._lock_for(job_id):
            doc = await self._repo.get(job_id)
            if doc is not None and doc.get("status") == JobStatus.PENDING.value:
                await self._repo.update(job_id, {
                    "status": JobStatus.RUNNING.value,
                    "started_at": time.time(),
                })

    async def recover_sandbox_jobs(self, executor: Any) -> None:
        """清理本部署重启前遗留的平台任务并标记中断。

        Args:
            executor: 提供 cleanup_execution 的当前部署执行器。
        """
        active = {JobStatus.PENDING.value, JobStatus.RUNNING.value}
        for doc in await self._repo.list():
            if doc.get("backend") != "sandbox" or doc.get("status") not in active:
                continue
            job_id = str(doc.get("_id") or "")
            if not job_id:
                continue
            stopped = await executor.cleanup_execution(job_id)
            if stopped:
                await self._repo.update(job_id, {
                    "status": JobStatus.FAILED.value,
                    "error": "服务进程重启，后台任务已中断且不会自动重跑",
                    "error_code": "process_interrupted",
                    "ended_at": time.time(),
                    "cancel_requested": False,
                })
            else:
                await self._repo.update(job_id, {
                    "cancel_requested": True,
                    "error": "服务进程重启，尚未确认遗留容器已停止",
                    "error_code": "cleanup_unconfirmed",
                })

    async def finish_sandbox(
        self,
        job_id: str,
        result: ToolResult,
        *,
        cancelled: bool = False,
    ) -> None:
        """在任务锁内收敛平台任务终态。"""
        async with self._lock_for(job_id):
            doc = await self._repo.get(job_id)
            if doc is None or is_terminal(doc.get("status")):
                return
            exit_code = result.data.get("exit_code")
            timed_out = bool(result.data.get("timed_out")) or result.error == "timeout"
            if cancelled:
                status = JobStatus.CANCELLED
                error_code = "cancelled"
            elif result.ok and exit_code in (None, 0):
                status = JobStatus.COMPLETED
                error_code = ""
            else:
                status = JobStatus.FAILED
                error_code = "timeout" if timed_out else (result.error or "nonzero_exit")
            await self._repo.update(job_id, {
                "status": status.value,
                "result": result.content[-65_536:],
                "exit_code": exit_code,
                "timed_out": timed_out,
                "truncated": result.truncated or len(result.content) > 65_536,
                "error": "" if status is JobStatus.COMPLETED else result.content[-4000:],
                "error_code": error_code,
                "ended_at": time.time(),
                "cancel_requested": False,
            })
    async def describe(self, job_id: str, *, user_id: str,
                       refresh: bool = False) -> ToolResult:
        """查单个任务（可选先同步刷新一次状态，让用户看到最新进度）。

        Args:
            job_id: 任务 id。
            user_id: 调用者（非本人一律 not_found，不泄露存在性）。
            refresh: 是否先向外部系统拉一次最新状态（终态任务不刷新）。

        Returns:
            工具结果；content 为任务摘要。
        """
        doc = await self._repo.get(job_id)
        if doc is None or str(doc.get("user_id")) != user_id:
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        if (refresh and doc.get("backend") == "external"
                and not is_terminal(doc.get("status"))):
            refreshed = await self.refresh(doc)
            if refreshed is not None:
                doc = refreshed
        return ToolResult(ok=True, content=self._render(doc),
                          data={
                              "job_id": doc.get("_id", ""),
                              "status": doc.get("status", ""),
                              "exit_code": doc.get("exit_code"),
                              "timed_out": bool(doc.get("timed_out")),
                              "truncated": bool(doc.get("truncated")),
                              "error_code": doc.get("error_code", ""),
                          })

    def _lock_for(self, job_id: str) -> asyncio.Lock:
        """取该任务的串行锁（任务级，防并发刷新重复推进）。

        Args:
            job_id: 任务 id。

        Returns:
            该任务对应的锁（首次访问时创建）。
        """
        lock = self._job_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._job_locks[job_id] = lock
        return lock

    async def refresh(self, doc: dict) -> dict | None:
        """向外部系统拉一次最新状态并落库（轮询与手工查询共用）。

        并发安全：任务级锁内**重读**文档——两个调用方（poller 的 tick 与模型
        调 job.status）可能拿到同一份非终态快照，串行化后后到者在锁内会看到
        已落库的终态从而早返回，同一任务只调一次外部 poll。

        Args:
            doc: 任务文档（仅用于取 id；真实依据是锁内重读的文档）。

        Returns:
            更新后的文档；任务不存在返回 None。

        Note:
            查询失败或状态未映射时**保持原状态**并累计 poll_failures——
            一次网络抖动不得把任务判为失败，也不得让状态倒退。
        """
        job_id = str(doc.get("_id", ""))
        if not job_id:
            return doc
        async with self._lock_for(job_id):
            # 锁内重读：并发的第二个调用方在这里会看到已推进的状态
            fresh = await self._repo.get(job_id)
            if fresh is not None:
                doc = fresh
            return await self._refresh_locked(doc)

    async def _fetch_optional(self, connector: Any, hook: str, external_id: str,
                              ctx: dict) -> str:
        """调用连接器的可选取数能力（未实现或失败时返回空串）。

        Args:
            connector: 连接器实例。
            hook: 方法名（连接器协议未定义者，用 getattr 探测以不破坏既有
                连接器的 isinstance 校验）。
            external_id: 外部任务 id。
            ctx: 连接器调用上下文。

        Returns:
            文本；连接器未实现该方法、或调用失败时返回空串（只告警，不影响
            状态落地）。
        """
        fetch = getattr(connector, hook, None)
        if not callable(fetch):
            return ""
        try:
            return str(await fetch(external_id, ctx) or "")
        except Exception:  # noqa: BLE001 取数失败不阻断状态流转
            _LOGGER.warning("连接器取数失败 hook=%s external_id=%s",
                            hook, external_id, exc_info=True)
            return ""

    async def _refresh_locked(self, doc: dict) -> dict | None:
        """refresh 的实际逻辑（调用方须持有该任务的锁）。

        Args:
            doc: 锁内重读后的任务文档。

        Returns:
            更新后的文档；任务不存在返回 None。
        """
        if doc.get("backend") != "external":
            return doc
        try:
            status = JobStatus(doc["status"])
        except (KeyError, ValueError):
            return doc
        if is_terminal(status):
            return doc
        registered = self._connectors.get(str(doc.get("kind", "")))
        if registered is None:
            # 连接器消失（插件被卸载）：任务无法继续跟踪，标记失败并说明原因
            failed = await self._repo.update(doc["_id"], {
                "status": JobStatus.FAILED.value,
                "error": f"任务类型已不可用: {doc.get('kind')}",
                "ended_at": time.time(),
            })
            return failed
        ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                  registered.connector.plugin_id,
                                  workspace_root=str(doc.get("workspace_root") or ""),
                                  identity=doc.get("user_identity") or {})
        try:
            raw = await registered.connector.poll(str(doc.get("external_id", "")), ctx)
        except JobPollFailed as exc:
            return await self._repo.update(doc["_id"], {
                "poll_failures": int(doc.get("poll_failures") or 0) + 1,
                "last_poll_error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001 未归一化异常同样只记不改状态
            _LOGGER.warning("任务轮询异常 job=%s", doc.get("_id"), exc_info=True)
            return await self._repo.update(doc["_id"], {
                "poll_failures": int(doc.get("poll_failures") or 0) + 1,
                "last_poll_error": str(exc),
            })
        mapped = registered.map_status(raw)
        if mapped is None:
            # 外部状态不在映射表内：保持原状态（并记录原文，便于补映射）
            return await self._repo.update(doc["_id"], {"last_raw_status": str(raw)})
        if not can_transition(status, mapped):
            _LOGGER.warning("任务状态非法流转 job=%s %s -> %s",
                            doc.get("_id"), status, mapped)
            return doc
        fields: dict = {"status": mapped.value, "last_raw_status": str(raw)}
        if is_terminal(mapped):
            fields["ended_at"] = time.time()
        updated = await self._repo.update(doc["_id"], fields)
        if mapped is JobStatus.COMPLETED:
            # 成功终态回填结果，供任务详情和后续主动查询读取。
            result = await self._fetch_optional(
                registered.connector, "fetch_result",
                str(doc.get("external_id", "")), ctx)
            if result:
                updated = await self._repo.update(doc["_id"], {"result": result})
        elif mapped is JobStatus.FAILED:
            # 失败终态回填上游给出的失败原因：上游状态接口的 message 往往只是
            # "failed"，真正的原因（如"暂不支持Raman的greedy_decode模式"）在结果
            # 接口的 error 字段里。不回填的话，job.status 只有一个 "failed"，
            # 后续查询无法解释失败原因。
            reason = await self._fetch_optional(
                registered.connector, "fetch_error",
                str(doc.get("external_id", "")), ctx)
            if reason:
                updated = await self._repo.update(doc["_id"], {"error": reason})
        return updated

    async def render_list(self, session_id: str, *, user_id: str) -> ToolResult:
        """列出本会话任务（供模型汇报整体进度）。"""
        docs = [d for d in await self.list_for_session(session_id)
                if str(d.get("user_id")) == user_id]
        if not docs:
            return ToolResult(ok=True, content="本会话还没有提交过后台任务。",
                              data={"count": 0})
        return ToolResult(ok=True, content="\n".join(self._render(d) for d in docs),
                          data={"count": len(docs)})

    async def cancel(self, job_id: str, *, user_id: str) -> ToolResult:
        """取消任务（已结束的任务拒绝并说明）。

        并发安全：与 `refresh` 共用同一把任务级锁并在锁内重读文档。连接器取消
        耗时较长（网络 await），不取锁时轮询会在该窗口内落地状态、随后被
        `_refresh_locked` 的整字段写覆盖（cancelled 回退为 running，任务"复活"
        继续被轮询）。取锁后取消一定落在最新状态之上，其后轮询看到终态即早返回。

        Args:
            job_id: 任务 id。
            user_id: 调用者（非本人 not_found）。

        Returns:
            工具结果。
        """
        if not job_id:
            # 工具层已校验非空，此处防御：空串不该造出一把空键锁
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        doc = await self._repo.get(job_id)
        if doc is None or str(doc.get("user_id")) != user_id:
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        if doc.get("backend") == "sandbox":
            return await self._cancel_sandbox(doc)
        async with self._lock_for(job_id):
            # 锁内重读：并发的 refresh 可能刚把任务推进到终态，以最新状态为准
            doc = await self._repo.get(job_id)
            if doc is None or str(doc.get("user_id")) != user_id:
                return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                                  error="not_found")
            # 与 describe 同一口径：脏状态（缺失/未知字符串）一律当作"非终态"，
            # 不裸转换 JobStatus（KeyError/ValueError 会以内部报错文本逃到模型侧）。
            # 对合法状态而言 is_terminal 即 can_transition(_, CANCELLED) 取反
            # （流转表里只有终态没有指向 CANCELLED 的出边）。
            raw_status = str(doc.get("status", ""))
            if is_terminal(raw_status):
                return ToolResult(
                    ok=False,
                    content=f"任务已结束（{raw_status}），无需取消。",
                    error="already_finished")
            registered = self._connectors.get(str(doc.get("kind", "")))
            accepted = False
            if registered is not None:
                ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                          registered.connector.plugin_id,
                                          workspace_root=str(
                                              doc.get("workspace_root") or ""),
                                          identity=doc.get("user_identity") or {})
                try:
                    accepted = bool(await registered.connector.cancel(
                        str(doc.get("external_id", "")), ctx))
                except Exception:  # noqa: BLE001 取消失败不阻断本地状态收敛
                    _LOGGER.warning("任务取消失败 job=%s", job_id, exc_info=True)
            await self._repo.update(job_id, {
                "status": JobStatus.CANCELLED.value,
                "cancel_accepted": accepted,
                "ended_at": time.time(),
            })
        note = "已请求取消" if accepted else "已标记取消（外部系统未确认）"
        return ToolResult(ok=True, content=f"任务 {job_id} {note}。",
                          data={"job_id": job_id, "status": "cancelled"})

    async def _cancel_sandbox(self, doc: dict) -> ToolResult:
        """取消平台沙箱任务并等待停止确认。"""
        job_id = str(doc["_id"])
        async with self._lock_for(job_id):
            fresh = await self._repo.get(job_id)
            if fresh is None:
                return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                                  error="not_found")
            if is_terminal(fresh.get("status")):
                return ToolResult(
                    ok=False,
                    content=f"任务已结束（{fresh.get('status')}），无需取消。",
                    error="already_finished",
                )
            await self._repo.update(job_id, {"cancel_requested": True})
        if self._sandbox_runner is None:
            return ToolResult(
                ok=False,
                content=f"任务 {job_id} 已请求停止，但运行器不可用，尚未确认容器停止。",
                error="cancel_unconfirmed",
            )
        stopped = await self._sandbox_runner.cancel(job_id)
        if not stopped:
            return ToolResult(
                ok=False,
                content=f"任务 {job_id} 已请求停止，但尚未确认容器停止。",
                error="cancel_unconfirmed",
                data={"job_id": job_id, "cancel_requested": True},
            )
        fresh = await self._repo.get(job_id)
        return ToolResult(
            ok=True,
            content=f"任务 {job_id} 已停止。",
            data={"job_id": job_id,
                  "status": (fresh or {}).get("status", "cancelled")},
        )

    @staticmethod
    def _render(doc: dict) -> str:
        """任务文档 → 一行摘要（给 LLM 与用户看）。"""
        parts = [f"[{doc['_id']}] {doc.get('label') or doc.get('kind')}"]
        if doc.get("params"):
            params = json.dumps(doc["params"], ensure_ascii=False, default=str)
            parts.append(f"参数: {_clip(params, 200)}")
        parts.append(f"状态: {doc.get('status')}")
        if doc.get("backend") == "sandbox":
            if doc.get("exit_code") is not None:
                parts.append(f"退出码: {doc.get('exit_code')}")
            if doc.get("timed_out"):
                parts.append("执行超时: 是")
            owner = doc.get("workspace_owner") or {}
            if owner.get("project_id"):
                parts.append(f"工作区归属: 项目 {owner['project_id']}")
            elif owner.get("session_id"):
                parts.append(f"工作区归属: 会话 {owner['session_id']}")
        failures = int(doc.get("poll_failures") or 0)
        if failures:
            # 轮询失败刻意不改状态（防网络抖动误判失败），但必须让模型/用户看见：
            # 否则 401 这类不可自愈的失败会把任务静默挂成 pending，模型只会说"正常"
            parts.append(f"轮询失败 {failures} 次: "
                         f"{_clip(str(doc.get('last_poll_error') or '原因未知'), 200)}")
        if doc.get("error"):
            parts.append(f"错误: {_clip(str(doc['error']), 400)}")
        if doc.get("result"):
            parts.append(f"结果: {_clip(str(doc['result']))}")
        return " | ".join(parts)
