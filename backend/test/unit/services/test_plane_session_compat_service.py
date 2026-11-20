from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

service = importlib.import_module("yuxi.services.plane_session_compat_service")
context_service = importlib.import_module("yuxi.services.plane_context_service")
from yuxi.services.plane_context_service import (  # noqa: E402
    PlaneMcpRuntime,
    ResearchContextError,
    ResearchContextMetadata,
)

USER = SimpleNamespace(id=7, uid="plane:p1", username="alice", role="user")


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return iter(self._value if isinstance(self._value, list) else [])


class FakeDb:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.commits = 0

    async def execute(self, _query):
        value = self.results.pop(0) if self.results else None
        if isinstance(value, list):
            return SimpleNamespace(scalars=lambda: iter(value), scalar_one_or_none=lambda: value[0] if value else None)
        return FakeResult(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class FakeEvents:
    def __init__(self):
        self.rows = []
        self._next_seq = -1

    async def max_seq(self, _thread_id):
        return self._next_seq

    async def append(self, *, thread_id, seq, event_type, run_id, payload, source_key=None):
        self._next_seq = seq
        row = SimpleNamespace(
            thread_id=thread_id,
            seq=seq,
            event_type=event_type,
            run_id=run_id,
            payload=payload,
            source_key=source_key,
        )
        self.rows.append(row)
        return row

    async def source_exists(self, _thread_id, source_key):
        return any(row.source_key == source_key for row in self.rows)

    async def events_after(self, _thread_id, after_seq, limit=500):
        return [row for row in self.rows if row.seq > after_seq][:limit]

    async def event_counts(self, _thread_id):
        return {}


class FakeRuns:
    def __init__(self, rows):
        self.rows = rows
        self.updated = None

    async def list_for_thread(self, _thread_id):
        return self.rows

    async def get_by_external_run_id(self, _external):
        return None

    async def get_by_v1_run_id(self, v1):
        return next((row for row in self.rows if row.v1_run_id == v1), None)

    async def set_current_run_id(self, row, run_id):
        self.updated = (row.v1_run_id, run_id)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _mapping(v1="v1-run", thread="t1", current="run-internal"):
    return SimpleNamespace(
        v1_run_id=v1,
        thread_id=thread,
        uid=USER.uid,
        external_run_id="ext-1",
        current_run_id=current,
        label="hi",
        created_at=None,
        updated_at=None,
    )


def _envelope(event, payload):
    return {"seq": f"1-{event}", "event_type": event, "payload": {"schema_version": 1, "payload": payload}, "ts": 1}


def _make_service(db, mappings):
    svc = service.PlaneSessionCompatService(db)
    svc.runs = FakeRuns(mappings)
    svc.events = FakeEvents()
    return svc


# ---------------------------------------------------------------------------
# 事件投影转换矩阵
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projection_converts_full_lifecycle(monkeypatch):
    db = FakeDb(
        results=[
            "run-internal",
            SimpleNamespace(status="completed", output_message_id=11),
            SimpleNamespace(id=9, content="最终回复"),
        ]
    )
    db.results.append(
        [SimpleNamespace(id=88, content="工具输出", extra_metadata={"tool_name": "web_search", "tool_call_id": "tc1"})]
    )
    svc = _make_service(db, [_mapping()])
    redis = FakeRedis()
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(redis))
    events = [
        # turn/start 在提交时落库，metadata 信标不再投影（避免 resume 后重复 turn）
        _envelope("metadata", {"request_id": "v1-run"}),
        _envelope("end", {"status": "completed"}),
    ]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    types = [row.event_type for row in svc.events.rows]
    assert types == ["assistant/message", "tool/result", "turn/end"]
    assert svc.events.rows[0].payload["content"] == "最终回复"
    assert svc.events.rows[1].payload["name"] == "web_search"
    assert redis.store["plane:proj:run-internal"] == "1-end"


@pytest.mark.asyncio
async def test_projection_is_idempotent_via_cursor(monkeypatch):
    db = FakeDb(results=[None, SimpleNamespace(status="completed", output_message_id=None), None])
    svc = _make_service(db, [_mapping()])
    redis = FakeRedis()
    redis.store["plane:proj:run-internal"] = "1-end"
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(redis))

    async def read_after_cursor(_run_id, *, after_seq, limit=500):
        assert after_seq == "1-end"
        return []

    monkeypatch.setattr(service, "list_run_stream_events", read_after_cursor)
    await svc.project_pending_events("t1")
    assert svc.events.rows == []


@pytest.mark.asyncio
async def test_projection_replay_deduplicates_by_source_key(monkeypatch):
    """游标丢失（如 Redis 重启）整批重放时，源键幂等保证不产生重复事件。"""
    db = FakeDb(
        results=[
            "run-internal",
            SimpleNamespace(status="completed", output_message_id=11),
            SimpleNamespace(id=9, content="回复"),
        ]
    )
    db.results.append([])
    db2 = FakeDb(results=[None])
    svc = _make_service(db, [_mapping()])
    redis = FakeRedis()
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(redis))
    events = [_envelope("error", {"chunk": {"error_type": "x", "error_message": "m"}})]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    redis.store.pop("plane:proj:run-internal")  # 模拟游标丢失
    db2 = FakeDb()
    svc2 = _make_service(db2, [_mapping()])
    svc2.events = svc.events  # 两次投影共享同一 durable 存储（真实场景为同表）
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(redis))
    await svc2.project_pending_events("t1")
    assert len(svc.events.rows) == 1  # 源键已存在，重放零新增


@pytest.mark.asyncio
async def test_projection_maps_interrupt_and_aborted(monkeypatch):
    db = FakeDb(results=["run-internal", None])
    svc = _make_service(db, [_mapping()])
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(FakeRedis()))
    events = [
        _envelope(
            "interrupt",
            {
                "reason": "human_approval",
                "chunk": {"approval": {"action_requests": [{"name": "execute", "id": "tc9", "args": {"cmd": "ls"}}]}},
            },
        ),
        _envelope("end", {"status": "interrupted"}),
        _envelope("error", {"chunk": {"error_type": "model_error", "error_message": "失败"}}),
        _envelope("end", {"status": "cancelled"}),
    ]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    types = [row.event_type for row in svc.events.rows]
    # interrupted 不收尾 turn；error→error；cancelled→turn/aborted
    assert types == ["ask/user", "error", "turn/aborted"]
    ask = svc.events.rows[0].payload
    assert ask["kind"] == "approval" and ask["tool"] == "execute"
    assert svc.events.rows[2].payload["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_projection_maps_question_interrupt(monkeypatch):
    db = FakeDb(results=["run-internal"])
    svc = _make_service(db, [_mapping()])
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(FakeRedis()))
    events = [
        _envelope("interrupt", {"chunk": {"questions": [{"question_id": "q1", "question": "继续？"}]}}),
    ]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    assert svc.events.rows[0].event_type == "ask/user"
    assert svc.events.rows[0].payload["kind"] == "question"


def _async(value):
    return _AsyncWrap(value)


class _AsyncWrap:
    def __init__(self, value):
        self._value = value

    def __await__(self):
        if False:
            yield
        return self._value


def _async_return(value):
    return _AsyncWrap(value)


# ---------------------------------------------------------------------------
# 审批桥与取消
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_allows_and_rejects_approval(monkeypatch):
    for text, expected in (("允许", "approve"), ("拒绝", "reject"), ("随便", "reject")):
        db = FakeDb(
            results=[
                "run-internal",
                SimpleNamespace(
                    status="interrupted", error_type="human_approval_required", agent_slug="default-chatbot"
                ),
            ]
        )
        svc = _make_service(db, [_mapping()])
        calls = {}

        async def fake_resume(**kwargs):
            calls.update(kwargs)
            return {"run_id": "run-resumed"}

        monkeypatch.setattr(service, "create_resume_run_view", fake_resume)
        result = await svc.answer_run("v1-run", USER, text)
        assert result == {"ok": True}
        assert calls["resume"] == {"decisions": [{"type": expected}]}
        assert svc.runs.updated == ("v1-run", "run-resumed")


@pytest.mark.asyncio
async def test_answer_rejects_when_not_interrupted():
    db = FakeDb(
        results=["run-internal", SimpleNamespace(status="completed", error_type=None, agent_slug="default-chatbot")]
    )
    svc = _make_service(db, [_mapping()])
    with pytest.raises(HTTPException) as exc:
        await svc.answer_run("v1-run", USER, "允许")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_on_running_requests_cancel(monkeypatch):
    db = FakeDb(
        results=["run-internal", SimpleNamespace(status="running", error_type=None, agent_slug="default-chatbot")]
    )
    svc = _make_service(db, [_mapping()])
    cancelled = {}

    async def fake_cancel(**kwargs):
        cancelled.update(kwargs)
        return SimpleNamespace(status="cancel_requested")

    monkeypatch.setattr(service, "request_cancel_agent_run", fake_cancel)
    assert await svc.cancel_run("v1-run", USER) == {"ok": True}
    assert cancelled["run_id"] == "run-internal"
    assert cancelled["cascade_children"] is True


@pytest.mark.asyncio
async def test_cancel_on_terminal_returns_not_ok():
    db = FakeDb(
        results=["run-internal", SimpleNamespace(status="completed", error_type=None, agent_slug="default-chatbot")]
    )
    svc = _make_service(db, [_mapping()])
    assert await svc.cancel_run("v1-run", USER) == {"ok": False}


@pytest.mark.asyncio
async def test_cancel_on_question_interrupt_rejected():
    db = FakeDb(
        results=[
            "run-internal",
            SimpleNamespace(
                status="interrupted", error_type="ask_user_question_required", agent_slug="default-chatbot"
            ),
        ]
    )
    svc = _make_service(db, [_mapping()])
    with pytest.raises(HTTPException) as exc:
        await svc.cancel_run("v1-run", USER)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# 上下文校验
# ---------------------------------------------------------------------------


def _context(**overrides):
    values = {
        "schema_version": "agent-context.v3",
        "context_id": "ctx-1",
        "context_hash": "hash-1",
        "issuer": "http://plane.test",
        "target": {"type": "WORKSPACE", "workspace_id": "ws-1", "workspace_slug": "ws-slug"},
        "subject": {"plane_user_id": "p1"},
        "policy": {"policy_id": "pol-1", "policy_hash": "ph-1", "allowed_tools": ["plane_get_context"]},
        "manifest": {"version": 1, "hash": "mh-1"},
    }
    values.update(overrides)
    return ResearchContextMetadata(**values)


def test_plane_mcp_scope_validation():
    ok = PlaneMcpRuntime(url="http://plane.test/api/mcp/", connection_id="c1", allowed_tools=["plane_get_context"])
    context_service.validate_plane_mcp_scope(ok, _context())
    bad_url = PlaneMcpRuntime(url="http://evil.test/api/mcp/", connection_id="c1", allowed_tools=[])
    with pytest.raises(ResearchContextError) as exc:
        context_service.validate_plane_mcp_scope(bad_url, _context())
    assert exc.value.status_code == 403
    too_many = PlaneMcpRuntime(url="http://plane.test/api/mcp/", connection_id="c1", allowed_tools=["plane_write"])
    with pytest.raises(ResearchContextError):
        context_service.validate_plane_mcp_scope(too_many, _context())


def test_plane_metadata_scope_validation():
    meta = {"workspace": {"id": "ws-1", "slug": "ws-slug"}, "target": {"type": "WORKSPACE"}}
    context_service.validate_plane_metadata_scope(meta, _context())
    with pytest.raises(ResearchContextError):
        context_service.validate_plane_metadata_scope(
            {"workspace": {"id": "ws-other", "slug": "x"}, "target": {"type": "WORKSPACE"}}, _context()
        )


def test_v2_context_synthesizes_target():
    v2 = ResearchContextMetadata(
        schema_version="agent-context.v2",
        context_id="ctx-2",
        context_hash="hash-2",
        issuer="http://plane.test",
        workspace_id="ws-1",
        workspace_slug="ws-slug",
        research_project_id="proj-1",
        chain_node_id="",
    )
    projection = v2.normalized_projection()
    assert projection["target"]["workspace_id"] == "ws-1"
    assert projection["target"]["type"] == "PROJECT"


@pytest.mark.asyncio
async def test_validate_research_context_rejects_foreign_issuer():
    with pytest.raises(ResearchContextError) as exc:
        await context_service.validate_research_context(token="t", expected=_context(issuer="http://evil.test"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_validate_research_context_compares_remote(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            # Plane 真实返回是信封，元数据在 context 键下
            return {"schema_version": "2026-09-18", "context": _context().model_dump()}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(context_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        context_service,
        "plane_config",
        SimpleNamespace(
            plane_base_url="http://plane.test", plane_allowed_issuers="", plane_request_timeout_seconds=3.0
        ),
    )
    await context_service.validate_research_context(token="t", expected=_context())

    # 远端不一致 → 403
    class MismatchResponse(FakeResponse):
        def json(self):
            return {"schema_version": "2026-09-18", "context": _context(context_hash="hash-CHANGED").model_dump()}

    async def mismatch_get(_self, _url, headers=None):
        return MismatchResponse()

    FakeClient.get = mismatch_get
    with pytest.raises(ResearchContextError) as exc:
        await context_service.validate_research_context(token="t", expected=_context())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_validate_research_context_detects_policy_inflation(monkeypatch):
    """保留真实哈希但放大 allowed_tools 的篡改必须被完整 policy 比较拒绝。"""

    class InflatedResponse:
        status_code = 200

        def json(self):
            inflated = _context().model_dump()
            inflated["policy"]["allowed_tools"] = ["plane_get_context", "plane_write"]
            return {"schema_version": "2026-09-18", "context": inflated}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, headers=None):
            return InflatedResponse()

    monkeypatch.setattr(context_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        context_service,
        "plane_config",
        SimpleNamespace(
            plane_base_url="http://plane.test", plane_allowed_issuers="", plane_request_timeout_seconds=3.0
        ),
    )
    with pytest.raises(ResearchContextError) as exc:
        await context_service.validate_research_context(token="t", expected=_context())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_projection_duplicate_end_envelope_projects_once(monkeypatch):
    """worker 终态与 SSE 兜底各发一条 end 信标时，终态只投影一次。"""
    db = FakeDb(
        results=[
            "run-internal",
            SimpleNamespace(status="completed", output_message_id=11),
            SimpleNamespace(id=9, content="回复"),
            [SimpleNamespace(id=77, content="ls 输出", extra_metadata={"tool_name": "execute"})],
        ]
    )
    svc = _make_service(db, [_mapping()])
    redis = FakeRedis()
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(redis))
    events = [
        _envelope("end", {"status": "completed"}),
        _envelope("end", {"status": "completed"}),
    ]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    types = [row.event_type for row in svc.events.rows]
    assert types == ["assistant/message", "tool/result", "turn/end"]


@pytest.mark.asyncio
async def test_projection_emits_tool_call_started_events(monkeypatch):
    """messages 信标中的 tool_call 终态块投影为 tool/call（Plane 耗时计算依赖）。"""
    db = FakeDb(results=["run-internal"])
    svc = _make_service(db, [_mapping()])
    monkeypatch.setattr(service, "get_async_redis_client", lambda: _async(FakeRedis()))
    chunk = {
        "request_id": "r",
        "response": None,
        "thread_id": "t1",
        "status": "loading",
        "stream_event": {"type": "tool_call", "message_id": "m1", "tool_call_id": "tc-1", "name": "ls", "args": {"path": "/tmp"}, "index": 0},
    }
    import json as json_mod

    items_form = _envelope("messages", {"items": [json_mod.dumps(chunk)]})
    single_form = dict(_envelope("messages", {"chunk": chunk}), seq="2-messages")  # 单块形式
    events = [items_form, single_form]
    monkeypatch.setattr(service, "list_run_stream_events", lambda *a, **k: _async_return(events))
    await svc.project_pending_events("t1")
    calls = [row for row in svc.events.rows if row.event_type == "tool/call"]
    assert len(calls) == 2
    assert calls[0].payload["tool_call"] == {"name": "ls", "id": "tc-1", "arguments": {"path": "/tmp"}}
    assert calls[0].source_key.endswith(":call:tc-1")
