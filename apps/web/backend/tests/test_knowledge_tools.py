"""WeKnora 知识检索工具单测（原 harness test_builtin 中的 knowledge 用例，随工具迁入宿主）。"""
import app.tools.knowledge as knowledge_tools
from synlys_harness import ToolContext, ToolPipeline, ToolRegistry

from app.tools.knowledge import knowledge_list, knowledge_search


def _setup() -> ToolPipeline:
    reg = ToolRegistry()
    reg.register(knowledge_list)
    reg.register(knowledge_search)
    return ToolPipeline(registry=reg)


def _ctx(tmp_path, extra=None) -> ToolContext:
    return ToolContext(user_id="u1", run_id="r1", workspace_root=tmp_path, extra=extra or {})


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
        return _R({"total": 7})

    async def post(self, url, headers=None, json=None):
        class _R:
            status_code = 200
            text = ""
            def json(self):
                return {"data": _WkClient.search_hits}
        _WkClient.last_post = json
        return _R()


def _patch_wk(monkeypatch, kb_list=None, search_hits=None):
    """把假客户端挂到工具模块的 httpx 上（连接信息由用例 ctx 自带）。"""
    _WkClient.kb_list = kb_list or []
    _WkClient.search_hits = search_hits or []
    _WkClient.last_post = None
    monkeypatch.setattr(knowledge_tools.httpx, "AsyncClient", _WkClient)


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
    """knowledge.list：列知识库 + 展示文档数。"""
    _patch_wk(monkeypatch, kb_list=[
        {"id": "kb-1", "name": "粘结剂资料库", "description": "文献", "knowledge_count": 12},
        {"id": "kb-2", "name": "实验数据", "description": "", "knowledge_count": None},
    ])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
    })
    r = await pipe.run("knowledge.list", ctx, {})
    assert r.ok and r.data["count"] == 2
    assert "粘结剂资料库" in r.content and "kb-1" in r.content
    assert "12 篇文档" in r.content and "文档数未知" in r.content
    # 未配置时统一报错
    bad = await pipe.run("knowledge.list", _ctx(tmp_path), {})
    assert not bad.ok and bad.error == "weknora_unconfigured"


async def test_knowledge_search_explicit_ids(tmp_path, monkeypatch):
    """knowledge.search 传 knowledge_base_ids：优先于 ctx 绑定范围（自主选库）。"""
    _patch_wk(monkeypatch, search_hits=[
        {"knowledge_title": "t", "knowledge_filename": "f.pdf",
         "content": "命中", "score": 0.9},
    ])
    pipe = _setup()
    # ctx 不带绑定范围：参数指定 kb-2 也能检索
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
    })
    r = await pipe.run("knowledge.search", ctx,
                       {"query": "q", "knowledge_base_ids": ["kb-2"]})
    assert r.ok and r.data["hits"] == 1
    assert _WkClient.last_post["knowledge_base_ids"] == ["kb-2"]


async def test_knowledge_search_weknora(tmp_path, monkeypatch):
    """knowledge.search 调 WeKnora hybrid-search 并格式化片段。"""
    _patch_wk(monkeypatch, search_hits=[
        {"knowledge_title": "粘结剂综述", "knowledge_filename": "review.pdf",
         "content": "PVDF 是常用粘结剂", "score": 0.92},
        {"knowledge_title": "实验记录", "knowledge_filename": "lab.md",
         "content": "PAA 水系粘结剂", "score": 0.81},
    ])
    pipe = _setup()
    ctx = _ctx(tmp_path, extra={
        "weknora_base_url": "http://wk.test/api/v1", "weknora_api_key": "sk-x",
        "knowledge_base_ids": ["kb-1"],
    })
    r = await pipe.run("knowledge.search", ctx, {"query": "粘结剂", "top_k": 5})
    assert r.ok and r.data["hits"] == 2
    assert "PVDF 是常用粘结剂" in r.content and "review.pdf" in r.content
    # v0.7.1 推荐的混合检索端点；服务端限量 + 跨库 ids
    assert _WkClient.last_post is not None


async def test_kb_scope_restricts_list(tmp_path, monkeypatch):
    """助手绑定范围：knowledge.list 只显示绑定的库并标注限定。"""
    _patch_wk(monkeypatch, kb_list=[
        {"id": "kb-1", "name": "库A", "description": ""},
        {"id": "kb-2", "name": "库B", "description": ""},
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
