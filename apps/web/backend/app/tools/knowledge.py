"""WeKnora 知识检索工具（宿主侧业务工具，非 harness 内置）。

连接配置由宿主经 ctx.extra 注入（weknora_base_url / weknora_api_key），
助手绑定的知识库允许范围经 ctx.extra.knowledge_base_ids 注入。
"""
from __future__ import annotations

import httpx

from synlys_harness import ToolContext, ToolResult, tool


def _kb_scope(ctx: ToolContext) -> list[str]:
    """助手绑定的知识库允许范围（宿主按助手配置注入；空 = 不限，可检索平台全部）。

    Args:
        ctx: 工具上下文。

    Returns:
        允许的知识库 id 列表（空列表表示无限制）。
    """
    return [str(k) for k in (ctx.extra.get("knowledge_base_ids") or []) if k]


@tool(
    name="knowledge.list",
    description=(
        "列出当前可用的知识库（id、名称、描述、文档数）。检索前用它确定要查哪些库；"
        "助手限定范围时仅显示范围内知识库。"
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    timeout_s=15,
)
async def knowledge_list(ctx: ToolContext, args: dict) -> ToolResult:
    """WeKnora 知识库列表：GET /knowledge-bases（助手绑定范围时过滤显示）。

    文档数用列表自带的 knowledge_count（chunk_count 在 WeKnora v0.7.1 恒为
    0，未维护）。连接配置由宿主经 ctx.extra 注入。
    """
    base_url = str(ctx.extra.get("weknora_base_url") or "").rstrip("/")
    api_key = str(ctx.extra.get("weknora_api_key") or "")
    headers = {"X-API-Key": api_key}
    if not base_url or not api_key:
        return ToolResult(ok=False, content="知识库服务未配置（缺 WeKnora 连接信息）", error="weknora_unconfigured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/knowledge-bases", headers=headers)
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"知识库请求失败: {exc}", error="http_error")
    if resp.status_code != 200:
        return ToolResult(ok=False, content=f"知识库服务返回 {resp.status_code}: {resp.text[:200]}", error="http_error")
    kbs = (resp.json() or {}).get("data") or []
    allowed = _kb_scope(ctx)
    if allowed:
        kbs = [kb for kb in kbs if kb.get("id") in allowed]
    if not kbs:
        content = "（助手绑定的知识库均不可用，请检查绑定配置）" if allowed else "（平台暂无知识库）"
        return ToolResult(ok=True, content=content, data={"count": 0, "restricted": bool(allowed)})
    lines = []
    for kb in kbs:
        count = kb.get("knowledge_count")
        count_text = f"{count} 篇文档" if isinstance(count, int) else "文档数未知"
        lines.append(
            f"- {kb.get('name', '未命名')}（id: {kb.get('id', '')}，{count_text}）"
            + (f"：{kb.get('description', '')}" if kb.get("description") else "")
        )
    prefix = "（助手限定范围，仅以下知识库可用）\n" if allowed else ""
    return ToolResult(ok=True, content=prefix + "\n".join(lines),
                      data={"count": len(kbs), "restricted": bool(allowed)})


@tool(
    name="knowledge.search",
    description=(
        "在科研知识库中检索相关资料（混合检索：语义+关键词，返回最相关原文片段与来源文件）。"
        "知识库范围：不传 knowledge_base_ids 时检索助手绑定的库；助手限定范围内只能检索"
        "这些库。不确定有哪些库时先调 knowledge.list。查找文献/资料/事实依据时优先用本工具。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "检索问题或关键词"},
        "knowledge_base_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "要检索的知识库 id 列表（来自 knowledge.list；不传检索助手绑定的库）",
        },
        "top_k": {"type": "integer", "default": 5, "description": "返回片段数上限（≤10）"},
    }, "required": ["query"]},
    timeout_s=30,
)
async def knowledge_search(ctx: ToolContext, args: dict) -> ToolResult:
    """WeKnora 知识检索：POST /knowledge-bases/{id}/hybrid-search（v0.7.1 推荐的混合检索）。

    范围控制（硬边界，模型绕不过）：宿主注入的 ctx.extra.knowledge_base_ids
    是**允许范围**——非空时，参数指定的库会被取交集，交集为空直接拒绝；
    未指定参数时检索整个允许范围。允许范围为空 = 平台全部知识库可用。
    """
    base_url = str(ctx.extra.get("weknora_base_url") or "").rstrip("/")
    api_key = str(ctx.extra.get("weknora_api_key") or "")
    if not base_url or not api_key:
        return ToolResult(ok=False, content="知识库服务未配置（缺 WeKnora 连接信息）", error="weknora_unconfigured")
    allowed = _kb_scope(ctx)
    kb_ids = [str(k) for k in (args.get("knowledge_base_ids") or []) if k]
    if allowed:
        requested = kb_ids or allowed
        kb_ids = [k for k in requested if k in allowed]
        if not kb_ids:
            denied = [k for k in requested if k not in allowed]
            return ToolResult(
                ok=False,
                content=f"知识库不在当前助手的允许范围内: {', '.join(denied)}；"
                        f"可用范围见 knowledge.list 或不传 knowledge_base_ids 直接检索",
                error="kb_not_allowed",
                data={"allowed": allowed},
            )
    elif not kb_ids:
        return ToolResult(
            ok=False,
            content="未指定知识库；请先调用 knowledge.list 查看可用知识库，再把 id 传入 knowledge_base_ids",
            error="no_knowledge_base",
        )
    top_k = max(1, min(int(args.get("top_k", 5)), 10))
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # hybrid-search（v0.7.1 推荐）：向量+关键词混合检索，服务端限量
            # （match_count）；跨库检索靠 body 的 knowledge_base_ids（优先级
            # 高于路径 :id，路径取首个库仅为满足路由）
            resp = await client.post(
                f"{base_url}/knowledge-bases/{kb_ids[0]}/hybrid-search",
                headers={"X-API-Key": api_key},
                json={
                    "query_text": args["query"],
                    "match_count": top_k,
                    "knowledge_base_ids": kb_ids,
                },
            )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"知识库请求失败: {exc}", error="http_error")
    if resp.status_code != 200:
        return ToolResult(ok=False, content=f"知识库服务返回 {resp.status_code}: {resp.text[:200]}", error="http_error")
    hits = (resp.json() or {}).get("data") or []
    picked = hits[:top_k]
    if not picked:
        return ToolResult(ok=True, content="（未检索到相关内容，可换个说法再试）", data={"hits": 0})
    lines = []
    for i, h in enumerate(picked, 1):
        title = h.get("knowledge_title") or h.get("knowledge_filename") or "未命名"
        fname = h.get("knowledge_filename") or ""
        score = h.get("score")
        suffix = f"（{fname}，相关度 {float(score):.2f}）" if fname and isinstance(score, (int, float)) else (f"（{fname}）" if fname else "")
        lines.append(f"【{i}】{title}{suffix}\n{h.get('content', '')}".rstrip())
    return ToolResult(
        ok=True,
        content="\n\n".join(lines),
        data={"hits": len(picked), "total": len(hits)},
    )
