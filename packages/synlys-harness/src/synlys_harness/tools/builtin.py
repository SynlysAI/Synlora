"""内置工具集：file.* / python.run / knowledge.* / web.search / web.fetch / http.request / skill.*。"""
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ..types import ToolContext, ToolResult
from .registry import ToolRegistry, tool
from .sandbox import DEFAULT_LOCAL_EXECUTOR


def _safe_path(root: Path, rel: str) -> Path | None:
    """解析相对路径并确保不逃逸出 root。

    Args:
        root: 工作区根目录。
        rel: 用户/LLM 给出的相对路径。

    Returns:
        绝对 Path；逃逸时返回 None。
    """
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None
    return target


def _no_workspace() -> ToolResult:
    """当前会话未挂载工作区的统一错误。"""
    return ToolResult(ok=False, content="当前会话未挂载用户工作区", error="no_workspace")


@tool(
    name="file.read",
    description=(
        "读取用户工作区中的文本文件（带行号、分段读取）。默认读前 1000 行；"
        "长文件按提示传 offset 继续读，不要假设一次看到了全文。"
    ),
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对工作区根的路径"},
        "offset": {"type": "integer", "default": 0, "description": "起始行号（0 基）"},
        "limit": {"type": "integer", "default": 1000, "description": "读取行数上限（≤2000）"},
    }, "required": ["path"]},
    timeout_s=10,
)
async def file_read(ctx: ToolContext, args: dict) -> ToolResult:
    """读取工作区文件（DSH/pi 式：行号 + offset/limit 分页，截断必有明确提示）。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    path = _safe_path(ctx.workspace_root, args["path"])
    if path is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not path.is_file():
        return ToolResult(ok=False, content=f"文件不存在: {args['path']}", error="not_found")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(ok=False, content=f"读取失败: {exc}", error="io_error")
    lines = text.splitlines()
    total = len(lines)
    offset = max(0, min(int(args.get("offset", 0)), max(0, total - 1)))
    limit = max(1, min(int(args.get("limit", 1000)), 2000))
    # 单次字符预算（低于管线 64KB 截断，保证提示永远先于静默截断出现）
    char_budget = 48000
    shown: list[str] = []
    used = 0
    end = offset
    for i in range(offset, min(offset + limit, total)):
        line = f"{i + 1:>6}│ {lines[i]}"
        if used + len(line) > char_budget and shown:
            break
        shown.append(line)
        used += len(line) + 1
        end = i + 1
    parts = ["\n".join(shown)]
    notes = []
    if offset > 0:
        notes.append(f"从第 {offset + 1} 行开始")
    if end < total:
        notes.append(f"共 {total} 行，本次读第 {offset + 1}–{end} 行，未读完——继续读传 offset={end}")
    if notes:
        parts.append("（" + "；".join(notes) + "）")
    return ToolResult(
        ok=True,
        content="\n".join(parts),
        data={"path": args["path"], "size": len(text), "total_lines": total,
              "offset": offset, "end": end, "truncated": end < total},
    )


@tool(
    name="file.write",
    description="把文本内容写入用户工作区文件（自动创建父目录）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, "required": ["path", "content"]},
    timeout_s=10,
)
async def file_write(ctx: ToolContext, args: dict) -> ToolResult:
    """写入工作区文件。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    path = _safe_path(ctx.workspace_root, args["path"])
    if path is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    return ToolResult(ok=True, content=f"已写入 {args['path']}（{len(args['content'])} 字符）")


@tool(
    name="file.list",
    description="列出用户工作区指定目录下的文件（递归相对路径，默认前 500 项）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对路径，默认 '.'"},
        "max_entries": {"type": "integer", "default": 500, "description": "返回条数上限（≤2000）"},
    }},
    timeout_s=10,
)
async def file_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出工作区文件树（超量截断必有提示）。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    base = _safe_path(ctx.workspace_root, args.get("path", "."))
    if base is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not base.exists():
        return ToolResult(ok=False, content=f"目录不存在: {args.get('path')}", error="not_found")
    max_entries = max(1, min(int(args.get("max_entries", 500)), 2000))
    files = [
        p.relative_to(ctx.workspace_root.resolve()).as_posix()
        for p in base.rglob("*") if p.is_file()
    ]
    total = len(files)
    shown = files[:max_entries]
    content = "\n".join(shown) or "(空目录)"
    if total > max_entries:
        content += f"\n（共 {total} 个文件，仅列出前 {max_entries} 个；缩小 path 范围或调大 max_entries）"
    return ToolResult(ok=True, content=content,
                      data={"files": shown, "total": total, "truncated": total > max_entries})


@tool(
    name="python.run",
    description=(
        "在用户沙箱中执行 Python 代码（隔离模式，可读写沙箱文件，输出受限）。"
        "工作目录为 tmp/ 子目录，工作区根是其父目录（file.write 写入的文件在根目录，"
        "需用相对路径 ../文件名 访问）；隔离模式下当前目录不在模块搜索路径，"
        "import 本地模块需先 sys.path.insert(0, os.getcwd())。适合数据分析与绘图。"
    ),
    parameters={"type": "object", "properties": {
        "code": {"type": "string", "description": "要执行的 Python 源码"},
    }, "required": ["code"]},
    timeout_s=70,  # 外层管线兜底须晚于沙箱内部 60s，保证内部先走到 kill+收尸路径
)
async def python_run(ctx: ToolContext, args: dict) -> ToolResult:
    """在用户沙箱 tmp/ 下执行代码（执行器经 ctx.extra 注入，缺省本机）。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    cwd = ctx.workspace_root / "tmp"
    cwd.mkdir(parents=True, exist_ok=True)
    executor = ctx.extra.get("code_executor") or DEFAULT_LOCAL_EXECUTOR
    result = await executor.run(args["code"], cwd=cwd, timeout_s=60)
    result.data["cwd"] = str(cwd)
    return result


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


# read_image 允许的图片类型（后缀 → MIME；模型侧通常支持这四种）
_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5MB


@tool(
    name="file.read_image",
    description=(
        "读取工作区中的图片（PNG/JPEG/WebP/GIF）并作为视觉输入查看（需模型支持多模态）。"
        "适合查看谱图、电镜照片、曲线截图等。图片仅本次分析可见，不会留存到对话历史。"
    ),
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对工作区根的图片路径"},
    }, "required": ["path"]},
    timeout_s=15,
)
async def file_read_image(ctx: ToolContext, args: dict) -> ToolResult:
    """读取工作区图片为 base64 附件（pi 式瞬态附件：经 data.images 上浮，不落事件）。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    path = _safe_path(ctx.workspace_root, args["path"])
    if path is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not path.is_file():
        return ToolResult(ok=False, content=f"文件不存在: {args['path']}", error="not_found")
    mime = _IMAGE_TYPES.get(path.suffix.lower())
    if mime is None:
        return ToolResult(ok=False, content=f"不支持的图片类型: {path.suffix}（支持 {'/'.join(_IMAGE_TYPES)}）", error="unsupported_type")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return ToolResult(ok=False, content=f"读取失败: {exc}", error="io_error")
    if len(raw) > _IMAGE_MAX_BYTES:
        return ToolResult(ok=False, content=f"图片过大（{len(raw) // 1024}KB > 5MB 上限）", error="too_large")
    import base64

    return ToolResult(
        ok=True,
        content=f"（已附图片 {args['path']}，{len(raw) // 1024}KB，供本次视觉分析）",
        data={"path": args["path"], "images": [{"mime": mime, "base64": base64.b64encode(raw).decode("ascii")}]},
    )


@tool(
    name="ask_user",
    description=(
        "向用户提问并等待回答（对话流内出现问答卡片，用户勾选/填写后统一确认提交）。"
        "两种用法：1) 单问题：只传 query（可带 2-4 个 options）；"
        "2) 多问题：传 questions 数组（每题 question 必填，可带 header 短标题、"
        "options 2-4 个选项、multi_select 多选标记），用户逐题作答、一次提交。"
        "信息不足、方案有重大取舍或需要用户确认时使用；不要用它问能自己查到的问题；"
        "一次最多 4 题，问题相关的合并为一次提问，不要连续多次调用。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "要问用户的问题（一句话说清背景与选项含义）"},
        "options": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object", "properties": {
                    "label": {"type": "string", "description": "选项文案（1-5 个词）"},
                    "description": {"type": "string", "description": "选项含义补充说明"},
                }, "required": ["label"],
            },
            "description": "预置选项（2-4 个）；不传则用户自由输入",
        },
        "questions": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object", "properties": {
                    "question": {"type": "string", "description": "问题正文"},
                    "header": {"type": "string", "description": "短标题（卡片头部显示，可选）"},
                    "options": {
                        "type": "array", "maxItems": 4,
                        "items": {
                            "type": "object", "properties": {
                                "label": {"type": "string", "description": "选项文案（1-5 个词）"},
                                "description": {"type": "string", "description": "选项含义补充说明"},
                            }, "required": ["label"],
                        },
                        "description": "预置选项（2-4 个）；不传则该题自由输入",
                    },
                    "multi_select": {"type": "boolean", "description": "允许多选（默认单选）"},
                }, "required": ["question"],
            },
            "description": "多问题模式（与 query 二选一）：用户逐题作答、一次提交",
        },
    }, "required": []},
    timeout_s=600,  # 等人回答；管线超时后工具取消并报错，不打断后续
)
async def ask_user(ctx: ToolContext, args: dict) -> ToolResult:
    """向用户提问（宿主经 ctx.extra 注入 ask_user_handler：发 ask/user 事件并等回答）。"""
    handler = ctx.extra.get("ask_user_handler")
    if not callable(handler):
        return ToolResult(ok=False, content="当前运行环境不支持用户问询", error="no_handler")

    def _norm_options(raw: list) -> list[dict]:
        return [
            {"label": str(o.get("label", ""))[:40],
             **({"description": str(o.get("description", ""))[:120]} if o.get("description") else {})}
            for o in (raw or []) if isinstance(o, dict) and o.get("label")
        ][:4]

    payload: dict = {"tool_call_id": str(ctx.extra.get("tool_call_id", ""))}
    if args.get("questions"):
        questions = []
        for q in args["questions"][:4]:
            if not isinstance(q, dict) or not str(q.get("question", "")).strip():
                continue
            question = {
                "question": str(q["question"])[:500],
                "options": _norm_options(q.get("options")),
            }
            if q.get("header"):
                question["header"] = str(q["header"])[:30]
            if q.get("multi_select") and question["options"]:
                question["multi_select"] = True
            questions.append(question)
        if not questions:
            return ToolResult(ok=False, content="questions 内没有有效问题（question 必填）",
                              error="invalid_arguments")
        payload.update({"questions": questions, "query": ""})
    else:
        if not str(args.get("query", "")).strip():
            return ToolResult(ok=False, content="query 与 questions 至少提供其一",
                              error="invalid_arguments")
        payload.update({
            "query": args["query"],
            "options": _norm_options(args.get("options")),
        })
    answer = await handler(payload)
    return ToolResult(ok=True, content=f"用户回答：{answer}", data={"answer": answer})


@tool(
    name="file.send",
    description=(
        "把工作区文件作为产物交付给用户（对话内出现可下载的文件卡片）。"
        "生成报告/图表/数据表等最终产物后用它交付；中间过程文件不必发送。"
    ),
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对工作区根的文件路径"},
        "note": {"type": "string", "description": "一句话说明这个文件是什么（可选）"},
    }, "required": ["path"]},
    timeout_s=15,
)
async def file_send(ctx: ToolContext, args: dict) -> ToolResult:
    """交付文件给用户（宿主经 ctx.extra 注入 send_file_handler：登记 files 集合并发事件）。"""
    handler = ctx.extra.get("send_file_handler")
    if not callable(handler):
        return ToolResult(ok=False, content="当前运行环境不支持文件交付", error="no_handler")
    return await handler({
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "path": args["path"],
        "note": str(args.get("note", ""))[:200],
    })


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
    """SearXNG 联网搜索：GET /search?format=json，取即时答案 + 前N条结果。

    服务地址（与可选 API Key）由宿主经 ctx.extra 注入，未配置时明确报错。
    """
    endpoint = str(ctx.extra.get("web_search_endpoint") or "").rstrip("/")
    api_key = str(ctx.extra.get("web_search_api_key") or "")
    if not endpoint:
        return ToolResult(ok=False, content="联网搜索未配置（缺 SearXNG 服务地址）", error="search_unconfigured")
    max_results = max(1, min(int(args.get("max_results", 5)), 10))
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{endpoint}/search",
                headers=headers,
                params={
                    "q": args["query"],
                    "format": "json",
                    "language": args.get("language") or "auto",
                },
            )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"搜索请求失败: {exc}", error="http_error")
    if resp.status_code != 200:
        return ToolResult(ok=False, content=f"搜索服务返回 {resp.status_code}: {resp.text[:200]}", error="http_error")
    payload = resp.json() or {}
    results = (payload.get("results") or [])[:max_results]
    answers = [str(a) for a in (payload.get("answers") or []) if a]
    if not results and not answers:
        return ToolResult(ok=True, content="（无搜索结果，可换个说法再试）", data={"results": 0})
    lines = []
    if answers:
        lines.append("即时答案：" + " / ".join(answers))
    for i, r in enumerate(results, 1):
        snippet = (r.get("content") or "").strip()
        lines.append(f"【{i}】{r.get('title') or '无标题'}\nURL: {r.get('url', '')}"
                     + (f"\n摘要: {snippet}" if snippet else ""))
    return ToolResult(ok=True, content="\n\n".join(lines), data={"results": len(results)})


def _is_private_ip(ip: str) -> bool:
    """判断 IP 是否私网/环回/链路本地地址（SSRF 防护）。

    Args:
        ip: 点分十进制 IPv4 或 IPv6 字符串。

    Returns:
        True 表示不可抓取的内网地址。
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # 解析不了按内网处理（宁可误杀）
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def _assert_fetchable_url(url: str) -> str | None:
    """校验 URL 可安全抓取：http(s)、端口 80/443、主机解析到公网 IP。

    Args:
        url: 目标 URL。

    Returns:
        校验失败原因（None = 通过）。DNS 解析在 httpx 请求前单独做（TOCTOU
        窗口存在，属事故围栏级别而非安全边界级别，与 python.run 定位一致）。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https（收到 {parsed.scheme or '空'}）"
    if parsed.port not in (80, 443, None):
        return f"仅允许 80/443 端口（收到 {parsed.port}）"
    host = parsed.hostname or ""
    if not host:
        return "URL 缺少主机名"
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        return f"主机解析失败: {host} ({exc})"
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            return f"拒绝抓取内网地址: {host} ({ip})"
    return None


def _html_to_text(html: str) -> str:
    """HTML 转纯文本（标准库实现，去 script/style/标签，压缩空白）。"""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip_depth += 1

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self._skip_depth:
                self._skip_depth -= 1

        def handle_data(self, data):
            if not self._skip_depth and data.strip():
                self.parts.append(data.strip())

    parser = _Text()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))


# 抓取用完整浏览器 UA（jiuwen 同款）：简短自述 UA 容易被反爬直接拒
_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_META_CHARSET_RE = re.compile(br"""<meta[^>]+charset=["']?\s*([A-Za-z0-9._-]+)""", re.IGNORECASE)
_HEADER_CHARSET_RE = re.compile(r"charset=([^\s;]+)", re.IGNORECASE)


def _decode_body(raw: bytes, header_charset: str) -> str:
    """按候选链解码响应体（照抄 jiuwen：显式声明优先，老中文站 GBK 兜底）。"""
    declared = (header_charset or "").strip().lower()
    meta = _META_CHARSET_RE.search(raw[:4096])
    meta_declared = meta.group(1).decode("ascii", errors="ignore").lower() if meta else ""
    candidates: list[str] = []
    if declared and declared not in ("iso-8859-1", "latin-1"):
        candidates.append(declared)
    candidates.extend(["utf-8", meta_declared, "gb18030", "big5", "shift_jis", "cp1252", "iso-8859-1"])
    seen: set[str] = set()
    for enc in candidates:
        enc = (enc or "").strip().lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_title(text: str) -> str:
    """提取 <title> 文本（输出头展示用）。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120]


@tool(
    name="web.fetch",
    description=(
        "抓取网页正文（web.search 命中后深入阅读）。仅允许公网 http/https；"
        "HTML 自动转纯文本，超长截断。"
    ),
    parameters={"type": "object", "properties": {
        "url": {"type": "string", "description": "要抓取的网页地址（来自 web.search 结果）"},
        "max_chars": {"type": "integer", "default": 20000, "description": "正文长度上限（≤50000）"},
    }, "required": ["url"]},
    timeout_s=25,
)
async def web_fetch(ctx: ToolContext, args: dict) -> ToolResult:
    """网页正文抓取：SSRF 防护（私网/端口/协议校验 + 重定向逐跳复检）+ HTML 转文本。

    服务端无配置依赖（任意公网页面均可），防护规则见 _assert_fetchable_url。
    """
    max_chars = max(1000, min(int(args.get("max_chars", 20000)), 50000))
    url = str(args["url"])
    headers = {"User-Agent": _FETCH_UA}
    visited = 0
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            # 手动跟随重定向（≤3 跳）：每一跳都重新过 SSRF 校验
            while True:
                reason = _assert_fetchable_url(url)
                if reason is not None:
                    return ToolResult(ok=False, content=f"拒绝抓取: {reason}", error="url_denied")
                resp = await client.get(url, headers=headers)
                if resp.is_redirect:
                    visited += 1
                    if visited > 3:
                        return ToolResult(ok=False, content="重定向次数超限（>3）", error="too_many_redirects")
                    location = str(resp.headers.get("location", ""))
                    url = urljoin(url, location)
                    continue
                break
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"抓取失败: {exc}", error="http_error")
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text" not in ctype and ctype:
        return ToolResult(
            ok=False,
            content=f"不支持的内容类型: {ctype or '未知'}（仅支持 HTML/文本；PDF 等附件暂不支持）",
            error="unsupported_content_type",
        )
    raw = resp.content or b""
    charset_match = _HEADER_CHARSET_RE.search(resp.headers.get("content-type", ""))
    page = _decode_body(raw, charset_match.group(1).strip("\"'") if charset_match else "")
    title = _extract_title(page) if "html" in ctype else ""
    text = _html_to_text(page) if "html" in ctype else page
    truncated = len(text) > max_chars
    if not text.strip():
        return ToolResult(ok=True, content="（页面无可见文本，可能是纯脚本渲染页）", data={"url": url, "chars": 0})
    head = f"URL: {url}" + (f"\n标题: {title}" if title else "") + "\n\n"
    content = head + text[:max_chars] + ("\n…（正文超长已截断，可用 max_chars 调整）" if truncated else "")
    return ToolResult(ok=True, content=content, data={"url": url, "chars": len(text), "truncated": truncated})


@tool(
    name="http.request",
    description="发起受限 HTTP GET 请求（仅允许白名单域名，用于访问内部平台 API）",
    parameters={"type": "object", "properties": {
        "url": {"type": "string"},
    }, "required": ["url"]},
    timeout_s=30,
)
async def http_request(ctx: ToolContext, args: dict) -> ToolResult:
    """白名单内的 GET 请求。"""
    allowed_hosts: list[str] = ctx.extra.get("http_allowed_hosts", [])
    host = urlparse(args["url"]).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        return ToolResult(ok=False, content=f"域名不在白名单: {host}", error="host_denied")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            resp = await client.get(args["url"])
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"请求失败: {exc}", error="http_error")
    return ToolResult(
        ok=resp.status_code < 400,
        content=resp.text[:65_536],
        data={"status_code": resp.status_code},
        truncated=len(resp.text) > 65_536,
    )


@tool(
    name="skill.list",
    description="列出本会话可用技能（名称 + 用途）。不确定该用哪个技能时先调用它。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def skill_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出可用技能。

    Args:
        ctx: 工具上下文（技能从 ctx.extra 取）。
        args: 无参数。

    Returns:
        技能清单（名称 + 描述）；无技能时返回提示文本。
    """
    skills: dict[str, str] = ctx.extra.get("skills") or {}
    listing = ctx.extra.get("skill_meta") or {}
    if not skills:
        return ToolResult(ok=True, content="当前没有可用技能。")
    lines = [f"- {name}：{listing.get(name, '')}" for name in skills]
    return ToolResult(ok=True, content="可用技能：\n" + "\n".join(lines))


@tool(
    name="skill.read",
    description="读取某个技能的完整 SKILL.md 正文（含工作流与输出要求）。",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "技能名"}},
        "required": ["name"],
    },
)
async def skill_read(ctx: ToolContext, args: dict) -> ToolResult:
    """读取技能正文。

    Args:
        ctx: 工具上下文（技能正文从 ctx.extra 取）。
        args: 含 name（技能名）。

    Returns:
        技能正文；技能名缺失或不存在时 ok=False。
    """
    name = str(args.get("name", "")).strip()
    skills: dict[str, str] = ctx.extra.get("skills") or {}
    content = skills.get(name)
    if content is None:
        return ToolResult(ok=False, error=f"技能不存在：{name}")
    return ToolResult(ok=True, content=content, data={"name": name, "content": content})


def _job_handler(ctx: ToolContext):
    """取宿主注入的任务处理器（缺失返回 None）。"""
    handler = ctx.extra.get("job_handler")
    return handler if callable(handler) else None


def _no_jobs() -> ToolResult:
    """无任务处理器时的统一失败结果。"""
    return ToolResult(ok=False, content="当前运行环境不支持后台任务", error="no_handler")


@tool(
    name="job.submit",
    description=(
        "提交一个后台长任务（谱图解析、批量计算等耗时数分钟以上的作业）。"
        "提交后立即返回任务 ID，任务完成时系统会自动通知你继续处理。"
        "不要重复提交同一请求，也不要在提交后反复调用 job.status 轮询。"
    ),
    parameters={"type": "object", "properties": {
        "kind": {"type": "string", "description": "任务类型（见技能说明，如 spec.nmr.forward）"},
        "params": {"type": "object", "description": "任务参数（随任务类型而定）"},
        "label": {"type": "string", "description": "任务简述，用于向用户展示（可选）"},
    }, "required": ["kind", "params"]},
    timeout_s=30,  # 只覆盖"提交"这一次请求；任务本身在后台跑
)
async def job_submit(ctx: ToolContext, args: dict) -> ToolResult:
    """提交后台任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_jobs()
    kind = str(args.get("kind") or "").strip()
    if not kind:
        return ToolResult(ok=False, content="kind 不能为空", error="invalid_arguments")
    params = args.get("params")
    if not isinstance(params, dict):
        return ToolResult(ok=False, content="params 必须是 JSON 对象", error="invalid_arguments")
    return await handler({
        "action": "submit",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "kind": kind,
        "params": params,
        "label": str(args.get("label") or "")[:200],
    })


@tool(
    name="job.status",
    description=(
        "查询一个后台任务的当前状态与结果。仅在用户主动询问进度、"
        "或任务完成通知里缺少必要信息时使用——不要在提交后反复轮询。"
    ),
    parameters={"type": "object", "properties": {
        "job_id": {"type": "string", "description": "任务 ID（job.submit 返回的）"},
    }, "required": ["job_id"]},
    timeout_s=60,
)
async def job_status(ctx: ToolContext, args: dict) -> ToolResult:
    """查询单个任务状态（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_jobs()
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return ToolResult(ok=False, content="job_id 不能为空", error="invalid_arguments")
    return await handler({
        "action": "status",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "job_id": job_id,
    })


@tool(
    name="job.list",
    description="列出本会话提交过的后台任务（含状态与结果摘要），用于汇报整体进度。",
    parameters={"type": "object", "properties": {}, "required": []},
    timeout_s=30,
)
async def job_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出本会话的任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_jobs()
    return await handler({
        "action": "list",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
    })


@tool(
    name="job.cancel",
    description="取消一个尚未完成的后台任务（已结束的任务取消无效果）。",
    parameters={"type": "object", "properties": {
        "job_id": {"type": "string", "description": "任务 ID"},
    }, "required": ["job_id"]},
    timeout_s=60,
)
async def job_cancel(ctx: ToolContext, args: dict) -> ToolResult:
    """取消任务（宿主经 ctx.extra 注入 job_handler）。"""
    handler = _job_handler(ctx)
    if handler is None:
        return _no_jobs()
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return ToolResult(ok=False, content="job_id 不能为空", error="invalid_arguments")
    return await handler({
        "action": "cancel",
        "tool_call_id": str(ctx.extra.get("tool_call_id", "")),
        "job_id": job_id,
    })


def register_builtin_tools(registry: ToolRegistry) -> None:
    """把全部内置工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    for fn in (
        file_read, file_write, file_list, python_run, file_read_image,
        knowledge_list, knowledge_search, web_search, web_fetch, http_request,
        ask_user, file_send, skill_list, skill_read,
        job_submit, job_status, job_list, job_cancel,
    ):
        registry.register(fn)
