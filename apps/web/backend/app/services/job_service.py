"""后台任务服务：提交、查询、取消、状态流转与完成唤醒。

职责边界：本服务只认 JobConnector 协议（怎么跟外部系统说话）与 JobRepo
（任务文档在哪），不认识任何具体子平台。任务完成后的"唤醒 agent 继续处理"
通过构造期注入的 wake 回调完成（Task 9 提供 set_wake_callback）。

状态流转与取消（Task 6）在本类里以最小可用形态存在：`refresh` 暂不刷新、
`cancel` 返回 not_implemented，保证「只查询、不流转」时服务自洽可交付。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Awaitable, Callable

from synlys_harness import (
    ACTIVE_STATUSES,
    JobStatus,
    ToolResult,
    is_terminal,
)

from app.services.job_connectors import (
    JobConnectorRegistry,
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
        """向外部系统拉一次最新状态并落库（Task 6 实现真实逻辑）。

        Args:
            doc: 任务文档。

        Returns:
            更新后的任务文档；本任务尚未提供刷新时返回 None，
            调用方沿用原文档。
        """
        return None

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
        """取消任务（真实实现见 Task 6）。"""
        return ToolResult(ok=False, content="取消功能尚未提供", error="not_implemented")

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
