"""复用 catalog 插件工具模块构建 MCP 工具表。

插件工具是带 ``__tool_definition__`` 的普通异步函数；网关直接导入复用
（登录、轮询、截断、错误归一化零移植漂移），仅做 MCP 命名 sanitize。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent.parent
_HARNESS_SRC = REPO_ROOT / "packages" / "synlys-harness" / "src"
if _HARNESS_SRC.is_dir() and str(_HARNESS_SRC) not in sys.path:
    sys.path.insert(0, str(_HARNESS_SRC))

from synlys_harness.types import ToolContext  # noqa: E402

# 复用的插件工具模块（文件相对 catalog/plugins/<id>/）。
PLUGIN_TOOL_MODULES = {
    "spec_agent": ("tools.py", ("spec_nmr_forward", "spec_nmr_reverse", "spec_nmr_search")),
    "poly_agent": (
        "tools.py",
        (
            "poly_tg_predict",
            "poly_electrolyte_predict",
            "poly_raman_analyze",
            "poly_reactivity_fit",
            "poly_silicon_bond",
        ),
    ),
    "sciverse": (
        "tools.py",
        (
            "sciverse_agentic_search",
            "sciverse_content",
            "sciverse_resource",
            "sciverse_meta_catalog",
            "sciverse_meta_search",
        ),
    ),
}


def _load_plugin_module(plugin_id: str, filename: str):
    """按文件路径加载一个插件工具模块（与宿主加载方式一致）。"""
    path = BACKEND_DIR / "catalog" / "plugins" / plugin_id / filename
    spec = importlib.util.spec_from_file_location(
        f"catalog_plugins_{plugin_id}_{Path(filename).stem}", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mcp_name(tool_name: str) -> str:
    """MCP 工具名只允许 [A-Za-z0-9_-]，点号转下划线。"""
    return tool_name.replace(".", "_")


def build_plugin_tool_table() -> dict:
    """收集复用工具：MCP 名 → {fn, definition}。"""
    table: dict[str, Any] = {}
    for plugin_id, (filename, functions) in PLUGIN_TOOL_MODULES.items():
        module = _load_plugin_module(plugin_id, filename)
        for function_name in functions:
            fn = getattr(module, function_name)
            definition = fn.__tool_definition__
            table[mcp_name(definition.name)] = {"fn": fn, "definition": definition}
    return table


def make_tool_context(plugin_configs: dict, *, workspace_root=None) -> ToolContext:
    """构造宿主同构的工具上下文（配置按插件命名空间注入）。"""
    return ToolContext(
        user_id="sd-mcp-gateway",
        run_id=f"mcp-{uuid.uuid4().hex[:12]}",
        workspace_root=workspace_root,
        extra={"plugins": plugin_configs},
    )
