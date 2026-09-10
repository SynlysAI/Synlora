"""内置工具单测。"""
from synlys_harness.tools.builtin import register_builtin_tools
from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry
from synlys_harness.types import ToolContext

EXPECTED = ["file.read", "file.write", "file.list", "python.run", "knowledge.search", "http.request"]


def _setup() -> ToolPipeline:
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return ToolPipeline(registry=reg)


def _ctx(tmp_path, extra=None) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path, extra=extra or {})


def test_all_registered():
    """六个内置工具全部注册成功。"""
    reg = ToolRegistry()
    register_builtin_tools(reg)
    assert sorted(reg.names) == sorted(EXPECTED)


async def test_file_roundtrip_and_escape_guard(tmp_path):
    """写入→读取正常；路径逃逸被拒。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    w = await pipe.run("file.write", ctx, {"path": "a/b.txt", "content": "hello"})
    assert w.ok and (tmp_path / "a" / "b.txt").read_text() == "hello"
    r = await pipe.run("file.read", ctx, {"path": "a/b.txt"})
    assert r.ok and r.content == "hello"
    esc = await pipe.run("file.read", ctx, {"path": "../../etc/passwd"})
    assert not esc.ok and esc.error == "path_escape"
    esc2 = await pipe.run("file.write", ctx, {"path": "../evil.txt", "content": "x"})
    assert not esc2.ok and esc2.error == "path_escape"


async def test_no_workspace_guard():
    """未挂载工作区时文件与沙箱工具统一拒绝。"""
    pipe = _setup()
    ctx = ToolContext(user_id="u1", run_id="r1", workspace_root=None)
    r = await pipe.run("file.read", ctx, {"path": "a.txt"})
    assert not r.ok and r.error == "no_workspace"
    p = await pipe.run("python.run", ctx, {"code": "print(1)"})
    assert not p.ok and p.error == "no_workspace"


async def test_file_list(tmp_path):
    """file.list 返回相对路径列表。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    (tmp_path / "x.csv").write_text("1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.txt").write_text("2")
    r = await pipe.run("file.list", ctx, {"path": "."})
    assert r.ok and "x.csv" in r.content and "sub/y.txt" in r.content


async def test_python_run_tool(tmp_path):
    """python.run 工具走沙箱执行。"""
    pipe = _setup()
    r = await pipe.run("python.run", _ctx(tmp_path), {"code": "print(6*7)"})
    assert r.ok and "42" in r.content


async def test_knowledge_search_mock(tmp_path):
    """knowledge.search 返回 WeKnora 形状的 mock 结果。"""
    pipe = _setup()
    r = await pipe.run("knowledge.search", _ctx(tmp_path), {"query": "聚酰亚胺", "top_k": 3})
    assert r.ok and r.data["mock"] is True
    assert r.data["results"][0]["source"] == "mock"


async def test_http_request_host_guard(tmp_path):
    """非白名单域名被拒。"""
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"http_allowed_hosts": ["api.example.com"]})
    denied = await pipe.run("http.request", ctx, {"url": "http://evil.com/x"})
    assert not denied.ok and denied.error == "host_denied"


async def test_absolute_path_escape(tmp_path):
    """绝对路径（盘符/根路径）不能逃出工作区。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    esc = await pipe.run("file.read", ctx, {"path": "C:/Windows/win.ini"})
    assert not esc.ok and esc.error == "path_escape"
    esc2 = await pipe.run("file.write", ctx, {"path": "C:/Windows/SynlysEvil.ini", "content": "x"})
    assert not esc2.ok and esc2.error == "path_escape"
    assert not (tmp_path / ".." / ".." / "Windows" / "SynlysEvil.ini").resolve().exists()


async def test_http_subdomain_allowed(tmp_path):
    """白名单域名的子域放行（不被 host_denied 拒绝）。"""
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"http_allowed_hosts": ["example.com"]})
    r = await pipe.run("http.request", ctx, {"url": "http://sub.example.com/x"})
    # 通过白名单检查后因无真实服务落到 http_error，而非 host_denied
    assert r.error != "host_denied" and not r.ok
