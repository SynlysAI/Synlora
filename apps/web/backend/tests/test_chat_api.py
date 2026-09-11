"""SSE 对话端点端到端测试（脚本化 mock provider）。

说明：httpx ASGITransport 会把响应体整体缓冲（app 跑完才返回 Response），
无法从客户端侧真正"中途断开"SSE，因此：
- cancel/并发限制测试用后台 task 发消息 + 轮询 DB 拿 run_id 再操作；
- 断连不 cancel 的语义在 service 层验证（不给队列任何消费者，run 仍完成后落盘）。
"""
import asyncio
import json
from typing import ClassVar

import pytest
from synlys_harness import (
    ModelProviderConfig, ReasoningDelta, TextDelta, ToolCallChunk, Usage,
)

PROVIDER_BODY = {
    "name": "chat-mock",
    "base_url": "https://api.chat-mock.local/v1",
    "api_key": "sk-chat",
    "model_id": "gpt-chat",
    "enabled": True,
}


class FakeBackend:
    """脚本化后端：每次 stream 调用按剧本顺序弹出一组事件，并记录投喂的 messages。"""

    script: ClassVar[list] = []
    received: ClassVar[list] = []
    providers: ClassVar[list] = []

    def __init__(self, provider):
        """记录 provider。"""
        self.provider = provider
        FakeBackend.providers.append(provider)

    async def stream(self, messages, tools=None):
        """按剧本产出；记录本轮 messages（多轮记忆断言用）。"""
        FakeBackend.received.append(list(messages))
        for ev in FakeBackend.script.pop(0):
            yield ev


class SlowFakeBackend:
    """慢速后端：逐块延时产出文本增量，为 cancel/并发测试留时间窗口。"""

    deltas: ClassVar[int] = 40
    interval: ClassVar[float] = 0.05

    def __init__(self, provider):
        """记录 provider。"""
        self.provider = provider

    async def stream(self, messages, tools=None):
        """逐块延时产出。"""
        for i in range(SlowFakeBackend.deltas):
            yield TextDelta(text=f"块{i} ")
            await asyncio.sleep(SlowFakeBackend.interval)


class DelayedFakeBackend(FakeBackend):
    """延迟启动后端：首事件前等待，为同会话并发请求制造重叠窗口。"""

    delay: ClassVar[float] = 0.05

    async def stream(self, messages, tools=None):
        """先等待再按剧本产出（历史快照 seed 与事件写入的并发重叠窗口）。"""
        await asyncio.sleep(DelayedFakeBackend.delay)
        async for ev in super().stream(messages, tools):
            yield ev


@pytest.fixture(autouse=True)
def _reset_scripts():
    """每个测试前后重置脚本与记录，避免跨测试残留。"""
    FakeBackend.script = []
    FakeBackend.received = []
    FakeBackend.providers = []
    yield
    FakeBackend.script = []
    FakeBackend.received = []
    FakeBackend.providers = []


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析为 (event, payload) 列表。

    Args:
        text: text/event-stream 响应体。

    Returns:
        [(事件类型, data JSON 反序列化结果), ...]。
    """
    out: list[tuple[str, dict]] = []
    cur_event: str | None = None
    cur_data: str | None = None
    for line in text.splitlines():
        if not line.strip():
            if cur_event is not None or cur_data is not None:
                out.append((cur_event or "message", json.loads(cur_data or "{}")))
                cur_event, cur_data = None, None
        elif line.startswith("event:"):
            cur_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            cur_data = line[len("data:"):].strip()
    if cur_event is not None or cur_data is not None:
        out.append((cur_event or "message", json.loads(cur_data or "{}")))
    return out


async def _make_provider(client, headers, **overrides) -> dict:
    """建 provider，返回响应 JSON。

    Args:
        client: httpx 异步客户端。
        headers: 请求头（管理员）。
        overrides: 覆盖 PROVIDER_BODY 的字段（如 name/model_id 区分多 provider）。

    Returns:
        创建成功的 provider 文档。
    """
    body = {**PROVIDER_BODY, **overrides}
    r = await client.post("/api/v1/models", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _bind_provider_to_asst_data(client, admin_headers) -> dict:
    """建 provider 并关联到种子助手 asst-data。

    Args:
        client: httpx 异步客户端。
        admin_headers: 管理员请求头。

    Returns:
        provider 文档。
    """
    provider = await _make_provider(client, admin_headers)
    r = await client.patch("/api/v1/assistants/asst-data", headers=admin_headers,
                           json={"model_provider_id": provider["_id"]})
    assert r.status_code == 200, r.text
    return provider


async def _make_session(client, headers, assistant_id: str = "asst-data") -> str:
    """建会话，返回会话 id。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        assistant_id: 关联助手 id。

    Returns:
        会话 _id。
    """
    r = await client.post("/api/v1/sessions", headers=headers,
                          json={"assistant_id": assistant_id})
    assert r.status_code == 201, r.text
    return r.json()["_id"]


async def _wait_for_running_runs(app, count: int, timeout_s: float = 5.0) -> list[str]:
    """轮询 DB 直到出现 count 个 running 状态的 run。

    Args:
        app: 已初始化的 FastAPI 实例。
        count: 期望的 running run 数。
        timeout_s: 轮询超时。

    Returns:
        running run 的 _id 列表。
    """
    for _ in range(int(timeout_s / 0.02)):
        runs = await app.state.store.list("runs", filters={"status": "running"})
        if len(runs) >= count:
            return [r["_id"] for r in runs]
        await asyncio.sleep(0.02)
    raise AssertionError(f"{timeout_s}s 内未等到 {count} 个 running run")


# ---------- 完整工具链路（HTTP + SSE + python.run 子进程 + 持久化） ----------


async def test_full_tool_chain(app, client, admin_headers, monkeypatch):
    """工具全链路：SSE 事件序列完整，python.run 真实执行，JSONL/DB 副本/自动标题齐备。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [
        [ToolCallChunk(id="c1", name="python.run", arguments={"code": "print(6*7)"})],
        [TextDelta(text="答案是 42"), Usage(prompt_tokens=10, completion_tokens=5)],
    ]
    sid = await _make_session(client, admin_headers)

    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "算一下 6*7"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    assert types == [
        "turn/start", "user/message", "tool/call", "tool/result",
        "llm/delta", "assistant/message", "turn/end",
    ]
    tool_result = dict(events[types.index("tool/result")][1]["payload"])
    assert tool_result["name"] == "python.run"
    assert tool_result["ok"] is True
    assert "42" in tool_result["content"]
    assert events[-2][1]["payload"]["content"] == "答案是 42"

    # JSONL 回放源：瞬态 llm/delta 不落盘，行数 = 结构事件数（7 - 1）
    jsonl = app.state.settings.data_root / "sessions" / sid / "events.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(events) - 1 == 6
    assert json.loads(lines[0])["type"] == "turn/start"

    # DB 副本：不含瞬态 delta（seq 4 缺位成洞），after_seq 增量过滤正确
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    assert r.status_code == 200
    replayed = r.json()
    assert [e["seq"] for e in replayed] == [0, 1, 2, 3, 5, 6]
    assert [e["type"] for e in replayed] == [t for t in types if t != "llm/delta"]
    r = await client.get(f"/api/v1/sessions/{sid}/events?after_seq=5",
                         headers=admin_headers)
    assert [e["seq"] for e in r.json()] == [6]

    # 首条消息自动生成标题 + message_count 自增
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["message_count"] == 1
    assert mine["title"] == "算一下 6*7"

    run = await app.state.store.get("runs", events[0][1]["payload"]["run_id"])
    assert run["status"] == "completed"


async def test_plain_text_chat(app, client, admin_headers, monkeypatch):
    """纯文本对话：无工具事件，assistant/message 与 turn/end 收尾。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="你好，我是助手"), Usage()]]
    sid = await _make_session(client, admin_headers)

    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "你好"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    assert types == ["turn/start", "user/message", "llm/delta", "assistant/message", "turn/end"]
    assert "tool/call" not in types and "tool/result" not in types
    assert events[-2][1]["payload"]["content"] == "你好，我是助手"


# ---------- 瞬态事件不落盘（流式性能：每 token 一次 fsync 拖慢流式） ----------


async def test_transient_deltas_streamed_but_not_persisted(
        app, client, admin_headers, monkeypatch):
    """瞬态 delta：SSE 全量收到 llm/delta，DB/JSONL 只落结构事件（seq 有洞）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[
        TextDelta(text="你"), TextDelta(text="好"), TextDelta(text="呀"), Usage(),
    ]]
    sid = await _make_session(client, admin_headers)

    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "你好"})
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    # SSE 不过滤：3 个 llm/delta 全部推送
    assert types == ["turn/start", "user/message",
                     "llm/delta", "llm/delta", "llm/delta",
                     "assistant/message", "turn/end"]

    # DB 回放：不含 llm/delta 但含 assistant/message；delta 占号不落盘 → seq 有洞
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["type"] for e in replayed] == [
        "turn/start", "user/message", "assistant/message", "turn/end",
    ]
    assert [e["seq"] for e in replayed] == [0, 1, 5, 6]  # seq 2/3/4 被 3 个 delta 占用

    # JSONL 行数 = 结构事件数（4），同样不含瞬态
    jsonl = app.state.settings.data_root / "sessions" / sid / "events.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert all(json.loads(line)["type"] != "llm/delta" for line in lines)


async def test_reasoning_streamed_and_replayed(app, client, admin_headers, monkeypatch):
    """思考链路：SSE 收 reasoning/delta 与 assistant/reasoning 定稿；回放含定稿不含瞬态。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[
        ReasoningDelta(text="先想"), ReasoningDelta(text="一想"),
        TextDelta(text="答"), Usage(prompt_tokens=7, completion_tokens=4),
    ]]
    sid = await _make_session(client, admin_headers)

    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "想想再答"})
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    assert types == [
        "turn/start", "user/message",
        "reasoning/delta", "reasoning/delta", "llm/delta",
        "assistant/reasoning", "assistant/message", "turn/end",
    ]
    idx = types.index("assistant/reasoning")
    assert events[idx][1]["payload"]["content"] == "先想一想"
    # turn/end payload 透传 usage
    assert events[-1][1]["payload"]["usage"] == {
        "prompt_tokens": 7, "completion_tokens": 4,
    }

    # 回放：定稿 assistant/reasoning 在场、瞬态 reasoning/delta 不落盘
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["type"] for e in replayed] == [
        "turn/start", "user/message", "assistant/reasoning",
        "assistant/message", "turn/end",
    ]
    assert [e["seq"] for e in replayed] == [0, 1, 5, 6, 7]  # seq 2/3/4 被瞬态占用


# ---------- 多轮会话：事件 seq 连续 + 对话记忆 ----------


async def test_multi_turn_events_persist_and_context(app, client, admin_headers, monkeypatch):
    """同会话两轮消息：事件 seq 全程连续、DB 无碰撞、第二轮 LLM 收到第一轮上下文。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [
        [TextDelta(text="第一答"), Usage()],
        [TextDelta(text="第二答"), Usage()],
    ]
    sid = await _make_session(client, admin_headers)

    r1 = await client.post(f"/api/v1/sessions/{sid}/messages",
                           headers=admin_headers, json={"text": "第一问"})
    assert r1.status_code == 200, r1.text
    r2 = await client.post(f"/api/v1/sessions/{sid}/messages",
                           headers=admin_headers, json={"text": "第二问"})
    assert r2.status_code == 200, r2.text

    # DB 副本：两轮 8 条结构事件（每轮 1 个 llm/delta 瞬态不落盘），seq 有洞且无重复
    # （碰撞写入会被静默吞掉 → 重复即红；洞位是瞬态占号的预期产物）
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["seq"] for e in replayed] == [0, 1, 3, 4, 5, 6, 8, 9]
    user_texts = [e["payload"]["text"] for e in replayed
                  if e["type"] == "user/message"]
    assert user_texts == ["第一问", "第二问"]

    # JSONL 回放源同样只落结构事件（seq 与 DB 一致）
    jsonl = app.state.settings.data_root / "sessions" / sid / "events.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [0, 1, 3, 4, 5, 6, 8, 9]

    # 第二轮 LLM 收到第一轮的 user/assistant 消息（对话记忆存在）
    msgs = FakeBackend.received[-1]
    pairs = [(m.role.value, m.content) for m in msgs]
    assert ("user", "第一问") in pairs
    assert ("assistant", "第一答") in pairs
    assert ("user", "第二问") in pairs


# ---------- 同会话并发互斥（Critical） ----------


async def test_same_session_concurrent_rejected(app, client, admin_headers, monkeypatch):
    """同会话 2 条并发消息：会话级互斥拒绝第二条（429），首条事件完整落盘。

    修复前：两条都被放行（用户级上限按用户计数且查 DB 有竞态），各自 seed
    同一份（空）历史从相同 seq 起号，DB _id=f"{sid}:{seq}" 碰撞写入被静默
    吞 → 两条都 200 且事件错乱/丢失。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend",
                        DelayedFakeBackend)
    FakeBackend.script = [
        [TextDelta(text="答一"), Usage()],
        [TextDelta(text="答二"), Usage()],
    ]
    sid = await _make_session(client, admin_headers)

    r1, r2 = await asyncio.gather(
        client.post(f"/api/v1/sessions/{sid}/messages",
                    headers=admin_headers, json={"text": "第一条"}),
        client.post(f"/api/v1/sessions/{sid}/messages",
                    headers=admin_headers, json={"text": "第二条"}),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 429]
    ok = r1 if r1.status_code == 200 else r2
    ok_events = parse_sse(ok.text)
    assert ok_events[-1][0] == "turn/end"
    # gather 到达顺序不定，胜者从 200 响应自身提取（被拒方文本不得出现）
    winner_text = next(p["payload"]["text"] for t, p in ok_events
                       if t == "user/message")

    # 胜者完整落盘：GET events 的 seq 有洞（瞬态 delta 占号）无重复，仅一条 user/message
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["seq"] for e in replayed] == [0, 1, 3, 4]
    user_texts = [e["payload"]["text"] for e in replayed
                  if e["type"] == "user/message"]
    assert user_texts == [winner_text]

    # 被拒消息零副作用：不计数、不生成标题
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["message_count"] == 1
    assert mine["title"] == winner_text

    # 首条 run 结束后会话占位被摘除，同会话可正常再发
    r3 = await client.post(f"/api/v1/sessions/{sid}/messages",
                           headers=admin_headers, json={"text": "第三条"})
    assert r3.status_code == 200, r3.text
    assert parse_sse(r3.text)[-1][0] == "turn/end"


# ---------- message_count 并发原子性 ----------


async def test_concurrent_messages_count_atomic(app, client, admin_headers, monkeypatch):
    """不同会话 2 条并发消息：各自 message_count 正确为 1、标题正确（并发互不串扰）。

    同会话并发已被会话级互斥拒绝（见 test_same_session_concurrent_rejected），
    同会话并发 bump 互覆盖的场景此后结构上不可能发生，原子性验证改为并发
    路径下各会话计数不丢失、不串写。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [
        [TextDelta(text="答一"), Usage()],
        [TextDelta(text="答二"), Usage()],
    ]
    sid1 = await _make_session(client, admin_headers)
    sid2 = await _make_session(client, admin_headers)

    r1, r2 = await asyncio.gather(
        client.post(f"/api/v1/sessions/{sid1}/messages",
                    headers=admin_headers, json={"text": "第一条"}),
        client.post(f"/api/v1/sessions/{sid2}/messages",
                    headers=admin_headers, json={"text": "第二条"}),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    # 两轮 SSE 均完整收尾
    assert parse_sse(r1.text)[-1][0] == "turn/end"
    assert parse_sse(r2.text)[-1][0] == "turn/end"

    # 并发下各会话计数不丢失、不串写（互覆盖/串写时至少一个 count 错误）
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    by_id = {s["_id"]: s for s in r.json()}
    assert by_id[sid1]["message_count"] == 1
    assert by_id[sid2]["message_count"] == 1
    assert by_id[sid1]["title"] == "第一条"
    assert by_id[sid2]["title"] == "第二条"

    # 两个 run 均完成落盘
    runs = await app.state.store.list("runs", filters={})
    assert len(runs) == 2
    assert all(run["status"] == "completed" for run in runs)


# ---------- cancel 与并发限制 ----------


async def test_cancel_run(app, client, admin_headers, monkeypatch):
    """用户显式 cancel：SSE 以 turn/aborted(reason=user_cancel) 收尾，runs 状态 aborted。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", SlowFakeBackend)
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "慢慢说"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]

    r = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    assert events[-1][0] == "turn/aborted"
    assert events[-1][1]["payload"]["reason"] == "user_cancel"
    run = await app.state.store.get("runs", run_id)
    assert run["status"] == "aborted"


async def test_cancel_missing_run_404(client, admin_headers):
    """取消不存在的 run 应 404。"""
    r = await client.post("/api/v1/runs/nope/cancel", headers=admin_headers)
    assert r.status_code == 404


async def test_concurrency_limit_429(app, client, admin_headers, monkeypatch):
    """每用户并发上限 2：两个长 run 挂起中第三个 POST → 429；取消后收尾正常。

    两个长 run 分属不同会话（同会话并发已被会话级互斥先行拒绝），
    第三条发往全新会话，确保命中的是用户级上限而非会话互斥。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", SlowFakeBackend)
    sids = [await _make_session(client, admin_headers) for _ in range(2)]

    tasks = [
        asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                        headers=admin_headers,
                                        json={"text": f"长任务{i}"}))
        for i, sid in enumerate(sids)
    ]
    run_ids = await _wait_for_running_runs(app, 2)

    sid3 = await _make_session(client, admin_headers)
    r = await client.post(f"/api/v1/sessions/{sid3}/messages",
                          headers=admin_headers, json={"text": "第三个"})
    assert r.status_code == 429

    for rid in run_ids:
        cr = await client.post(f"/api/v1/runs/{rid}/cancel", headers=admin_headers)
        assert cr.json()["ok"] is True
    for t in tasks:
        resp = await asyncio.wait_for(t, timeout=15)
        events = parse_sse(resp.text)
        assert events[-1][0] == "turn/aborted"


# ---------- 归属隔离与 provider 校验 ----------


async def test_session_isolation(client, admin_headers, user_headers):
    """user 的会话对其他用户（含 admin）不可见、不可操作（统一 404）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    sid = await _make_session(client, user_headers)

    r = await client.get("/api/v1/sessions", headers=admin_headers)
    assert all(s["_id"] != sid for s in r.json())
    r = await client.get("/api/v1/sessions", headers=user_headers)
    assert [s["_id"] for s in r.json()] == [sid]

    assert (await client.get(f"/api/v1/sessions/{sid}/events",
                             headers=admin_headers)).status_code == 404
    assert (await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                               json={"title": "x"})).status_code == 404
    assert (await client.delete(f"/api/v1/sessions/{sid}",
                                headers=admin_headers)).status_code == 404
    assert (await client.post(f"/api/v1/sessions/{sid}/messages",
                              headers=admin_headers,
                              json={"text": "hi"})).status_code == 404


async def test_provider_missing_or_disabled_422(client, admin_headers, user_headers):
    """助手未关联 provider 或 provider 已停用 → 422。"""
    sid = await _make_session(client, user_headers)  # asst-data 未关联 provider
    r = await client.post(f"/api/v1/sessions/{sid}/messages",
                          headers=user_headers, json={"text": "你好"})
    assert r.status_code == 422

    provider = await _bind_provider_to_asst_data(client, admin_headers)
    await client.patch(f"/api/v1/models/{provider['_id']}", headers=admin_headers,
                       json={"enabled": False})
    r = await client.post(f"/api/v1/sessions/{sid}/messages",
                          headers=user_headers, json={"text": "你好"})
    assert r.status_code == 422


# ---------- 断连不 cancel（service 层验证 _drive 独立完成） ----------


async def test_run_completes_without_sse_consumer(app, client, admin_headers, monkeypatch):
    """无 SSE 消费者（等价断连）：_drive 后台独立跑完并落盘，events 可回放补齐。"""
    provider = await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="后台完成"), Usage()]]
    sid = await _make_session(client, admin_headers)

    decrypted = await app.state.provider_repo.get_decrypted(provider["_id"])
    cfg = ModelProviderConfig(
        name=decrypted["name"], base_url=decrypted["base_url"],
        api_key=decrypted["api_key"], model_id=decrypted["model_id"],
    )
    assistant = await app.state.assistant_repo.get("asst-data")
    user = {"sub": "u-admin", "username": "tester-admin", "role": "admin"}
    run_id = await app.state.agent_service.chat(sid, user, assistant, cfg, "后台跑")

    types: list[str] = []
    for _ in range(100):
        r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
        types = [e["type"] for e in r.json()]
        if "turn/end" in types:
            break
        await asyncio.sleep(0.05)
    assert "turn/end" in types
    run = await app.state.store.get("runs", run_id)
    assert run["status"] == "completed"


# ---------- chat() 异常路径清理（Important） ----------


async def test_chat_init_failure_releases_session_slot(app, client, admin_headers, monkeypatch):
    """chat() 注册后初始化失败（DB 历史读取抛错）：注册回滚，同会话可立即重发。

    修复前：_runs/会话占位残留失败 run，条目泄漏、哨兵永不投递，
    同会话后续消息被残留占位卡成 429。
    """
    provider = await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="恢复"), Usage()]]
    sid = await _make_session(client, admin_headers)

    svc = app.state.agent_service
    decrypted = await app.state.provider_repo.get_decrypted(provider["_id"])
    cfg = ModelProviderConfig(
        name=decrypted["name"], base_url=decrypted["base_url"],
        api_key=decrypted["api_key"], model_id=decrypted["model_id"],
    )
    assistant = await app.state.assistant_repo.get("asst-data")
    user = {"sub": "u-admin", "username": "tester-admin", "role": "admin"}

    async def _boom(session_id):
        """模拟 DB 历史读取异常。"""
        raise ValueError("DB 历史空洞")

    real_list_events = app.state.event_repo.list_events
    monkeypatch.setattr(app.state.event_repo, "list_events", _boom)
    with pytest.raises(ValueError):
        await svc.chat(sid, user, assistant, cfg, "会失败")
    assert not svc._runs  # 注册表无残留

    monkeypatch.setattr(app.state.event_repo, "list_events", real_list_events)
    run_id = await svc.chat(sid, user, assistant, cfg, "重发")  # 不被残留占位卡成 429
    run = None
    for _ in range(100):
        run = await app.state.store.get("runs", run_id)
        if run and run["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert run is not None and run["status"] == "completed"


# ---------- 会话 CRUD ----------


async def test_sessions_crud(app, client, admin_headers, monkeypatch):
    """会话 CRUD：创建校验助手、列表按 updated_at 倒序、PATCH、DELETE 清理 events+JSONL。"""
    r = await client.post("/api/v1/sessions", headers=admin_headers,
                          json={"assistant_id": "nope"})
    assert r.status_code == 404

    s1 = await _make_session(client, admin_headers)
    await asyncio.sleep(0.01)
    s2 = await _make_session(client, admin_headers)
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    ids = [s["_id"] for s in r.json()]
    assert ids[0] == s2
    assert s1 in ids

    r = await client.patch(f"/api/v1/sessions/{s1}", headers=admin_headers,
                           json={"title": "改名", "archived": True})
    assert r.status_code == 200
    assert r.json()["title"] == "改名"
    assert r.json()["archived"] is True

    # 对话一轮产生 events + JSONL，DELETE 后全部清理
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    sid = await _make_session(client, admin_headers)
    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "在吗"})
    assert resp.status_code == 200
    jsonl = app.state.settings.data_root / "sessions" / sid / "events.jsonl"
    assert jsonl.exists()

    r = await client.delete(f"/api/v1/sessions/{sid}", headers=admin_headers)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not jsonl.exists()
    assert (await client.get(f"/api/v1/sessions/{sid}/events",
                             headers=admin_headers)).status_code == 404
    events = await app.state.store.list("events", filters={"session_id": sid})
    assert events == []


async def test_message_empty_text_422(client, admin_headers):
    """空白消息文本应 422。"""
    sid = await _make_session(client, admin_headers)
    r = await client.post(f"/api/v1/sessions/{sid}/messages",
                          headers=admin_headers, json={"text": "   "})
    assert r.status_code == 422


# ---------- 会话级模型选择（session.model_provider_id 覆盖助手默认） ----------


async def _chat_once(client, headers, sid: str, text: str = "你好") -> None:
    """发一条消息并断言 SSE 正常收尾。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        sid: 会话 id。
        text: 消息文本。
    """
    r = await client.post(f"/api/v1/sessions/{sid}/messages",
                          headers=headers, json={"text": text})
    assert r.status_code == 200, r.text
    assert parse_sse(r.text)[-1][0] == "turn/end"


async def test_session_model_override_used_in_chat(app, client, admin_headers, monkeypatch):
    """建会话带 model_provider_id 覆盖：对话走覆盖的 provider 而非助手绑定。"""
    await _bind_provider_to_asst_data(client, admin_headers)  # 助手绑定 chat-mock
    override = await _make_provider(client, admin_headers,
                                    name="override-mock", model_id="gpt-override")
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="覆盖生效"), Usage()]]

    r = await client.post("/api/v1/sessions", headers=admin_headers,
                          json={"assistant_id": "asst-data",
                                "model_provider_id": override["_id"]})
    assert r.status_code == 201, r.text
    assert r.json()["model_provider_id"] == override["_id"]
    sid = r.json()["_id"]

    await _chat_once(client, admin_headers, sid)
    assert len(FakeBackend.providers) == 1
    assert FakeBackend.providers[0].name == "override-mock"
    assert FakeBackend.providers[0].model_id == "gpt-override"

    # 列表回读：覆盖字段随会话文档返回
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["model_provider_id"] == override["_id"]


async def test_session_model_patch_switch_and_restore(app, client, admin_headers, monkeypatch):
    """PATCH 切换覆盖 provider 生效；PATCH null 恢复跟随助手绑定。"""
    bound = await _bind_provider_to_asst_data(client, admin_headers)
    alt = await _make_provider(client, admin_headers,
                               name="alt-mock", model_id="gpt-alt")
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]] * 3

    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid, "默认")
    assert FakeBackend.providers[-1].name == "chat-mock"

    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"model_provider_id": alt["_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["model_provider_id"] == alt["_id"]
    await _chat_once(client, admin_headers, sid, "切换")
    assert FakeBackend.providers[-1].name == "alt-mock"

    # 传 null 显式恢复助手默认（区别于"未提供该字段"）
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"model_provider_id": None, "title": "顺带改名"})
    assert r.status_code == 200, r.text
    assert r.json()["model_provider_id"] is None
    assert r.json()["title"] == "顺带改名"
    await _chat_once(client, admin_headers, sid, "恢复")
    assert FakeBackend.providers[-1].name == "chat-mock"
    assert len(FakeBackend.providers) == 3

    # 未提供字段的 PATCH 不动覆盖值
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"model_provider_id": bound["_id"]})
    await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                       json={"archived": False})
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["model_provider_id"] == bound["_id"]


async def test_session_model_override_disabled_after_set_422(
        app, client, admin_headers, user_headers, monkeypatch):
    """覆盖的 provider 事后被停用：对话 422（会话级覆盖同样受 enabled 约束）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    override = await _make_provider(client, admin_headers, name="will-disable",
                                    model_id="gpt-wd")
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    r = await client.post("/api/v1/sessions", headers=user_headers,
                          json={"assistant_id": "asst-data",
                                "model_provider_id": override["_id"]})
    sid = r.json()["_id"]

    await client.patch(f"/api/v1/models/{override['_id']}", headers=admin_headers,
                       json={"enabled": False})
    r = await client.post(f"/api/v1/sessions/{sid}/messages",
                          headers=user_headers, json={"text": "你好"})
    assert r.status_code == 422


async def test_session_model_invalid_422(client, admin_headers, user_headers):
    """建会话/PATCH 传不存在或已停用的 provider id → 422。"""
    disabled = await _make_provider(client, admin_headers, name="disabled-mock",
                                    model_id="gpt-off", enabled=False)

    r = await client.post("/api/v1/sessions", headers=user_headers,
                          json={"assistant_id": "asst-data",
                                "model_provider_id": "nope"})
    assert r.status_code == 422
    r = await client.post("/api/v1/sessions", headers=user_headers,
                          json={"assistant_id": "asst-data",
                                "model_provider_id": disabled["_id"]})
    assert r.status_code == 422

    sid = await _make_session(client, user_headers)
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=user_headers,
                           json={"model_provider_id": "nope"})
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=user_headers,
                           json={"model_provider_id": disabled["_id"]})
    assert r.status_code == 422
    # 校验失败不产生副作用（覆盖值保持未设置）
    r = await client.get("/api/v1/sessions", headers=user_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["model_provider_id"] is None
