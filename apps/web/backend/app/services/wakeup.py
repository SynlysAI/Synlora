"""任务完成唤醒的运行装配解析（main 接线注入 AgentService）。"""
from __future__ import annotations

import logging
from typing import Any

from app.services.session_runtime import resolve_session_runtime

_LOGGER = logging.getLogger(__name__)


def make_run_context_resolver(settings: Any, project_service: Any, repos: Any,
                              expert_service: Any = None):
    """构造唤醒装配解析回调（闭包持有装配依赖）。

    Args:
        settings: 应用配置。
        project_service: 项目服务（绑定项目解析）。
        repos: repo 集中访问对象。
        expert_service: 用户专家服务（自建专家路由）。

    Returns:
        async def(session_id, user_id) -> dict | None，供
        AgentService.set_run_context_resolver 注入。
    """

    async def resolve(session_id: str, user_id: str) -> dict | None:
        """重放 send_message 的装配：会话不存在/非本人/模型不可用时放弃唤醒。

        Args:
            session_id: 任务归属会话 id。
            user_id: 任务提交者 sub（job 文档落库值）。

        Returns:
            chat 所需装配字典；放弃唤醒时 None。
        """
        if not session_id or not user_id:
            return None
        doc = await repos.session.get(session_id)
        if doc is None or str(doc.get("user_id") or "") != user_id:
            return None
        try:
            runtime = await resolve_session_runtime(
                settings, project_service, repos, doc, {"sub": user_id},
                expert_service=expert_service)
        except Exception:
            _LOGGER.info("唤醒放弃：会话 %s 装配不可用", session_id)
            return None
        return {
            "user": {"sub": user_id},
            "assistant": runtime.assistant,
            "provider_cfg": runtime.provider_cfg,
            "workspace_root": runtime.workspace_root,
            "ownership": runtime.ownership,
            "enabled_plugins": doc.get("enabled_plugins"),
            "enabled_mcp": doc.get("enabled_mcp"),
            "research_context": doc.get("research_context"),
        }

    return resolve
