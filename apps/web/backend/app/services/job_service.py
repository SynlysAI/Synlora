"""后台任务服务：提交、查询、取消、状态流转与完成唤醒。

职责边界：本服务只认 JobConnector 协议（怎么跟外部系统说话）与 JobRepo
（任务文档在哪），不认识任何具体子平台。任务完成后的"唤醒 agent 继续处理"
经装配阶段注入的 wake 回调完成（`set_wake_callback` / `set_busy_check`）。

状态流转与取消（Task 6）已实现：`refresh` 把外部状态原文映射为统一状态并校验
合法流转（查询失败/未映射一律保持原状态、只累计 poll_failures），`cancel` 调
连接器请求取消后本地收敛为 cancelled（终态任务拒绝取消）。

完成唤醒（Task 9）：`refresh` 落到终态后调 `_notify_wake`——会话空闲立即唤醒
agent 整合结果，会话忙则进 `_pending_wake` 排队，等该会话的 run 结束后由
`drain_pending`（AgentService 的 run 结束回调驱动）补发。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from synlys_harness import (
    ACTIVE_STATUSES,
    JobStatus,
    ToolResult,
    can_transition,
    is_terminal,
)

from app.services.agent_service import TooManyRuns, WakeTargetGone
from app.services.job_connectors import (
    JobConnectorRegistry,
    JobPollFailed,
    JobSubmitFailed,
)

_LOGGER = logging.getLogger(__name__)

# 摘要文本里注入给 LLM 的结果上限（超出截断，完整结果可由外部系统/后续工具取）
RESULT_PREVIEW_CHARS = 4000

WakeCallback = Callable[[str, str, str], Awaitable[None]]
"""唤醒回调签名：(session_id, text, job_id) -> None。"""


def _new_job_id() -> str:
    """生成任务 id。"""
    return "job-" + uuid.uuid4().hex[:12]


def _clip(text: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    """截断长文本并标注。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字）"


class JobService:
    """后台任务编排（单例，挂 app.state.job_service）。"""

    def __init__(self, repo: Any, connectors: JobConnectorRegistry,
                 plugin_config_store: Any = None,
                 ai4ms_identity: Any = None) -> None:
        """保存依赖。

        Args:
            repo: JobRepo（任务文档读写）。
            connectors: 连接器注册表（kind → 连接器）。
            plugin_config_store: 插件配置存储（轮询时按 job 的 user_id 重新解析
                配置；None 时轮询只用空配置）。
            ai4ms_identity: AI⁴MS 代签服务（预留）。注意：轮询路径目前只带
                user_id，而代签需要完整用户 payload，故 `_ctx_for` 的轮询
                分支不注入用户凭证——需要凭证的插件须依赖配置里的服务 token。
        """
        self._repo = repo
        self._connectors = connectors
        self._plugin_config_store = plugin_config_store
        self._ai4ms_identity = ai4ms_identity
        self._wake: WakeCallback | None = None
        # 待唤醒会话队列：会话忙时任务先在这里排队，run 结束后 drain
        self._pending_wake: dict[str, list[str]] = {}
        # 会话忙判定（装配阶段注入 AgentService.is_busy；缺省视为空闲）
        self._busy_check: Callable[[str], bool] = lambda _sid: False

    # ---------- 查询 ----------

    async def get(self, job_id: str) -> dict | None:
        """按 id 取任务文档。"""
        return await self._repo.get(job_id)

    async def list_for_session(self, session_id: str) -> list[dict]:
        """列出某会话的全部任务（创建时间升序）。"""
        docs = await self._repo.list(filters={"session_id": session_id})
        return sorted(docs, key=lambda d: float(d.get("created_at") or 0))

    async def list_active(self) -> list[dict]:
        """列出全部未完成任务（轮询入口；终态任务不再纳入）。"""
        active_values = {s.value for s in ACTIVE_STATUSES}
        docs = await self._repo.list()
        return [d for d in docs if d.get("status") in active_values]

    # ---------- 完成唤醒 ----------

    def set_wake_callback(self, callback: WakeCallback) -> None:
        """注入唤醒回调（装配阶段传 AgentService 的唤醒包装函数）。

        Args:
            callback: (session_id, text, job_id) -> None。
        """
        self._wake = callback

    def set_busy_check(self, check: Callable[[str], bool]) -> None:
        """注入会话忙判定（装配阶段传 AgentService.is_busy）。

        Args:
            check: (session_id) -> bool。
        """
        self._busy_check = check

    def pending_wake_count(self, session_id: str) -> int:
        """该会话待唤醒的任务数（可观测/测试用）。"""
        return len(self._pending_wake.get(session_id, []))

    async def _notify_wake(self, doc: dict) -> None:
        """任务终态后通知会话（空闲立即唤醒，忙则排队）。

        Args:
            doc: 已进入终态的任务文档。
        """
        job_id = str(doc.get("_id", ""))
        session_id = str(doc.get("session_id", ""))
        if not job_id or not session_id:
            return
        if self._busy_check(session_id):
            self._pending_wake.setdefault(session_id, []).append(job_id)
            return
        await self._wake_now(doc)

    async def _wake_now(self, doc: dict) -> None:
        """立即唤醒（回调缺失记日志跳过；失败不抛出，避免打挂轮询循环）。

        Args:
            doc: 任务文档。
        """
        if self._wake is None:
            _LOGGER.warning("未注入唤醒回调，任务完成通知被丢弃 job=%s", doc.get("_id"))
            return
        session_id = str(doc.get("session_id", ""))
        job_id = str(doc.get("_id", ""))
        try:
            await self._wake(session_id, self.compose_wake_text(doc), job_id)
        except TooManyRuns:
            # "判定空闲"到"chat 取会话锁"之间有窗口（解析装配含多个 await），
            # 用户此刻发消息就会撞上：重新入队等本轮结束补发，不丢通知
            self._pending_wake.setdefault(session_id, []).append(job_id)
        except WakeTargetGone:
            # 会话已删（delete_session 不清理 jobs）：通知无接收方，静默跳过
            _LOGGER.info("唤醒目标已不存在，跳过 job=%s", job_id)
        except Exception:  # noqa: BLE001 唤醒失败不得影响轮询循环
            _LOGGER.warning("任务完成唤醒失败 session=%s job=%s",
                            session_id, job_id, exc_info=True)

    async def drain_pending(self, session_id: str) -> None:
        """会话空闲后补发待唤醒任务（由 AgentService 的 run 结束回调驱动）。

        逐条出队处理：单条失败或被重新入队，都不牵连同会话其余的待唤醒通知。

        Args:
            session_id: 会话 id。
        """
        pending = list(self._pending_wake.get(session_id, []))
        if not pending:
            return
        self._pending_wake.pop(session_id, None)
        for job_id in pending:
            try:
                doc = await self._repo.get(job_id)
            except Exception:  # noqa: BLE001 单条读取失败不牵连同会话其余通知
                _LOGGER.warning("读取待唤醒任务失败 job=%s", job_id, exc_info=True)
                continue
            if doc is None:
                continue
            await self._wake_now(doc)

    @staticmethod
    def compose_wake_text(doc: dict) -> str:
        """组装唤醒文本（模型据此继续处理任务结果）。

        Args:
            doc: 任务文档。

        Returns:
            系统通知文本。
        """
        lines = [
            "【系统通知】你之前提交的后台任务已结束，请据此继续完成任务。",
            f"任务 ID：{doc.get('_id')}",
            f"任务类型：{doc.get('kind')}",
            f"任务说明：{doc.get('label') or '（无）'}",
            f"最终状态：{doc.get('status')}",
        ]
        if doc.get("error"):
            lines.append(f"错误信息：{_clip(str(doc['error']), 1000)}")
        if doc.get("result"):
            lines.append(f"任务结果：\n{_clip(str(doc['result']))}")
        lines.append("若结果已足够，直接向用户汇报结论；若还需补充计算，可继续调用工具。")
        return "\n".join(lines)

    # ---------- 配置解析（提交/轮询共用） ----------

    async def _ctx_for(self, user_id: str, plugin_id: str,
                       ctx_extra: dict | None = None) -> dict:
        """构造连接器调用上下文。

        Args:
            user_id: 任务归属用户（轮询路径用它重新解析配置）。
            plugin_id: 归属插件 id。
            ctx_extra: 提交路径可直接给出的运行上下文（含 plugins/ai4ms_token）；
                为 None 时（轮询路径）从插件配置存储与代签服务重建。

        Returns:
            {"config": {插件配置}, "ai4ms_token": "<token 或空串>"}。
        """
        if ctx_extra is not None:
            plugins = ctx_extra.get("plugins") or {}
            return {
                "config": dict(plugins.get(plugin_id) or {}),
                "ai4ms_token": str(ctx_extra.get("ai4ms_token") or ""),
            }
        config: dict = {}
        if self._plugin_config_store is not None:
            try:
                config = await self._plugin_config_store.resolved_for_user(
                    user_id, plugin_id)
            except Exception:  # noqa: BLE001 解密失败等：按无配置处理，任务照常轮询
                _LOGGER.warning("轮询时解析插件配置失败 plugin=%s user=%s",
                                plugin_id, user_id, exc_info=True)
        return {"config": config, "ai4ms_token": ""}

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
                label=str(payload.get("label", "")), ctx_extra=ctx_extra)
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
                     params: dict, label: str, ctx_extra: dict) -> ToolResult:
        """提交任务：调连接器 → 落库 pending → 立即返回（不等待任务完成）。

        Args:
            session_id: 所属会话。
            user_id: 所属用户。
            kind: 任务类型。
            params: 任务参数。
            label: 任务简述（给用户看）。
            ctx_extra: 本轮运行上下文（取插件配置与代签 token）。

        Returns:
            ok=True 且 data["job_id"]；失败时错误码为 unknown_job_kind /
            submit_failed。
        """
        registered = self._connectors.get(kind)
        if registered is None:
            kinds = "、".join(self._connectors.kinds) or "（当前无可用的任务类型）"
            return ToolResult(
                ok=False,
                content=f"未注册的任务类型: {kind}。可用类型：{kinds}",
                error="unknown_job_kind")
        connector = registered.connector
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
            "plugin_id": connector.plugin_id,
            "status": JobStatus.PENDING.value,
            "session_id": session_id,
            "user_id": user_id,
            "external_id": str(external_id),
            "label": label,
            "params": params,
            "result": "",
            "error": "",
        })
        return ToolResult(
            ok=True,
            content=(f"已提交后台任务「{label or kind}」，任务 ID: {doc['_id']}。"
                     "任务在后台执行，完成时系统会自动通知你继续处理；"
                     "现在不要重复提交，也不必轮询状态。"),
            data={"job_id": doc["_id"], "status": doc["status"]})

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
        if refresh and not is_terminal(doc.get("status")):
            refreshed = await self.refresh(doc)
            if refreshed is not None:
                doc = refreshed
        return ToolResult(ok=True, content=self._render(doc),
                          data={"job_id": doc.get("_id", ""),
                                "status": doc.get("status", "")})

    async def refresh(self, doc: dict) -> dict | None:
        """向外部系统拉一次最新状态并落库（轮询与手工查询共用）。

        Args:
            doc: 任务文档。

        Returns:
            更新后的文档；任务不存在返回 None。

        Note:
            查询失败或状态未映射时**保持原状态**并累计 poll_failures——
            一次网络抖动不得把任务判为失败，也不得让状态倒退。
        """
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
            await self._notify_wake(failed or doc)
            return failed
        ctx = await self._ctx_for(str(doc.get("user_id", "")),
                                  registered.connector.plugin_id)
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
        if is_terminal(mapped):
            # 终态：唤醒会话，让 agent 继续整合结果
            await self._notify_wake(updated or doc)
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

        Args:
            job_id: 任务 id。
            user_id: 调用者（非本人 not_found）。

        Returns:
            工具结果。
        """
        doc = await self._repo.get(job_id)
        if doc is None or str(doc.get("user_id")) != user_id:
            return ToolResult(ok=False, content=f"任务不存在: {job_id}",
                              error="not_found")
        # 与 describe 同一口径：脏状态（缺失/未知字符串）一律当作"非终态"，
        # 不裸转换 JobStatus（KeyError/ValueError 会以内部报错文本逃到模型侧）
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
                                      registered.connector.plugin_id)
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

    @staticmethod
    def _render(doc: dict) -> str:
        """任务文档 → 一行摘要（给 LLM 与用户看）。"""
        parts = [f"[{doc['_id']}] {doc.get('label') or doc.get('kind')}"]
        if doc.get("params"):
            params = json.dumps(doc["params"], ensure_ascii=False, default=str)
            parts.append(f"参数: {_clip(params, 200)}")
        parts.append(f"状态: {doc.get('status')}")
        if doc.get("error"):
            parts.append(f"错误: {_clip(str(doc['error']), 400)}")
        if doc.get("result"):
            parts.append(f"结果: {_clip(str(doc['result']))}")
        return " | ".join(parts)
