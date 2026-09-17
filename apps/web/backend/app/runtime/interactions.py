"""宿主用户交互协议到中立核心类型的翻译。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from synlys_harness import ApprovalDecision

AskHandler = Callable[[dict], Awaitable[str]]
ApprovalHandler = Callable[[dict], Awaitable[ApprovalDecision]]


def make_approval_handler(ask_handler: AskHandler) -> ApprovalHandler:
    """构造结构化审批回调。

    Args:
        ask_handler: 发送审批问题并等待用户文本的宿主回调。
    Returns:
        工具管线消费的结构化审批回调。
    """
    async def approve(payload: dict) -> ApprovalDecision:
        reply = await ask_handler(payload)
        answer = reply.strip()
        return ApprovalDecision(
            approved=answer == "允许",
            reason="" if answer == "允许" else answer,
        )

    return approve
