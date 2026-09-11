"""内置工具集：file.* / python.run / knowledge.search / http.request。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..types import ToolContext, ToolResult
from .registry import ToolRegistry, tool
from .sandbox import run_python


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
    description="读取用户工作区中的文本文件内容",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对工作区根的路径"},
    }, "required": ["path"]},
    timeout_s=10,
)
async def file_read(ctx: ToolContext, args: dict) -> ToolResult:
    """读取工作区文件。"""
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
    return ToolResult(ok=True, content=text, data={"path": args["path"], "size": len(text)})


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
    description="列出用户工作区指定目录下的文件（递归相对路径）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "相对路径，默认 '.'"},
    }},
    timeout_s=10,
)
async def file_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出工作区文件树。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    base = _safe_path(ctx.workspace_root, args.get("path", "."))
    if base is None:
        return ToolResult(ok=False, content="路径越界", error="path_escape")
    if not base.exists():
        return ToolResult(ok=False, content=f"目录不存在: {args.get('path')}", error="not_found")
    files = [
        p.relative_to(ctx.workspace_root.resolve()).as_posix()
        for p in base.rglob("*") if p.is_file()
    ]
    return ToolResult(ok=True, content="\n".join(files) or "(空目录)", data={"files": files})


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
    """在用户沙箱 tmp/ 下执行代码。"""
    if ctx.workspace_root is None:
        return _no_workspace()
    cwd = ctx.workspace_root / "tmp"
    cwd.mkdir(parents=True, exist_ok=True)
    result = await run_python(args["code"], cwd=cwd, timeout_s=60)
    result.data["cwd"] = str(cwd)
    return result


@tool(
    name="knowledge.search",
    description="在科研知识库中检索相关资料（当前为示例实现，正式接入 WeKnora 后语义不变）",
    parameters={"type": "object", "properties": {
        "query": {"type": "string"}, "knowledge_base": {"type": "string"},
        "top_k": {"type": "integer", "default": 5},
    }, "required": ["query"]},
    timeout_s=30,
)
async def knowledge_search(ctx: ToolContext, args: dict) -> ToolResult:
    """mock 知识检索：返回 WeKnora 形状的占位结果。"""
    query = args["query"]
    results = [{
        "content": f"[mock] 关于「{query}」的检索占位结果 #{i + 1}",
        "source": "mock", "page": None, "score": round(0.9 - i * 0.1, 2),
    } for i in range(min(args.get("top_k", 5), 5))]
    return ToolResult(
        ok=True,
        content="\n\n".join(r["content"] for r in results),
        data={"results": results, "mock": True, "knowledge_base": args.get("knowledge_base", "default")},
    )


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


def register_builtin_tools(registry: ToolRegistry) -> None:
    """把全部内置工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    for fn in (
        file_read, file_write, file_list, python_run, knowledge_search, http_request,
        skill_list, skill_read,
    ):
        registry.register(fn)
