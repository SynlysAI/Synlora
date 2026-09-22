"""Sciverse 科学文献检索工具（插件包自带，宿主动态加载）。

配置由宿主按命名空间注入 ctx.extra["plugins"]["sciverse"]（见 plugin.json 的
config_schema），本模块不认识任何宿主全局配置——插件只依赖宿主给出的这个通用通道。
上游契约见 SpecLabOS `backend/app/services/sciverse_client.py`。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from synlys_harness import ToolContext, ToolResult, tool

PLUGIN_ID = "sciverse"
REQUEST_TIMEOUT_S = 120.0   # 读超时（智能检索/全文接口可能较慢）
CONNECT_TIMEOUT_S = 10.0    # 连接超时（宿主不可达时快速失败）
TOOL_TIMEOUT_S = 132.0      # 工具级超时必须大于 HTTP 超时：让 HTTP 层先报错
RENDER_MAX_CHARS = 40000    # 单次回给模型的 JSON 字符预算（留出管线 64KB 余地）
MAX_QUERY_CHARS = 4096      # 上游对 query 的长度上限
MAX_RESOURCE_BYTES = 50 * 1024 * 1024   # 附件下载上限，防止超大文件占用内存
FRESHNESS_LEVELS = ("NONE", "MILD", "STRONG")


def _config(ctx: ToolContext) -> dict:
    """取本插件在运行上下文中的配置（宿主按插件 id 命名空间注入）。

    Args:
        ctx: 工具上下文。

    Returns:
        插件配置 dict（未安装/未配置时为空 dict）。
    """
    return (ctx.extra.get("plugins") or {}).get(PLUGIN_ID) or {}


def _str_arg(args: dict, key: str, default: str = "") -> str:
    """取字符串参数（None/缺失一律回落默认值，避免 str(None) 变成 "None"）。

    Args:
        args: 工具参数。
        key: 参数名。
        default: 缺失或为 None 时的默认值。

    Returns:
        字符串参数值。
    """
    value = args.get(key)
    return default if value is None else str(value)


def _int_arg(args: dict, key: str, default: int, *, low: int, high: int) -> int:
    """取整数参数并钳制到 [low, high]（None/空白/非法值回落默认值）。

    Args:
        args: 工具参数。
        key: 参数名。
        default: 缺失或非法时的默认值。
        low: 下界（含）。
        high: 上界（含）。

    Returns:
        钳制后的整数。
    """
    raw = args.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _bool_arg(args: dict, key: str, default: bool = False) -> bool:
    """取布尔参数（支持 bool / 数字 / 常见英文真值串）。

    Args:
        args: 工具参数。
        key: 参数名。
        default: 缺失或无法解析时的默认值。

    Returns:
        布尔参数值。
    """
    value = args.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return default


def _list_arg(args: dict, key: str) -> tuple[list | None, bool]:
    """解析列表参数（支持 list 或 JSON 数组字符串）。

    Args:
        args: 工具参数。
        key: 参数名。

    Returns:
        (解析后的 list 或 None, 是否显式声明)。声明但解析失败时 value 为
        None 且 declared 为 True，由调用方按参数错误处理；空字符串视为未传。
    """
    raw = args.get(key)
    if raw is None:
        return None, False
    if isinstance(raw, list):
        return raw, True
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None, True
        if isinstance(parsed, list):
            return parsed, True
        return None, True
    return None, False


def _safe_path(root: Path, rel: str) -> Path | None:
    """解析相对路径并确保不逃逸出工作区根（与内置 file.* 同一口径）。

    Args:
        root: 工作区根目录。
        rel: 用户/LLM 给出的相对路径。

    Returns:
        resolve 后的绝对 Path；逃逸时返回 None。
    """
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None
    return target


def _invalid(message: str) -> ToolResult:
    """构造参数错误结果。

    Args:
        message: 错误说明。

    Returns:
        ok=False 的 ToolResult（error=invalid_arguments）。
    """
    return ToolResult(ok=False, content=message, error="invalid_arguments")


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端。

    读超时略大以覆盖智能检索/全文接口的处理耗时，连接超时短以便快速失败。

    Returns:
        配好超时的 httpx 异步客户端。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S))


async def _call(
    ctx: ToolContext,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> "httpx.Response | ToolResult":
    """调用 Sciverse 端点并归一化为 httpx 响应或错误结果。

    Args:
        ctx: 工具上下文（取插件配置）。
        method: HTTP 方法（GET/POST）。
        path: 端点路径（以 / 开头）。
        params: URL 查询参数。
        json_body: JSON 请求体。

    Returns:
        成功时为 httpx.Response；失败时返回 ok=False 的 ToolResult
        （error 为 sciverse_unconfigured / unauthorized / not_found /
        timeout / connection_error / http_error）。
    """
    config = _config(ctx)
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not base_url:
        return ToolResult(
            ok=False,
            content="Sciverse 插件未配置服务地址（请在管理后台「插件」页安装并填写）",
            error="sciverse_unconfigured",
        )
    api_token = str(config.get("api_token") or "")
    if not api_token:
        return ToolResult(
            ok=False,
            content="Sciverse 插件未配置访问凭证 api_token（请在管理后台「插件」页填写）",
            error="sciverse_unconfigured",
        )
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        async with _make_client() as client:
            resp = await client.request(
                method, f"{base_url}{path}", params=params, json=json_body,
                headers=headers)
    except httpx.TimeoutException as exc:
        return ToolResult(ok=False, content=f"Sciverse 服务响应超时: {exc}", error="timeout")
    except httpx.ConnectError as exc:
        return ToolResult(ok=False, content=f"无法连接 Sciverse 服务: {exc}", error="connection_error")
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"Sciverse 服务请求失败: {exc}", error="http_error")
    if resp.status_code in (401, 403):
        # 凭证问题单列：对用户/模型都不可操作，提示走管理后台更新，而不是当成参数错误重试
        return ToolResult(
            ok=False,
            content="Sciverse 访问凭证 api_token 无效或已过期，请联系管理员在管理后台「插件」页更新访问凭证",
            error="unauthorized",
        )
    if resp.status_code == 404:
        return ToolResult(
            ok=False,
            content=f"Sciverse 资源不存在: {path}（{params or ''}）",
            error="not_found",
        )
    if resp.status_code != 200:
        detail = resp.content[:300].decode("utf-8", errors="replace")
        return ToolResult(
            ok=False,
            content=f"Sciverse 服务返回 {resp.status_code}: {detail}",
            error="http_error",
        )
    return resp


def _render_body(body: Any) -> tuple[str, bool]:
    """把响应体渲染为给 LLM 的文本（超长时截断并明示）。

    Args:
        body: 上游 JSON 响应。

    Returns:
        (渲染文本, 是否被截断)。
    """
    text = json.dumps(body, ensure_ascii=False)
    total = len(text)
    if total <= RENDER_MAX_CHARS:
        return text, False
    return (
        text[:RENDER_MAX_CHARS]
        + f"\n（响应过长已截断：全文 {total} 字符，可用分页/裁剪参数减少返回量）",
        True,
    )


async def _call_json(
    ctx: ToolContext,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> ToolResult:
    """调用 Sciverse JSON 端点并渲染为工具结果。

    Args:
        ctx: 工具上下文。
        method: HTTP 方法。
        path: 端点路径。
        params: URL 查询参数。
        json_body: JSON 请求体。

    Returns:
        ToolResult：content 为 JSON 渲染文本；失败时透传 _call 的错误结果。
    """
    resp = await _call(ctx, method, path, params=params, json_body=json_body)
    if isinstance(resp, ToolResult):
        return resp
    try:
        body = resp.json()
    except ValueError:
        return ToolResult(
            ok=False,
            content=(
                "Sciverse 返回非 JSON: "
                f"{resp.content[:200].decode('utf-8', errors='replace')}"
            ),
            error="upstream_error",
        )
    text, truncated = _render_body(body)
    return ToolResult(
        ok=True,
        content=text,
        data={"chars": len(json.dumps(body, ensure_ascii=False)), "truncated": truncated},
    )


@tool(
    name="sciverse.agentic_search",
    description=(
        "Sciverse 智能文献检索：给定自然语言问题，检索并返回相关文献片段（hits）。"
        "适合开放式问题与研究综述；结果中的 doc_id 可用 sciverse.content 按分段读全文，"
        "附件可用 sciverse.resource 下载。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "自然语言检索问题，最长 4096 字符"},
        "top_k": {"type": "integer", "default": 10, "description": "返回片段数量（1-100，默认 10）"},
        "sub_queries": {"type": "integer", "default": 0,
                        "description": "查询改写数量（0-4，默认 0）"},
    }, "required": ["query"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def sciverse_agentic_search(ctx: ToolContext, args: dict) -> ToolResult:
    """智能检索文献片段（自然语言 → hits）。"""
    query = _str_arg(args, "query")
    if not query:
        return _invalid("query 不能为空")
    payload = {
        "query": query[:MAX_QUERY_CHARS],
        "top_k": _int_arg(args, "top_k", 10, low=1, high=100),
        "sub_queries": _int_arg(args, "sub_queries", 0, low=0, high=4),
    }
    return await _call_json(ctx, "POST", "/agentic-search", json_body=payload)


@tool(
    name="sciverse.content",
    description=(
        "按 doc_id 分段读取 Sciverse 文献全文。不传 offset 返回全文；"
        "长文档传 offset 继续读取，next_offset/more 字段用于判断是否还有后续段落。"
    ),
    parameters={"type": "object", "properties": {
        "doc_id": {"type": "string", "description": "文献 ID（agentic-search 或 meta-search 返回）"},
        "offset": {"type": "integer", "description": "字符偏移；不传返回全文"},
        "limit": {"type": "integer", "default": 700,
                  "description": "单次最大字符数（1-50000，默认 700，仅在传 offset 时生效）"},
    }, "required": ["doc_id"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def sciverse_content(ctx: ToolContext, args: dict) -> ToolResult:
    """分段读取文献全文（doc_id → text/next_offset/more）。"""
    doc_id = _str_arg(args, "doc_id")
    if not doc_id:
        return _invalid("doc_id 不能为空")
    params = {"doc_id": doc_id}
    raw_offset = args.get("offset")
    if raw_offset not in (None, ""):
        params["offset"] = _int_arg(args, "offset", 0, low=0, high=9_999_999_999)
        params["limit"] = _int_arg(args, "limit", 700, low=1, high=50_000)
    return await _call_json(ctx, "GET", "/content", params=params)


@tool(
    name="sciverse.resource",
    description=(
        "下载 Sciverse 文献附件（按 file_name 相对路径，常见 PDF）。附件为二进制，"
        "本工具不直接返回文件内容；传 save_relative_path 会保存到用户工作区，"
        "随后可用 file.send 交付给用户，或用 file.list/file.read 检查。"
    ),
    parameters={"type": "object", "properties": {
        "file_name": {"type": "string",
                      "description": "资源相对路径（不得包含反斜杠、不能以 / 开头、不含 ..）"},
        "save_relative_path": {"type": "string",
                               "description": "保存到工作区的相对路径（可选，扩展名应匹配实际格式）"},
    }, "required": ["file_name"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def sciverse_resource(ctx: ToolContext, args: dict) -> ToolResult:
    """下载文献附件（二进制仅回传元信息，可选落盘到工作区）。"""
    file_name = _str_arg(args, "file_name")
    if not file_name:
        return _invalid("file_name 不能为空")
    if "\\" in file_name or file_name.startswith("/") or ".." in file_name.split("/"):
        return _invalid("file_name 不能包含反斜杠、不能以 / 开头、不能包含 '..'")
    resp = await _call(ctx, "GET", "/resource", params={"file_name": file_name})
    if isinstance(resp, ToolResult):
        return resp
    data = resp.content
    content_type = resp.headers.get("content-type") or ""
    if len(data) > MAX_RESOURCE_BYTES:
        return ToolResult(
            ok=False,
            content=f"附件超过 {MAX_RESOURCE_BYTES // (1024 * 1024)}MB 上限",
            error="too_large",
            data={"size": len(data), "content_type": content_type},
        )
    save_relative_path = _str_arg(args, "save_relative_path")
    if not save_relative_path:
        return ToolResult(
            ok=True,
            content=(
                f"已获取二进制附件 {file_name}（{len(data)} bytes，{content_type}）；"
                "如需交付用户，传 save_relative_path 保存到工作区"
            ),
            data={"size": len(data), "content_type": content_type},
        )
    if ctx.workspace_root is None:
        return ToolResult(
            ok=False,
            content="当前会话未挂载用户工作区，无法保存附件",
            error="no_workspace",
        )
    target = _safe_path(ctx.workspace_root, save_relative_path)
    if target is None:
        return _invalid("save_relative_path 越界")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return ToolResult(ok=False, content=f"附件写盘失败: {exc}", error="io_error")
    return ToolResult(
        ok=True,
        content=f"已保存附件到 {save_relative_path}（{len(data)} bytes，{content_type}）",
        data={"size": len(data), "content_type": content_type, "path": save_relative_path},
    )


@tool(
    name="sciverse.meta_catalog",
    description=(
        "查看 Sciverse meta-search 支持的字段目录（字段名/类型/是否可过滤排序），"
        "用于构造结构化检索；include_sample_values 返回枚举字段样本值（缓存 24 小时）。"
    ),
    parameters={"type": "object", "properties": {
        "include_sample_values": {"type": "boolean", "default": False,
                                  "description": "是否返回枚举字段样本值"},
    }},
    timeout_s=TOOL_TIMEOUT_S,
)
async def sciverse_meta_catalog(ctx: ToolContext, args: dict) -> ToolResult:
    """查看 meta 结构化检索字段目录。"""
    return await _call_json(
        ctx, "GET", "/meta-catalog",
        params={"include_sample_values": _bool_arg(args, "include_sample_values", False)},
    )


@tool(
    name="sciverse.meta_search",
    description=(
        "按结构化条件检索 Sciverse 文献元数据：query 全文模糊检索（与 sort 互斥），"
        "filters/sort/fields 字段名取自 meta_catalog；page 分页与 cursor 翻页二选一"
        "（cursor 优先，二者不可同时用）。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "全文模糊检索词（与 sort 互斥）"},
        "filters": {"type": "array", "items": {"type": "object"},
                    "description": "字段过滤条件列表，每项 {field, operator?, value}"},
        "sort": {"type": "array", "items": {"type": "object"},
                 "description": "排序字段列表，每项 {field, order}（与 query 互斥）"},
        "fields": {"type": "array", "items": {"type": "string"},
                   "description": "字段投影列表（只返回这些字段）"},
        "page": {"type": "integer", "default": 1, "description": "页码（≥1，默认 1）"},
        "page_size": {"type": "integer", "default": 25,
                      "description": "每页条数（1-200，默认 25）"},
        "cursor": {"type": "string", "description": "游标翻页令牌（不与 page>1 同用）"},
        "freshness_boost": {"type": "string", "enum": ["NONE", "MILD", "STRONG"],
                            "description": "新鲜度加权：NONE/MILD/STRONG"},
    }, "required": []},
    timeout_s=TOOL_TIMEOUT_S,
)
async def sciverse_meta_search(ctx: ToolContext, args: dict) -> ToolResult:
    """按结构化条件检索文献元数据。"""
    query = _str_arg(args, "query")
    filters, filters_decl = _list_arg(args, "filters")
    sort, sort_decl = _list_arg(args, "sort")
    fields, fields_decl = _list_arg(args, "fields")
    if filters_decl and filters is None:
        return _invalid("filters 参数格式非法（应为 JSON 数组）")
    if sort_decl and sort is None:
        return _invalid("sort 参数格式非法（应为 JSON 数组）")
    if fields_decl and fields is None:
        return _invalid("fields 参数格式非法（应为 JSON 数组）")
    if query and sort:
        return _invalid("query 与 sort 互斥，请只传其中一个")
    if filters is not None and any(
        not isinstance(item, dict) or not str(item.get("field") or "").strip()
        for item in filters
    ):
        return _invalid("filters 每项必须为对象 {field, operator?, value} 且 field 不能为空")
    if sort is not None and any(
        not isinstance(item, dict) or not str(item.get("field") or "").strip()
        for item in sort
    ):
        return _invalid("sort 每项必须为对象 {field, order} 且 field 不能为空")
    if fields is not None and any(not isinstance(item, str) for item in fields):
        return _invalid("fields 每项必须是字符串")
    freshness_boost = _str_arg(args, "freshness_boost")
    if freshness_boost and freshness_boost.upper() not in FRESHNESS_LEVELS:
        return _invalid(f"freshness_boost 只能是 {'/'.join(FRESHNESS_LEVELS)}")
    cursor = _str_arg(args, "cursor")
    page = _int_arg(args, "page", 1, low=1, high=1_000_000)
    if cursor and page > 1:
        return _invalid("cursor 翻页与 page>1 互斥，请只使用其中一种")
    body: dict[str, Any] = {}
    if query:
        body["query"] = query
    if filters is not None:
        body["filters"] = filters
    if sort is not None:
        body["sort"] = sort
    if fields is not None:
        body["fields"] = fields
    if cursor:
        body["cursor"] = cursor
    else:
        body["page"] = page
        body["page_size"] = _int_arg(args, "page_size", 25, low=1, high=200)
    if freshness_boost:
        body["freshness_boost"] = freshness_boost.upper()
    return await _call_json(ctx, "POST", "/meta-search", json_body=body)
