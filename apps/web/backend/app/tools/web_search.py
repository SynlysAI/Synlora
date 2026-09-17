"""宿主 SearXNG 联网搜索工具。"""
from __future__ import annotations

import httpx

from synlys_harness import ToolContext, ToolResult, tool


@tool(
    name="web.search",
    description=(
        "联网搜索（SearXNG 元搜索，返回标题/链接/摘要，含即时答案）。"
        "需要查最新资料、文献线索、事实核查或工作区/知识库之外的公开信息时使用。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索关键词（英文关键词对英文资料效果更好）"},
        "max_results": {"type": "integer", "default": 5, "description": "返回结果数上限（≤10）"},
        "language": {"type": "string", "default": "auto", "description": "结果语言（如 zh-CN/en/auto）"},
    }, "required": ["query"]},
    timeout_s=20,
)
async def web_search(ctx: ToolContext, args: dict) -> ToolResult:
    """通过宿主配置的 SearXNG 服务搜索公开信息。"""
    endpoint = str(ctx.extra.get("web_search_endpoint") or "").rstrip("/")
    api_key = str(ctx.extra.get("web_search_api_key") or "")
    if not endpoint:
        return ToolResult(
            ok=False,
            content="联网搜索未配置（缺 SearXNG 服务地址）",
            error="search_unconfigured",
        )
    max_results = max(1, min(int(args.get("max_results", 5)), 10))
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{endpoint}/search",
                headers=headers,
                params={
                    "q": args["query"],
                    "format": "json",
                    "language": args.get("language") or "auto",
                },
            )
    except httpx.HTTPError as exc:
        return ToolResult(
            ok=False,
            content=f"搜索请求失败: {exc}",
            error="http_error",
        )
    if response.status_code != 200:
        return ToolResult(
            ok=False,
            content=f"搜索服务返回 {response.status_code}: {response.text[:200]}",
            error="http_error",
        )
    payload = response.json() or {}
    results = (payload.get("results") or [])[:max_results]
    answers = [str(answer) for answer in (payload.get("answers") or []) if answer]
    if not results and not answers:
        return ToolResult(
            ok=True,
            content="（无搜索结果，可换个说法再试）",
            data={"results": 0},
        )
    lines = []
    if answers:
        lines.append("即时答案：" + " / ".join(answers))
    for index, item in enumerate(results, 1):
        snippet = (item.get("content") or "").strip()
        lines.append(
            f"【{index}】{item.get('title') or '无标题'}\n"
            f"URL: {item.get('url', '')}"
            + (f"\n摘要: {snippet}" if snippet else "")
        )
    return ToolResult(
        ok=True,
        content="\n\n".join(lines),
        data={"results": len(results)},
    )
