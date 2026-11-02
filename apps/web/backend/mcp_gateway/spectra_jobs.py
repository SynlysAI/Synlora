"""五类谱图异步解析任务包装为 MCP 工具（复用 SpectraTaskConnector）。

上游契约：上传→提交→轮询→取结果；学生 SD 经 MCP 以 base64 传文件，网关落到
单次调用的临时工作区后走连接器原路径（含路径越界防护）。
"""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path

from catalog.plugins.spec_agent.connectors import SpectraTaskConnector

SPECTRA_KINDS = {
    "nmr": SpectraTaskConnector("spec.task.nmr", "nmr"),
    "ir": SpectraTaskConnector("spec.task.ir", "ir"),
    "gpc": SpectraTaskConnector("spec.task.gpc", "gpc"),
    "raman": SpectraTaskConnector("spec.task.raman", "raman"),
    "lcms": SpectraTaskConnector("spec.task.lcms", "lcms"),
}


def _job_ctx(spec_config: dict, workspace_root=None) -> dict:
    """构造连接器上下文（宿主同构 config + 临时工作区）。"""
    return {"config": spec_config, "workspace_root": workspace_root}


async def spectra_submit(args: dict, spec_config: dict) -> str:
    """提交一类谱图解析任务，返回上游 task_id。

    Args:
        args: MCP 入参 {kind, file_name, file_base64, params?}。
        spec_config: spec_agent 插件配置（base_url/token）。

    Returns:
        上游 task_id 文本。
    """
    kind = str(args.get("kind") or "")
    connector = SPECTRA_KINDS.get(kind)
    if connector is None:
        return f"ERROR: kind 必须是 {'/'.join(SPECTRA_KINDS)}"
    file_name = str(args.get("file_name") or "spectrum.bin")
    if "/" in file_name or "\\" in file_name or file_name in (".", ".."):
        return "ERROR: file_name 必须是纯文件名"
    try:
        content = base64.b64decode(str(args.get("file_base64") or ""), validate=True)
    except (binascii.Error, ValueError):
        return "ERROR: file_base64 不是合法的 base64"
    if not content:
        return "ERROR: file_base64 为空"
    with tempfile.TemporaryDirectory(prefix="mcp-spectra-") as tmp:
        root = Path(tmp)
        (root / file_name).write_bytes(content)
        params = dict(args.get("params") or {})
        task_id = await connector.submit(
            {"path": file_name, "params": params}, _job_ctx(spec_config, str(root))
        )
    return task_id


async def spectra_status(args: dict, spec_config: dict) -> str:
    """查询谱图任务状态原文（RUNNING/SUCCEEDED/...）。"""
    connector = SPECTRA_KINDS.get(str(args.get("kind") or ""))
    if connector is None:
        return f"ERROR: kind 必须是 {'/'.join(SPECTRA_KINDS)}"
    return await connector.poll(str(args.get("task_id") or ""), _job_ctx(spec_config))


async def spectra_result(args: dict, spec_config: dict) -> str:
    """取谱图任务结果文本（失败时取错误详情）。"""
    connector = SPECTRA_KINDS.get(str(args.get("kind") or ""))
    if connector is None:
        return f"ERROR: kind 必须是 {'/'.join(SPECTRA_KINDS)}"
    task_id = str(args.get("task_id") or "")
    ctx = _job_ctx(spec_config)
    status = await connector.poll(task_id, ctx)
    if str(status).upper() in ("SUCCEEDED", "SUCCESS", "DONE"):
        return await connector.fetch_result(task_id, ctx)
    return f"ERROR: 任务未完成（当前状态 {status}），完成后可再次调用"


async def spectra_cancel(args: dict, spec_config: dict) -> str:
    """取消谱图任务。"""
    connector = SPECTRA_KINDS.get(str(args.get("kind") or ""))
    if connector is None:
        return f"ERROR: kind 必须是 {'/'.join(SPECTRA_KINDS)}"
    cancelled = await connector.cancel(str(args.get("task_id") or ""), _job_ctx(spec_config))
    return "CANCELLED" if cancelled else "ERROR: 取消失败或任务已结束"
