"""Spec_Agent 插件包测试：manifest、工具收集、工具层行为（MockTransport 打桩）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from synlys_harness import ToolContext

from app.catalog.loader import load_plugin_tools, scan_catalog

REPO_CATALOG = Path(__file__).resolve().parents[1] / "catalog"
REPO_PLUGINS = REPO_CATALOG / "plugins"


_TOOLS_CACHE: tuple | None = None


def _load_tools() -> tuple:
    """从仓库插件目录加载 spec_agent 工具（缓存模块对象与工具表）。

    Returns:
        (module, {工具名: 函数})。缓存模块对象是为了让 monkeypatch 的目标
        与工具函数实际使用的模块一致（loader 每次调用都会重建模块对象）。
    """
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        packages = scan_catalog([REPO_CATALOG]).plugins
        module_name = "synlora_plugin_spec_agent"
        tools = load_plugin_tools(packages["spec_agent"])
        module = sys.modules[module_name]  # load_plugin_tools 已写入
        _TOOLS_CACHE = (module, {t.__tool_definition__.name: t for t in tools})
    return _TOOLS_CACHE


def test_manifest_is_valid_and_complete():
    """manifest 可解析，schema/技能/专家模板齐备。"""
    pkg = scan_catalog([REPO_CATALOG]).plugins["spec_agent"]
    assert pkg.name and pkg.version == "1.0.0"
    assert [f["key"] for f in pkg.config_schema] == ["base_url", "token"]
    assert pkg.config_schema[0]["required"] is True
    assert pkg.config_schema[1]["secret"] is True
    assert pkg.skills == ["spec-nmr", "spec-spectra"]
    assert pkg.skills_root is not None
    assert pkg.connectors_module == "connectors.py"
    assert pkg.expert["name"] == "谱图解析专家"
    assert "spec.nmr.forward" in pkg.expert["tool_whitelist"]
    assert "job.submit" in pkg.expert["tool_whitelist"]


def test_tools_registered_names():
    """插件导出三个工具。"""
    _module, tools = _load_tools()
    assert sorted(tools) == ["spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"]


def _ctx(config: dict | None, ai4ms_token: str | None = None) -> ToolContext:
    """构造带插件命名空间配置的工具上下文。

    Args:
        config: spec_agent 插件配置（None = 未配置）。
        ai4ms_token: 宿主按登录用户代签的 AI⁴MS 凭证（None = 未注入）。

    Returns:
        ToolContext。
    """
    plugins = {"spec_agent": config} if config is not None else {}
    extra: dict = {"plugins": plugins}
    if ai4ms_token is not None:
        extra["ai4ms_token"] = ai4ms_token
    return ToolContext(user_id="u", run_id="r", workspace_root=None, extra=extra)


def _patch_transport(monkeypatch, handler) -> None:
    """把插件的 HTTP 客户端换成 MockTransport 版本（对缓存模块对象打桩）。"""
    module, _tools = _load_tools()
    monkeypatch.setattr(
        module, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=1.0),
    )


async def test_unconfigured_returns_clear_error():
    """未配置（插件未安装）时不出请求，返回明确错误。"""
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx(None), {"smiles_input": "CCO"})
    assert not r.ok and r.error == "spec_agent_unconfigured"
    assert "插件" in r.content


async def test_forward_success(monkeypatch):
    """正向预测：路径/认证头/请求体正确，items 逐条返回。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/nmrserver/forward"
        assert request.headers["authorization"] == "Bearer tok"
        assert json.loads(request.content) == {"smiles_input": "CCO"}
        return httpx.Response(200, json={
            "code": 0, "message": "ok",
            "data": {"items": [{"smiles": "CCO", "c_shifts": [58.0]}]}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](
        _ctx({"base_url": "http://spec.local", "token": "tok"}), {"smiles_input": "CCO"})
    assert r.ok and "CCO" in r.content and r.data["items"] == 1


async def test_empty_token_sends_no_auth_header(monkeypatch):
    """token 留空 = 不发认证头（适配 Spec_Agent AUTH_ENABLED=false）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://spec.local"}), {"smiles_input": "CCO"})
    assert r.ok and seen["has_auth"] is False


async def test_dynamic_token_takes_precedence_over_config(monkeypatch):
    """宿主代签的动态 token 优先于插件配置里的服务 token（按登录用户提交）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](
        _ctx({"base_url": "http://spec.local", "token": "svc-tok"}, ai4ms_token="user-tok"),
        {"smiles_input": "CCO"})
    assert r.ok and seen["auth"] == "Bearer user-tok"


async def test_config_token_fallback_when_no_dynamic_token(monkeypatch):
    """无动态 token（未注入/解析不到身份）→ 回落到配置里的服务 token。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](
        _ctx({"base_url": "http://spec.local", "token": "svc-tok"}), {"smiles_input": "CCO"})
    assert r.ok and seen["auth"] == "Bearer svc-tok"


async def test_upstream_401_is_actionable_error(monkeypatch):
    """上游 401 → unauthorized + 可操作提示（不再原样透出上游 body）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "未登录或登录已失效"})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](
        _ctx({"base_url": "http://x", "token": "stale"}), {"smiles_input": "CCO"})
    assert r.ok is False and r.error == "unauthorized"
    assert "请在管理后台" in r.content or "AI⁴MS 账号" in r.content


async def test_upstream_403_is_actionable_error(monkeypatch):
    """上游 403 与 401 同口径（凭证问题归一类，别让模型以为是参数错）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.search"](_ctx({"base_url": "http://x"}), {"c_shifts_input": "20"})
    assert r.ok is False and r.error == "unauthorized"


async def test_upstream_http_error(monkeypatch):
    """上游 5xx 转 ok=False。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="NMRServer 请求超时")

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.reverse"](_ctx({"base_url": "http://x"}), {"c_shifts_input": "20,30"})
    assert not r.ok and r.error == "http_error" and "504" in r.content


async def test_upstream_business_error(monkeypatch):
    """code != 0 转 ok=False 且带上游 message。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 5001, "message": "SMILES 非法", "data": None})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://x"}), {"smiles_input": "bad"})
    assert not r.ok and r.error == "upstream_error" and "SMILES 非法" in r.content


async def test_search_payload_defaults(monkeypatch):
    """检索：未传参时按上游约束填默认值。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.search"](_ctx({"base_url": "http://x"}), {"c_shifts_input": "20,30"})
    assert r.ok and r.content == "（未返回结果）"
    assert seen["num_search"] == 500 and seen["topk"] == 10
    assert seen["allowed_elements"] == "C,H,N,O" and seen["h_shifts_input"] == ""


async def test_null_args_are_not_stringified(monkeypatch):
    """模型传显式 null 时，参数回落空串而非字面量 "None"。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    await tools["spec.nmr.reverse"](_ctx({"base_url": "http://x"}),
                                    {"c_shifts_input": None, "formula": None})
    assert seen["c_shifts_input"] == "" and seen["formula"] == ""
    assert "None" not in json.dumps(seen)


async def test_search_numeric_defaults_and_clamping(monkeypatch):
    """num_search/topk：null 回落默认；越界值本地钳制到上游约束。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    await tools["spec.nmr.search"](_ctx({"base_url": "http://x"}), {"num_search": None, "topk": None})
    await tools["spec.nmr.search"](_ctx({"base_url": "http://x"}),
                                   {"num_search": "999999", "topk": "0"})
    assert seen[0]["num_search"] == 500 and seen[0]["topk"] == 10
    assert seen[1]["num_search"] == 10000 and seen[1]["topk"] == 1


async def test_connection_error_classified(monkeypatch):
    """连接失败归 connection_error（模型可据此判断可重试）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://x"}), {"smiles_input": "CCO"})
    assert not r.ok and r.error == "connection_error"


async def test_non_json_body_is_upstream_error(monkeypatch):
    """上游返回非 JSON → upstream_error（不伪装成空结果）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://x"}), {"smiles_input": "CCO"})
    assert not r.ok and r.error == "upstream_error"


async def test_non_dict_data_is_upstream_error(monkeypatch):
    """data 非对象 → upstream_error（契约破坏不得伪装成空结果）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": [1, 2]})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://x"}), {"smiles_input": "CCO"})
    assert not r.ok and r.error == "upstream_error"


async def test_reverse_maps_all_fields(monkeypatch):
    """反向预测：六个字段按上游契约透传。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    _module, tools = _load_tools()
    await tools["spec.nmr.reverse"](_ctx({"base_url": "http://x"}), {
        "c_shifts_input": "20.1", "h_shifts_input": "1.2", "h_split_input": "s",
        "formula": "C2H6O", "allowed_elements": "C,H,O", "candidates": "CCO"})
    assert seen == {"c_shifts_input": "20.1", "h_shifts_input": "1.2", "h_split_input": "s",
                    "formula": "C2H6O", "allowed_elements": "C,H,O", "candidates": "CCO"}


def test_all_tools_have_long_timeout():
    """三个工具的超时都大于上游 350s（HTTP 层先报错）。"""
    module, tools = _load_tools()
    assert all(t.__tool_definition__.timeout_s == 380.0 for t in tools.values())
    assert module.REQUEST_TIMEOUT_S == 360.0 and module.CONNECT_TIMEOUT_S == 10.0


def test_skill_file_parses():
    """插件自带技能 SKILL.md 可被技能解析器解析。"""
    from app.services.skill_service import parse_skill_md

    md = REPO_PLUGINS / "spec_agent" / "skills" / "spec-nmr" / "SKILL.md"
    skill = parse_skill_md(md.read_text(encoding="utf-8"))
    assert skill["name"] == "spec-nmr" and "核磁" in skill["description"]


def test_plugin_declares_connectors_and_skill():
    """spec_agent 插件声明连接器模块与谱图任务技能。"""
    plugin_dir = REPO_PLUGINS / "spec_agent"
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["connectors_module"] == "connectors.py"
    assert "spec-spectra" in manifest["skills"]
    skill = plugin_dir / "skills" / "spec-spectra" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    for kind in ("spec.task.nmr", "spec.task.ir", "spec.task.gpc",
                 "spec.task.raman", "spec.task.lcms"):
        assert kind in text


def test_real_plugin_registers_all_five_kinds():
    """真实插件包经 loader + 注册表能挂出 5 个 kind（防声明漏配的静默失效）。"""
    from app.catalog.loader import load_plugin_connectors
    from app.services.job_connectors import JobConnectorRegistry

    package = scan_catalog([REPO_CATALOG]).plugins["spec_agent"]
    assert package.connectors_module == "connectors.py"

    reg = JobConnectorRegistry()
    for connector in load_plugin_connectors(package):
        reg.register(connector)
    assert reg.kinds == ["spec.task.gpc", "spec.task.ir", "spec.task.lcms",
                         "spec.task.nmr", "spec.task.raman"]
