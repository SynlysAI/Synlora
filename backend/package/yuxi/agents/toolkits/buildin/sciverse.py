"""Sciverse 科学文献检索内置工具。

来源：Synlora 插件 catalog/plugins/sciverse/tools.py 平移。
差异：配置改读环境变量（SCIVERSE_BASE_URL / SCIVERSE_API_TOKEN，静态 Bearer）；
附件落盘走 _workspace_io（沙盒写回共享 Workdir）；ToolResult 改 dict 返回。
上游契约见 SpecLabOS `backend/app/services/sciverse_client.py`。
"""

import json
import os
from typing import Any, Literal

import httpx
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.buildin._workspace_io import upload_workspace_file
from yuxi.agents.toolkits.registry import tool

REQUEST_TIMEOUT_S = 120.0   # 读超时（智能检索/全文接口可能较慢）
CONNECT_TIMEOUT_S = 10.0    # 连接超时（宿主不可达时快速失败）
RENDER_MAX_CHARS = 40000    # 单次回给模型的 JSON 字符预算
MAX_QUERY_CHARS = 4096      # 上游对 query 的长度上限
MAX_RESOURCE_BYTES = 50 * 1024 * 1024   # 附件下载上限，防止超大文件占用内存
FRESHNESS_LEVELS = ("NONE", "MILD", "STRONG")

# 测试注入 MockTransport 的钩子；None 时 httpx 使用默认传输
_transport: httpx.BaseTransport | None = None


def _settings() -> dict[str, str]:
    """读取 Sciverse 环境变量配置。"""
    return {
        "base_url": (os.getenv("SCIVERSE_BASE_URL") or "").strip().rstrip("/"),
        "api_token": (os.getenv("SCIVERSE_API_TOKEN") or "").strip(),
    }


def _error(error: str, message: str, **extra) -> dict:
    """构造上游类错误的结构化返回（不抛异常，供模型阅读后自行决策）。"""
    result = {"ok": False, "error": error, "message": message}
    result.update(extra)
    return result


def _invalid(message: str) -> dict:
    """构造参数错误返回。

    Args:
        message: 错误说明。

    Returns:
        ok=False 的错误 dict（error=invalid_arguments）。
    """
    return _error("invalid_arguments", message)


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端（读超时覆盖智能检索耗时，连接超时短以便快速失败）。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        transport=_transport,
    )


async def _call(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> "httpx.Response | dict":
    """调用 Sciverse 端点并归一化为 httpx 响应或错误 dict。

    Args:
        method: HTTP 方法（GET/POST）。
        path: 端点路径（以 / 开头）。
        params: URL 查询参数。
        json_body: JSON 请求体。

    Returns:
        成功时为 httpx.Response；失败时返回错误 dict
        （error 为 sciverse_unconfigured / unauthorized / not_found /
        timeout / connection_error / http_error）。
    """
    settings = _settings()
    if not settings["base_url"]:
        return _error("sciverse_unconfigured",
                      "Sciverse 未配置服务地址：请在部署配置中设置环境变量 SCIVERSE_BASE_URL")
    if not settings["api_token"]:
        return _error("sciverse_unconfigured",
                      "Sciverse 未配置访问凭证：请在部署配置中设置环境变量 SCIVERSE_API_TOKEN")
    headers = {"Authorization": f"Bearer {settings['api_token']}"}
    try:
        async with _make_client() as client:
            resp = await client.request(
                method, f"{settings['base_url']}{path}", params=params, json=json_body,
                headers=headers)
    except httpx.TimeoutException as exc:
        return _error("timeout", f"Sciverse 服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接 Sciverse 服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"Sciverse 服务请求失败: {exc}")
    if resp.status_code in (401, 403):
        # 凭证问题单列：对模型不可操作，提示走部署配置更新，而不是当成参数错误重试
        return _error("unauthorized",
                      "Sciverse 访问凭证无效或已过期：请核对部署配置中的 SCIVERSE_API_TOKEN")
    if resp.status_code == 404:
        return _error("not_found", f"Sciverse 资源不存在: {path}（{params or ''}）")
    if resp.status_code != 200:
        detail = resp.content[:300].decode("utf-8", errors="replace")
        return _error("http_error", f"Sciverse 服务返回 {resp.status_code}: {detail}")
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
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict:
    """调用 Sciverse JSON 端点并渲染为工具返回。

    Args:
        method: HTTP 方法。
        path: 端点路径。
        params: URL 查询参数。
        json_body: JSON 请求体。

    Returns:
        成功为 {"ok": True, "content": JSON 渲染文本, "chars", "truncated"}；
        失败透传 _call 的错误 dict。
    """
    resp = await _call(method, path, params=params, json_body=json_body)
    if isinstance(resp, dict):
        return resp
    try:
        body = resp.json()
    except ValueError:
        return _error(
            "upstream_error",
            "Sciverse 返回非 JSON: "
            + resp.content[:200].decode("utf-8", errors="replace"),
        )
    text, truncated = _render_body(body)
    return {
        "ok": True,
        "content": text,
        "chars": len(json.dumps(body, ensure_ascii=False)),
        "truncated": truncated,
    }


def _list_arg(value) -> tuple[list | None, bool]:
    """解析列表参数（支持 list 或 JSON 数组字符串）。

    Args:
        value: 原始参数值。

    Returns:
        (解析后的 list 或 None, 是否显式声明)。声明但解析失败时 value 为
        None 且 declared 为 True，由调用方按参数错误处理；空字符串视为未传。
    """
    if value is None:
        return None, False
    if isinstance(value, list):
        return value, True
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None, True
        if isinstance(parsed, list):
            return parsed, True
        return None, True
    return None, False


class SciverseAgenticSearchInput(BaseModel):
    query: str = Field(description="自然语言检索问题，最长 4096 字符")
    top_k: int = Field(default=10, ge=1, le=100, description="返回片段数量（1-100，默认 10）")
    sub_queries: int = Field(default=0, ge=0, le=4, description="查询改写数量（0-4，默认 0）")


class SciverseContentInput(BaseModel):
    doc_id: str = Field(description="文献 ID（agentic-search 或 meta-search 返回）")
    offset: int | None = Field(default=None, ge=0, description="字符偏移；不传返回全文")
    limit: int = Field(default=700, ge=1, le=50000,
                       description="单次最大字符数（1-50000，默认 700，仅在传 offset 时生效）")


class SciverseResourceInput(BaseModel):
    file_name: str = Field(
        description="资源相对路径（不得包含反斜杠、不能以 / 开头、不含 ..）")
    save_relative_path: str | None = Field(
        default=None, description="保存到工作区的相对路径（可选，扩展名应匹配实际格式）")


class SciverseMetaCatalogInput(BaseModel):
    include_sample_values: bool = Field(default=False, description="是否返回枚举字段样本值")


class SciverseMetaSearchInput(BaseModel):
    query: str | None = Field(default=None, description="全文模糊检索词（与 sort 互斥）")
    filters: list[dict] | str | None = Field(
        default=None, description="字段过滤条件列表，每项 {field, operator?, value}（可为 JSON 数组字符串）")
    sort: list[dict] | str | None = Field(
        default=None, description="排序字段列表，每项 {field, order}（与 query 互斥）")
    fields: list[str] | str | None = Field(
        default=None, description="字段投影列表（只返回这些字段）")
    page: int = Field(default=1, ge=1, description="页码（≥1，默认 1）")
    page_size: int = Field(default=25, ge=1, le=200, description="每页条数（1-200，默认 25）")
    cursor: str | None = Field(default=None, description="游标翻页令牌（不与 page>1 同用）")
    freshness_boost: Literal["NONE", "MILD", "STRONG"] | str | None = Field(
        default=None, description="新鲜度加权：NONE/MILD/STRONG")


@tool(
    category="sciverse",
    tags=["文献", "检索"],
    display_name="Sciverse 智能检索",
    description=(
        "Sciverse 智能文献检索：给定自然语言问题，检索并返回相关文献片段（hits）。"
        "适合开放式问题与研究综述；结果中的 doc_id 可用 sciverse_content 按分段读全文，"
        "附件可用 sciverse_resource 下载。"
    ),
    args_schema=SciverseAgenticSearchInput,
)
async def sciverse_agentic_search(query: str, top_k: int = 10, sub_queries: int = 0) -> dict:
    """智能检索文献片段（自然语言 → hits）。"""
    if not query.strip():
        return _invalid("query 不能为空")
    payload = {
        "query": query[:MAX_QUERY_CHARS],
        "top_k": top_k,
        "sub_queries": sub_queries,
    }
    return await _call_json("POST", "/agentic-search", json_body=payload)


@tool(
    category="sciverse",
    tags=["文献", "全文"],
    display_name="Sciverse 读文献全文",
    description=(
        "按 doc_id 分段读取 Sciverse 文献全文。不传 offset 返回全文；"
        "长文档传 offset 继续读取，next_offset/more 字段用于判断是否还有后续段落。"
    ),
    args_schema=SciverseContentInput,
)
async def sciverse_content(doc_id: str, offset: int | None = None, limit: int = 700) -> dict:
    """分段读取文献全文（doc_id → text/next_offset/more）。"""
    if not doc_id.strip():
        return _invalid("doc_id 不能为空")
    params: dict = {"doc_id": doc_id}
    if offset is not None:
        params["offset"] = offset
        params["limit"] = limit
    return await _call_json("GET", "/content", params=params)


@tool(
    category="sciverse",
    tags=["文献", "附件"],
    display_name="Sciverse 下载附件",
    description=(
        "下载 Sciverse 文献附件（按 file_name 相对路径，常见 PDF）。附件为二进制，"
        "本工具不直接返回文件内容；传 save_relative_path 会保存到当前工作区，"
        "随后可将路径告诉用户或用 read_file 检查。"
    ),
    args_schema=SciverseResourceInput,
)
async def sciverse_resource(
    file_name: str, save_relative_path: str | None = None, runtime: ToolRuntime = None
) -> dict:
    """下载文献附件（二进制仅回传元信息，可选落盘到工作区）。"""
    if not file_name.strip():
        return _invalid("file_name 不能为空")
    if "\\" in file_name or file_name.startswith("/") or ".." in file_name.split("/"):
        return _invalid("file_name 不能包含反斜杠、不能以 / 开头、不能包含 '..'")
    resp = await _call("GET", "/resource", params={"file_name": file_name})
    if isinstance(resp, dict):
        return resp
    data = resp.content
    content_type = resp.headers.get("content-type") or ""
    if len(data) > MAX_RESOURCE_BYTES:
        return _error("too_large", f"附件超过 {MAX_RESOURCE_BYTES // (1024 * 1024)}MB 上限",
                      size=len(data), content_type=content_type)
    if not save_relative_path:
        return {
            "ok": True,
            "message": (
                f"已获取二进制附件 {file_name}（{len(data)} bytes，{content_type}）；"
                "如需交付用户，传 save_relative_path 保存到工作区"
            ),
            "size": len(data),
            "content_type": content_type,
        }
    try:
        saved = await upload_workspace_file(data, save_relative_path, runtime)
    except ValueError as exc:
        if "运行时" in str(exc):
            return _error("no_workspace", f"当前运行时未挂载工作区，无法保存附件: {exc}")
        return _invalid(f"save_relative_path 非法: {exc}")
    return {
        "ok": True,
        "saved_path": saved,
        "size": len(data),
        "content_type": content_type,
        "message": f"已保存附件到 {saved}（{len(data)} bytes，{content_type}）",
    }


@tool(
    category="sciverse",
    tags=["文献", "元数据"],
    display_name="Sciverse 字段目录",
    description=(
        "查看 Sciverse meta-search 支持的字段目录（字段名/类型/是否可过滤排序），"
        "用于构造结构化检索；include_sample_values 返回枚举字段样本值（缓存 24 小时）。"
    ),
    args_schema=SciverseMetaCatalogInput,
)
async def sciverse_meta_catalog(include_sample_values: bool = False) -> dict:
    """查看 meta 结构化检索字段目录。"""
    return await _call_json(
        "GET", "/meta-catalog", params={"include_sample_values": include_sample_values})


@tool(
    category="sciverse",
    tags=["文献", "检索"],
    display_name="Sciverse 结构化检索",
    description=(
        "按结构化条件检索 Sciverse 文献元数据：query 全文模糊检索（与 sort 互斥），"
        "filters/sort/fields 字段名取自 meta_catalog；page 分页与 cursor 翻页二选一"
        "（cursor 优先，二者不可同时用）。"
    ),
    args_schema=SciverseMetaSearchInput,
)
async def sciverse_meta_search(
    query: str | None = None,
    filters: list[dict] | str | None = None,
    sort: list[dict] | str | None = None,
    fields: list[str] | str | None = None,
    page: int = 1,
    page_size: int = 25,
    cursor: str | None = None,
    freshness_boost: str | None = None,
) -> dict:
    """按结构化条件检索文献元数据。"""
    filters_parsed, filters_decl = _list_arg(filters)
    sort_parsed, sort_decl = _list_arg(sort)
    fields_parsed, fields_decl = _list_arg(fields)
    if filters_decl and filters_parsed is None:
        return _invalid("filters 参数格式非法（应为 JSON 数组）")
    if sort_decl and sort_parsed is None:
        return _invalid("sort 参数格式非法（应为 JSON 数组）")
    if fields_decl and fields_parsed is None:
        return _invalid("fields 参数格式非法（应为 JSON 数组）")
    if query and sort_parsed:
        return _invalid("query 与 sort 互斥，请只传其中一个")
    if filters_parsed is not None and any(
        not isinstance(item, dict) or not str(item.get("field") or "").strip()
        for item in filters_parsed
    ):
        return _invalid("filters 每项必须为对象 {field, operator?, value} 且 field 不能为空")
    if sort_parsed is not None and any(
        not isinstance(item, dict) or not str(item.get("field") or "").strip()
        for item in sort_parsed
    ):
        return _invalid("sort 每项必须为对象 {field, order} 且 field 不能为空")
    if fields_parsed is not None and any(not isinstance(item, str) for item in fields_parsed):
        return _invalid("fields 每项必须是字符串")
    if freshness_boost and freshness_boost.upper() not in FRESHNESS_LEVELS:
        return _invalid(f"freshness_boost 只能是 {'/'.join(FRESHNESS_LEVELS)}")
    if cursor and page > 1:
        return _invalid("cursor 翻页与 page>1 互斥，请只使用其中一种")
    body: dict[str, Any] = {}
    if query:
        body["query"] = query
    if filters_parsed is not None:
        body["filters"] = filters_parsed
    if sort_parsed is not None:
        body["sort"] = sort_parsed
    if fields_parsed is not None:
        body["fields"] = fields_parsed
    if cursor:
        body["cursor"] = cursor
    else:
        body["page"] = page
        body["page_size"] = page_size
    if freshness_boost:
        body["freshness_boost"] = freshness_boost.upper()
    return await _call_json("POST", "/meta-search", json_body=body)
