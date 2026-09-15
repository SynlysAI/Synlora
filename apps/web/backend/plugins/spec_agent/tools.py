"""Spec_Agent 核磁预测工具（插件包自带，宿主动态加载）。

配置由宿主按命名空间注入 ctx.extra["plugins"]["spec_agent"]（见 plugin.json 的
config_schema），本模块不认识任何宿主全局配置——插件只依赖宿主给出的这个通用通道。
上游契约见 Spec_Agent `backend/app/api/v1/endpoints/nmr_server.py`。
"""
from __future__ import annotations

import json

import httpx

from synlys_harness import ToolContext, ToolResult, tool

PLUGIN_ID = "spec_agent"
REQUEST_TIMEOUT_S = 350.0   # 上游模型推理 read_timeout
TOOL_TIMEOUT_S = 380.0      # 工具级超时必须大于 HTTP 超时：让 HTTP 层先报错


def _config(ctx: ToolContext) -> dict:
    """取本插件在运行上下文中的配置（宿主按插件 id 命名空间注入）。

    Args:
        ctx: 工具上下文。

    Returns:
        插件配置 dict（未安装/未配置时为空 dict）。
    """
    return (ctx.extra.get("plugins") or {}).get(PLUGIN_ID) or {}


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端（测试经 monkeypatch 注入 MockTransport）。

    Returns:
        配好超时的 httpx 异步客户端。
    """
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)


async def _call_nmrserver(ctx: ToolContext, path: str, payload: dict) -> ToolResult:
    """调用 NMRServer 端点并归一化为工具结果。

    Args:
        ctx: 工具上下文（取插件配置）。
        path: 端点路径段（forward/reverse/search）。
        payload: 请求体（字段名与上游 pydantic 模型一致）。

    Returns:
        ToolResult：content 为逐条 JSON（items）或"（未返回结果）"；失败时
        error 为 spec_agent_unconfigured / http_error / upstream_error。
    """
    config = _config(ctx)
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not base_url:
        return ToolResult(
            ok=False,
            content="谱图解析插件未配置服务地址（请在管理后台的插件页安装并填写）",
            error="spec_agent_unconfigured",
        )
    headers: dict[str, str] = {}
    token = str(config.get("token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with _make_client() as client:
            resp = await client.post(
                f"{base_url}/api/v1/nmrserver/{path}", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"谱图服务请求失败: {exc}", error="http_error")
    if resp.status_code != 200:
        return ToolResult(
            ok=False,
            content=f"谱图服务返回 {resp.status_code}: {resp.text[:300]}",
            error="http_error",
        )
    try:
        body = resp.json() or {}
    except ValueError:
        return ToolResult(
            ok=False, content=f"谱图服务返回非 JSON: {resp.text[:200]}",
            error="upstream_error")
    if body.get("code") != 0:
        return ToolResult(
            ok=False,
            content=f"谱图服务错误: {body.get('message') or body.get('code')}",
            error="upstream_error",
        )
    data = body.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return ToolResult(ok=True, content="（未返回结果）", data={"items": 0})
    return ToolResult(
        ok=True,
        content="\n".join(json.dumps(item, ensure_ascii=False) for item in items),
        data={"items": len(items)},
    )


@tool(
    name="spec.nmr.forward",
    description=(
        "核磁正向预测：给定分子 SMILES（可多行，每行一个），预测其 13C/1H 化学位移。"
        "用于验证「推测结构理论上应出什么谱」。模型推理较慢，单次可能数分钟，"
        "调用后请耐心等待，不要连续重复提交同一请求。"
    ),
    parameters={"type": "object", "properties": {
        "smiles_input": {"type": "string", "description": "多行 SMILES，每行一个分子"},
    }, "required": ["smiles_input"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_forward(ctx: ToolContext, args: dict) -> ToolResult:
    """正向核磁预测（SMILES → 化学位移）。

    Args:
        ctx: 工具上下文。
        args: 含 smiles_input。

    Returns:
        ToolResult（每行一个分子的预测结果 JSON）。
    """
    return await _call_nmrserver(ctx, "forward", {"smiles_input": args["smiles_input"]})


@tool(
    name="spec.nmr.reverse",
    description=(
        "核磁反向预测：给定实测化学位移（碳谱/氢谱，逗号分隔的数值串），推断可能的"
        "分子结构候选。可给分子式/允许元素/候选分子约束提高命中率。"
        "模型推理较慢，单次可能数分钟，不要连续重复提交。"
    ),
    parameters={"type": "object", "properties": {
        "c_shifts_input": {"type": "string", "description": "碳谱化学位移，逗号分隔"},
        "h_shifts_input": {"type": "string", "description": "氢谱化学位移，逗号分隔"},
        "h_split_input": {"type": "string", "description": "氢谱峰裂分类型（与氢谱位移对应）"},
        "formula": {"type": "string", "description": "分子式约束（可选）"},
        "allowed_elements": {"type": "string", "description": "允许元素，逗号分隔（可选）"},
        "candidates": {"type": "string", "description": "候选分子 SMILES（可选）"},
    }, "required": []},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_reverse(ctx: ToolContext, args: dict) -> ToolResult:
    """反向核磁预测（化学位移 → 结构候选）。

    Args:
        ctx: 工具上下文。
        args: 含各可选位移/约束字段。

    Returns:
        ToolResult（候选结构 JSON）。
    """
    return await _call_nmrserver(ctx, "reverse", {
        "h_shifts_input": str(args.get("h_shifts_input", "")),
        "h_split_input": str(args.get("h_split_input", "")),
        "c_shifts_input": str(args.get("c_shifts_input", "")),
        "formula": str(args.get("formula", "")),
        "allowed_elements": str(args.get("allowed_elements", "")),
        "candidates": str(args.get("candidates", "")),
    })


@tool(
    name="spec.nmr.search",
    description=(
        "核磁数据库检索：给定实测化学位移，在谱图数据库中检索最接近的化合物"
        "（返回候选与匹配信息）。适合「已知谱峰，想找库里最像的分子」。"
    ),
    parameters={"type": "object", "properties": {
        "c_shifts_input": {"type": "string", "description": "碳谱化学位移，逗号分隔"},
        "h_shifts_input": {"type": "string", "description": "氢谱化学位移，逗号分隔"},
        "h_split_input": {"type": "string", "description": "氢谱峰裂分类型"},
        "num_search": {"type": "integer", "default": 500,
                       "description": "候选搜索数量（10-10000）"},
        "topk": {"type": "integer", "default": 10, "description": "返回条数（1-100）"},
        "allowed_elements": {"type": "string", "default": "C,H,N,O",
                             "description": "允许元素，逗号分隔"},
    }, "required": []},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_search(ctx: ToolContext, args: dict) -> ToolResult:
    """核磁数据库检索（化学位移 → 库内匹配）。

    Args:
        ctx: 工具上下文。
        args: 含位移输入与检索参数。

    Returns:
        ToolResult（库内候选 JSON）。
    """
    return await _call_nmrserver(ctx, "search", {
        "h_shifts_input": str(args.get("h_shifts_input", "")),
        "h_split_input": str(args.get("h_split_input", "")),
        "c_shifts_input": str(args.get("c_shifts_input", "")),
        "num_search": int(args.get("num_search", 500)),
        "topk": int(args.get("topk", 10)),
        "allowed_elements": str(args.get("allowed_elements", "C,H,N,O")),
    })
