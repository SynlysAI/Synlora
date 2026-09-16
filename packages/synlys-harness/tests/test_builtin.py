"""内置工具单测。"""
from synlys_harness.tools.builtin import register_builtin_tools, skill_list, skill_read
from synlys_harness.tools.pipeline import ToolPipeline
from synlys_harness.tools.registry import ToolRegistry
from synlys_harness.types import ToolContext, ToolResult

EXPECTED = [
    "file.read", "file.write", "file.list", "python.run", "file.read_image",
    "knowledge.list", "knowledge.search", "web.search", "web.fetch", "http.request",
    "ask_user", "file.send", "skill.list", "skill.read",
    "job.submit", "job.status", "job.list", "job.cancel",
]


def _setup() -> ToolPipeline:
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return ToolPipeline(registry=reg)


def _ctx(tmp_path, extra=None) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path, extra=extra or {})


def test_all_registered():
    """内置工具全部注册成功（含 skill.list / skill.read）。"""
    reg = ToolRegistry()
    register_builtin_tools(reg)
    assert sorted(reg.names) == sorted(EXPECTED)


def test_skill_tools_registered():
    """skill.list / skill.read 必须真正进入注册表。"""
    reg = ToolRegistry()
    register_builtin_tools(reg)
    assert "skill.list" in reg.names
    assert "skill.read" in reg.names
    assert reg.get("skill.list").parameters["type"] == "object"
    assert reg.get("skill.read").parameters["required"] == ["name"]


async def test_file_roundtrip_and_escape_guard(tmp_path):
    """写入→读取正常（带行号）；路径逃逸被拒。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    w = await pipe.run("file.write", ctx, {"path": "a/b.txt", "content": "hello"})
    assert w.ok and (tmp_path / "a" / "b.txt").read_text() == "hello"
    r = await pipe.run("file.read", ctx, {"path": "a/b.txt"})
    assert r.ok and "1│ hello" in r.content and r.data["truncated"] is False
    esc = await pipe.run("file.read", ctx, {"path": "../../etc/passwd"})
    assert not esc.ok and esc.error == "path_escape"
    esc2 = await pipe.run("file.write", ctx, {"path": "../evil.txt", "content": "x"})
    assert not esc2.ok and esc2.error == "path_escape"


async def test_file_read_pagination(tmp_path):
    """长文件分页：默认前 1000 行；offset 续读；截断提示明确。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    content = "\n".join(f"行{i}" for i in range(25))
    await pipe.run("file.write", ctx, {"path": "data.txt", "content": content})
    # limit 分页 + 未读完提示
    r = await pipe.run("file.read", ctx, {"path": "data.txt", "offset": 10, "limit": 5})
    assert r.ok and "11│ 行10" in r.content and "15│ 行14" in r.content
    assert "16│ 行15" not in r.content
    assert r.data["truncated"] is True and r.data["total_lines"] == 25
    assert "offset=15" in r.content and "共 25 行" in r.content
    # 读完最后一页：无截断提示
    r2 = await pipe.run("file.read", ctx, {"path": "data.txt", "offset": 20, "limit": 100})
    assert r2.ok and r2.data["truncated"] is False and "25│ 行24" in r2.content
    assert "未读完" not in r2.content


async def test_file_list_truncation(tmp_path):
    """file.list 超量截断提示（max_entries）。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    for i in range(7):
        (tmp_path / f"f{i}.txt").write_text("x")
    r = await pipe.run("file.list", ctx, {"path": ".", "max_entries": 3})
    assert r.ok and r.data["truncated"] is True and r.data["total"] == 7
    assert "共 7 个文件" in r.content and "仅列出前 3 个" in r.content


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


async def test_knowledge_search_guards(tmp_path):
    """knowledge.search：未配置 WeKnora / 无范围（未传参且无绑定）时明确报错。"""
    pipe = _setup()
    uncfg = await pipe.run("knowledge.search", _ctx(tmp_path), {"query": "聚酰亚胺"})
    assert not uncfg.ok and uncfg.error == "weknora_unconfigured"
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": [],
    })
    nokb = await pipe.run("knowledge.search", ctx, {"query": "聚酰亚胺"})
    assert not nokb.ok and nokb.error == "no_knowledge_base"
    assert "knowledge.list" in nokb.content


async def test_knowledge_list(tmp_path, monkeypatch):
    """knowledge.list：列知识库 + 逐库取文档数（total）展示。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self.text = ""
            self._payload = payload
        def json(self):
            return self._payload

    class _Client:
        urls: list = []
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None, params=None):
            _Client.urls.append(url)
            return _Resp({"data": [
                {"id": "kb-1", "name": "粘结剂资料库", "description": "文献", "knowledge_count": 12},
                {"id": "kb-2", "name": "实验数据", "description": "", "knowledge_count": None},
            ]})

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
    })
    r = await pipe.run("knowledge.list", ctx, {})
    assert r.ok and r.data["count"] == 2
    assert "粘结剂资料库" in r.content and "kb-1" in r.content
    assert "12 篇文档" in r.content and "文档数未知" in r.content
    # 单次请求拿全部（文档数用列表自带 knowledge_count，不再逐库查）
    assert _Client.urls == ["http://wk.test/api/v1/knowledge-bases"]
    # 未配置时统一报错
    bad = await pipe.run("knowledge.list", _ctx(tmp_path), {})
    assert not bad.ok and bad.error == "weknora_unconfigured"


async def test_knowledge_search_explicit_ids(tmp_path, monkeypatch):
    """knowledge.search 传 knowledge_base_ids：优先于 ctx 绑定范围（自主选库）。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"data": [{"knowledge_title": "t", "knowledge_filename": "f.pdf",
                              "content": "命中", "score": 0.9}]}

    class _Client:
        last = None
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, headers=None, json=None):
            _Client.last = {"url": url, "json": json}
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    pipe = _setup()
    # ctx 不带绑定范围：参数指定 kb-2 也能检索
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
    })
    r = await pipe.run("knowledge.search", ctx,
                       {"query": "q", "knowledge_base_ids": ["kb-2"]})
    assert r.ok and r.data["hits"] == 1
    assert _Client.last["json"]["knowledge_base_ids"] == ["kb-2"]


async def test_knowledge_search_weknora(tmp_path, monkeypatch):
    """knowledge.search 调 WeKnora /knowledge-search 并格式化片段。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"success": True, "data": [
                {"knowledge_title": "粘结剂综述", "knowledge_filename": "review.pdf",
                 "content": "PVDF 是常用粘结剂", "score": 0.92},
                {"knowledge_title": "实验记录", "knowledge_filename": "lab.md",
                 "content": "PAA 水系粘结剂", "score": 0.81},
            ]}

    class _Client:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, headers=None, json=None):
            _Client.last = {"url": url, "headers": headers, "json": json}
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1"],
    })
    r = await pipe.run("knowledge.search", ctx, {"query": "粘结剂", "top_k": 5})
    assert r.ok and r.data["hits"] == 2
    assert "PVDF 是常用粘结剂" in r.content and "review.pdf" in r.content
    # v0.7.1 推荐的混合检索端点；服务端限量 + 跨库 ids
    assert _Client.last["url"].endswith("/knowledge-bases/kb-1/hybrid-search")
    assert _Client.last["json"]["query_text"] == "粘结剂"
    assert _Client.last["json"]["match_count"] == 5
    assert _Client.last["json"]["knowledge_base_ids"] == ["kb-1"]
    assert _Client.last["headers"]["X-API-Key"] == "sk-x"


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


async def test_skill_tools_run_through_pipeline(tmp_path):
    """skill.list / skill.read 经工具管线可正常调用。"""
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"skills": {"pdf-extraction": "# PDF 抽取\n\n步骤..."}})
    listed = await pipe.run("skill.list", ctx, {})
    assert listed.ok and "pdf-extraction" in listed.content
    read = await pipe.run("skill.read", ctx, {"name": "pdf-extraction"})
    assert read.ok and read.data["content"].startswith("# PDF 抽取")


async def test_skill_list_and_read(tmp_path):
    ctx = ToolContext(user_id="u", run_id="r", workspace_root=tmp_path,
                      extra={"skills": {"pdf-extraction": "# PDF 抽取\n\n步骤..."}})
    listed = await skill_list(ctx, {})
    assert listed.ok and "pdf-extraction" in listed.content
    read = await skill_read(ctx, {"name": "pdf-extraction"})
    assert read.ok and read.data["content"].startswith("# PDF 抽取")


async def test_skill_read_unknown_name(tmp_path):
    ctx = ToolContext(user_id="u", run_id="r", workspace_root=tmp_path, extra={"skills": {}})
    res = await skill_read(ctx, {"name": "nope"})
    assert res.ok is False and "nope" in (res.error or "")


async def test_skill_tools_without_skills_key(tmp_path):
    """ctx.extra 完全没有 skills 键时：list 提示为空，read 返回失败而非 KeyError。"""
    ctx = _ctx(tmp_path, extra={"unrelated": 1})
    listed = await skill_list(ctx, {})
    assert listed.ok is True and "没有可用技能" in listed.content
    res = await skill_read(ctx, {"name": "pdf-extraction"})
    assert res.ok is False and "pdf-extraction" in (res.error or "")


async def test_skill_list_includes_meta_description(tmp_path):
    """skill.list 的清单带 skill_meta 中的描述。"""
    ctx = _ctx(tmp_path, extra={
        "skills": {"pdf-extraction": "# PDF 抽取\n\n步骤...", "tabular-qa": "# 表格问答\n\n..."},
        "skill_meta": {"pdf-extraction": "从 PDF 中抽取结构化数据", "tabular-qa": "对表格做问答"},
    })
    listed = await skill_list(ctx, {})
    assert listed.ok
    assert "从 PDF 中抽取结构化数据" in listed.content
    assert "对表格做问答" in listed.content
    assert "pdf-extraction" in listed.content and "tabular-qa" in listed.content


async def test_skill_read_blank_or_missing_name(tmp_path):
    """name 缺失或为空白串时返回 ok=False，不抛异常。"""
    ctx = _ctx(tmp_path, extra={"skills": {"pdf-extraction": "# PDF 抽取"}})
    missing = await skill_read(ctx, {})
    assert missing.ok is False
    blank = await skill_read(ctx, {"name": "   "})
    assert blank.ok is False
    numeric = await skill_read(ctx, {"name": 123})
    assert numeric.ok is False


# ---------- 助手绑定范围 = 硬边界（绑定后只能看/搜绑定的库） ----------


class _WkClient:
    """可配置响应的假 WeKnora 客户端（list/search 共用）。"""

    kb_list: list = []
    search_hits: list = []
    last_post: dict | None = None

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        class _R:
            def __init__(self, payload):
                self.status_code = 200
                self.text = ""
                self._payload = payload
            def json(self):
                return self._payload
        if url.endswith("/knowledge-bases"):
            return _R({"data": _WkClient.kb_list})
        return _R({"total": 7})  # /knowledge-bases/{id}/knowledge 文档数

    async def post(self, url, headers=None, json=None):
        class _R:
            status_code = 200
            text = ""
            def json(self):
                return {"data": _WkClient.search_hits}
        _WkClient.last_post = json
        return _R()


def _patch_wk(monkeypatch, kb_list=None, search_hits=None):
    import synlys_harness.tools.builtin as builtin

    _WkClient.kb_list = kb_list or []
    _WkClient.search_hits = search_hits or []
    _WkClient.last_post = None
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _WkClient)


async def test_kb_scope_restricts_list(tmp_path, monkeypatch):
    """助手绑定范围：knowledge.list 只显示绑定的库并标注限定。"""
    _patch_wk(monkeypatch, kb_list=[
        {"id": "kb-1", "name": "库A", "description": "", "chunk_count": 5},
        {"id": "kb-2", "name": "库B", "description": "", "chunk_count": 6},
    ])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1"],
    })
    r = await pipe.run("knowledge.list", ctx, {})
    assert r.ok and r.data["restricted"] is True
    assert "库A" in r.content and "库B" not in r.content


async def test_kb_scope_blocks_out_of_range_search(tmp_path, monkeypatch):
    """范围外检索被拒（kb_not_allowed），交集为空不放行。"""
    _patch_wk(monkeypatch, search_hits=[])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1"],
    })
    r = await pipe.run("knowledge.search", ctx, {"query": "q", "knowledge_base_ids": ["kb-2"]})
    assert not r.ok and r.error == "kb_not_allowed"
    assert _WkClient.last_post is None  # 未发出真实请求


async def test_kb_scope_intersects_requested_ids(tmp_path, monkeypatch):
    """请求含范围外 id：取交集检索（范围内生效，范围外被剔除）。"""
    _patch_wk(monkeypatch, search_hits=[
        {"knowledge_title": "t", "knowledge_filename": "f.pdf", "content": "命中", "score": 0.9},
    ])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1", "kb-2"],
    })
    r = await pipe.run("knowledge.search", ctx,
                       {"query": "q", "knowledge_base_ids": ["kb-2", "kb-evil"]})
    assert r.ok and r.data["hits"] == 1
    assert _WkClient.last_post["knowledge_base_ids"] == ["kb-2"]


async def test_kb_scope_bound_default_no_arg(tmp_path, monkeypatch):
    """绑定范围 + 不传参：直接检索全部绑定库。"""
    _patch_wk(monkeypatch, search_hits=[
        {"knowledge_title": "t", "knowledge_filename": "", "content": "命中", "score": 0.9},
    ])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1", "kb-2"],
    })
    r = await pipe.run("knowledge.search", ctx, {"query": "q"})
    assert r.ok
    assert _WkClient.last_post["knowledge_base_ids"] == ["kb-1", "kb-2"]


# ---------- web.search（SearXNG 联网搜索） ----------


async def test_web_search_unconfigured(tmp_path):
    """未配置 SearXNG 地址：明确报错。"""
    pipe = _setup()
    r = await pipe.run("web.search", _ctx(tmp_path), {"query": "q"})
    assert not r.ok and r.error == "search_unconfigured"


async def test_web_search_formats_results(tmp_path, monkeypatch):
    """调 /search?format=json 并格式化即时答案 + 结果列表。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        status_code = 200
        text = ""
        def json(self):
            return {
                "answers": ["PVDF 是聚偏氟乙烯"],
                "results": [
                    {"title": "PVDF binder", "url": "https://a.com/1", "content": "常用粘结剂"},
                    {"title": "PAA binder", "url": "https://a.com/2", "content": ""},
                ],
                "unresponsive_engines": [],
            }

    class _Client:
        last = None
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None, params=None):
            _Client.last = {"url": url, "headers": headers, "params": params}
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    pipe = _setup()
    # 配了 API Key 时应携带 X-API-Key 头
    ctx = _ctx(tmp_path, extra={
        "web_search_endpoint": "http://sx.test", "web_search_api_key": "sk-sx",
    })
    r = await pipe.run("web.search", ctx, {"query": "PVDF", "max_results": 5})
    assert r.ok and r.data["results"] == 2
    assert "即时答案：PVDF 是聚偏氟乙烯" in r.content
    assert "https://a.com/1" in r.content and "常用粘结剂" in r.content
    assert _Client.last["url"] == "http://sx.test/search"
    assert _Client.last["params"]["format"] == "json"
    assert _Client.last["params"]["q"] == "PVDF"
    assert _Client.last["headers"]["X-API-Key"] == "sk-sx"


# ---------- web.fetch（网页正文抓取 + SSRF 防护） ----------


def _fake_dns(monkeypatch, ips):
    """把 DNS 解析替换为固定映射（ips 可为列表=全主机同 IP，或 dict=按主机）。"""
    import socket as real_socket
    table = ips if isinstance(ips, dict) else None

    def fake_getaddrinfo(host, port, *a, **kw):
        resolved = table.get(host, ["93.184.216.34"]) if table is not None else ips
        return [(real_socket.AF_INET, 1, 6, "", (ip, port)) for ip in resolved]

    monkeypatch.setattr(real_socket, "getaddrinfo", fake_getaddrinfo)


async def test_web_fetch_ssrf_guards(tmp_path, monkeypatch):
    """协议/端口/私网 IP/环回 全部拒绝，且不发出请求。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    # 协议
    r = await pipe.run("web.fetch", ctx, {"url": "file:///etc/passwd"})
    assert not r.ok and r.error == "url_denied"
    # 端口
    r = await pipe.run("web.fetch", ctx, {"url": "http://example.com:8005/x"})
    assert not r.ok and r.error == "url_denied"
    # 字面环回/私网（无需 DNS）
    for bad in ("http://127.0.0.1/x", "http://10.1.2.3/x", "http://192.168.1.1/x",
                "http://169.254.169.254/latest/meta-data"):
        r = await pipe.run("web.fetch", ctx, {"url": bad})
        assert not r.ok and r.error == "url_denied", bad
    # 域名解析到私网 IP（伪造 DNS）
    _fake_dns(monkeypatch, ["10.0.0.5"])
    r = await pipe.run("web.fetch", ctx, {"url": "http://internal.example.com/api"})
    assert not r.ok and r.error == "url_denied"
    assert "内网地址" in r.content


async def test_web_fetch_html_to_text_and_truncate(tmp_path, monkeypatch):
    """公网页面：标题进输出头、HTML 转纯文本（去 script/style）、超长截断。"""
    import synlys_harness.tools.builtin as builtin

    html = (
        "<html><head><title>锂电粘结剂综述 - 示例站</title>"
        "<style>body{}</style><script>alert(1)</script></head>"
        "<body><h1>锂电粘结剂综述</h1><p>PVDF 与 PAA 是两类主流粘结剂。</p></body></html>"
    )

    class _Resp:
        status_code = 200
        text = html
        content = html.encode("utf-8")
        headers = {"content-type": "text/html; charset=utf-8"}
        is_redirect = False

    class _Client:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _fake_dns(monkeypatch, ["93.184.216.34"])  # 公网 IP
    pipe = _setup()
    ctx = _ctx(tmp_path)
    r = await pipe.run("web.fetch", ctx, {"url": "http://example.com/paper", "max_chars": 1000})
    assert r.ok
    assert "标题: 锂电粘结剂综述 - 示例站" in r.content and r.content.startswith("URL: http://example.com/paper")
    assert "锂电粘结剂综述" in r.content and "PVDF" in r.content
    assert "alert" not in r.content and "body{}" not in r.content
    assert r.data["truncated"] is False


async def test_web_fetch_gbk_encoding(tmp_path, monkeypatch):
    """老中文站（GBK，仅 meta 声明）：候选链兜底解码不乱码。"""
    import synlys_harness.tools.builtin as builtin

    html = ('<html><head><meta charset="gbk"><title>粘结剂</title></head>'
            '<body><p>聚偏氟乙烯是常用粘结剂</p></body></html>').encode("gbk")

    class _Resp:
        status_code = 200
        text = ""
        content = html
        headers = {"content-type": "text/html"}
        is_redirect = False

    class _Client:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _fake_dns(monkeypatch, ["93.184.216.34"])
    pipe = _setup()
    r = await pipe.run("web.fetch", _ctx(tmp_path), {"url": "http://example.com/cn"})
    assert r.ok
    assert "聚偏氟乙烯是常用粘结剂" in r.content and "标题: 粘结剂" in r.content


async def test_web_fetch_redirect_recheck(tmp_path, monkeypatch):
    """重定向到内网地址：逐跳复检拒绝。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        status_code = 302
        text = ""
        headers = {"content-type": "text/html", "location": "http://127.0.0.1/steal"}
        is_redirect = True

    class _Client:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    # 首跳域名解析公网；重定向目标的字面 IP 按自身解析（127.0.0.1 → 环回）
    _fake_dns(monkeypatch, {"example.com": ["93.184.216.34"], "127.0.0.1": ["127.0.0.1"]})
    pipe = _setup()
    r = await pipe.run("web.fetch", _ctx(tmp_path), {"url": "http://example.com/go"})
    assert not r.ok and r.error == "url_denied"
    assert "127.0.0.1" in r.content or "内网" in r.content


async def test_web_fetch_unsupported_content_type(tmp_path, monkeypatch):
    """PDF 等非文本类型：明确报不支持。"""
    import synlys_harness.tools.builtin as builtin

    class _Resp:
        status_code = 200
        text = "%PDF-1.4"
        headers = {"content-type": "application/pdf"}
        is_redirect = False

    class _Client:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _fake_dns(monkeypatch, ["93.184.216.34"])
    pipe = _setup()
    r = await pipe.run("web.fetch", _ctx(tmp_path), {"url": "http://example.com/p.pdf"})
    assert not r.ok and r.error == "unsupported_content_type"


# ---------- file.read_image / ask_user / file.send ----------


async def test_file_read_image(tmp_path):
    """read_image：合法图片返回 base64 附件；类型/大小/越界防护。"""
    import base64

    pipe = _setup()
    ctx = _ctx(tmp_path)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    (tmp_path / "pic.png").write_bytes(png)
    r = await pipe.run("file.read_image", ctx, {"path": "pic.png"})
    assert r.ok and r.data["images"][0]["mime"] == "image/png"
    assert base64.b64decode(r.data["images"][0]["base64"]) == png
    bad_type = await pipe.run("file.read_image", ctx, {"path": "x.pdf"})
    (tmp_path / "x.pdf").write_text("pdf")
    bad_type = await pipe.run("file.read_image", ctx, {"path": "x.pdf"})
    assert not bad_type.ok and bad_type.error == "unsupported_type"
    missing = await pipe.run("file.read_image", ctx, {"path": "nope.png"})
    assert not missing.ok and missing.error == "not_found"


async def test_ask_user_tool(tmp_path):
    """ask_user：无 handler 报错；有 handler 透传参数并包装回答。"""
    pipe = _setup()
    nope = await pipe.run("ask_user", _ctx(tmp_path), {"query": "选哪个？"})
    assert not nope.ok and nope.error == "no_handler"

    captured = {}

    async def handler(payload):
        captured.update(payload)
        return "方案A"

    ctx = _ctx(tmp_path, extra={"ask_user_handler": handler, "tool_call_id": "c9"})
    r = await pipe.run("ask_user", ctx, {
        "query": "选哪个？",
        "options": [{"label": "方案A", "description": "稳妥"}, {"label": "方案B"}, {"bad": 1}, "junk"],
    })
    assert r.ok and r.content == "用户回答：方案A"
    assert captured["tool_call_id"] == "c9" and captured["query"] == "选哪个？"
    assert [o["label"] for o in captured["options"]] == ["方案A", "方案B"]  # 脏数据被滤掉


async def test_ask_user_multi_questions(tmp_path):
    """ask_user 多题模式：questions 规范化透传；query/questions 二选一校验。"""
    pipe = _setup()
    captured = {}

    async def handler(payload):
        captured.update(payload)
        return "1. 题：答"

    ctx = _ctx(tmp_path, extra={"ask_user_handler": handler, "tool_call_id": "c10"})
    r = await pipe.run("ask_user", ctx, {
        "questions": [
            {"question": "用哪个数据集？", "header": "数据", "multi_select": True,
             "options": [{"label": "A", "bad": 1}, {"label": "B"}]},
            {"question": "输出什么格式？", "options": []},
            {"bad": 1},  # 无 question 的脏项被滤掉
        ],
    })
    assert r.ok
    qs = captured["questions"]
    assert [q["question"] for q in qs] == ["用哪个数据集？", "输出什么格式？"]
    assert qs[0]["header"] == "数据" and qs[0]["multi_select"] is True
    assert [o["label"] for o in qs[0]["options"]] == ["A", "B"]
    assert qs[1]["options"] == []
    assert "query" in captured and captured["query"] == ""

    # 既无 query 也无 questions：invalid_arguments
    bad = await pipe.run("ask_user", ctx, {})
    assert not bad.ok and bad.error == "invalid_arguments"
    # questions 全是脏项：invalid_arguments
    bad2 = await pipe.run("ask_user", ctx, {"questions": [{"bad": 1}]})
    assert not bad2.ok and bad2.error == "invalid_arguments"


async def test_file_send_tool(tmp_path):
    """file.send：无 handler 报错（登记逻辑在后端服务层测）。"""
    pipe = _setup()
    r = await pipe.run("file.send", _ctx(tmp_path), {"path": "report.docx"})
    assert not r.ok and r.error == "no_handler"


async def test_job_submit_requires_handler(tmp_path):
    """无 job_handler 时四个 job 工具都报 no_handler（fail-closed）。"""
    pipe = _setup()
    ctx = _ctx(tmp_path)
    for name, args in (
        ("job.submit", {"kind": "spec.nmr.forward", "params": {}}),
        ("job.status", {"job_id": "j1"}),
        ("job.list", {}),
        ("job.cancel", {"job_id": "j1"}),
    ):
        result = await pipe.run(name, ctx, args)
        assert result.ok is False
        assert result.error == "no_handler"


async def test_job_submit_validates_arguments(tmp_path):
    """kind 为空或 params 非对象时不调 handler。"""
    calls: list[dict] = []

    async def handler(payload: dict):
        calls.append(payload)
        return ToolResult(ok=True, content="ok")

    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"job_handler": handler})
    bad_kind = await pipe.run("job.submit", ctx, {"kind": "  ", "params": {}})
    assert bad_kind.ok is False and bad_kind.error == "invalid_arguments"
    bad_params = await pipe.run("job.submit", ctx, {"kind": "k", "params": "oops"})
    assert bad_params.ok is False and bad_params.error == "invalid_arguments"
    assert calls == []


async def test_job_submit_rejects_null_arguments(tmp_path):
    """显式传 null 与传空串同义（不能被 str(None) 变成 "None" 绕过校验）。"""
    calls: list[dict] = []

    async def handler(payload: dict):
        calls.append(payload)
        return ToolResult(ok=True, content="ok")

    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"job_handler": handler})
    null_kind = await pipe.run("job.submit", ctx, {"kind": None, "params": {}})
    assert null_kind.ok is False and null_kind.error == "invalid_arguments"
    null_job = await pipe.run("job.status", ctx, {"job_id": None})
    assert null_job.ok is False and null_job.error == "invalid_arguments"
    assert calls == []


async def test_job_submit_forwards_payload(tmp_path):
    """submit 把 action/kind/params/label/tool_call_id 原样交给宿主 handler。"""
    seen: list[dict] = []

    async def handler(payload: dict):
        seen.append(payload)
        return ToolResult(ok=True, content="已提交，任务 ID: j-1")

    pipe = _setup()
    ctx = _ctx(tmp_path, extra={"job_handler": handler, "tool_call_id": "tc-9"})
    result = await pipe.run("job.submit", ctx, {
        "kind": "spec.nmr.forward", "params": {"smiles_input": "CCO"}, "label": "乙醇预测"})
    assert result.ok is True
    assert seen[0]["action"] == "submit"
    assert seen[0]["kind"] == "spec.nmr.forward"
    assert seen[0]["params"] == {"smiles_input": "CCO"}
    assert seen[0]["label"] == "乙醇预测"
    assert seen[0]["tool_call_id"] == "tc-9"
