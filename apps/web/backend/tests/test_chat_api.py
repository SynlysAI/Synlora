"""SSE 对话端点端到端测试（脚本化 mock provider）。

说明：httpx ASGITransport 会把响应体整体缓冲（app 跑完才返回 Response），
无法从客户端侧真正"中途断开"SSE，因此：
- cancel/并发限制测试用后台 task 发消息 + 轮询 DB 拿 run_id 再操作；
- 断连不 cancel 的语义在 service 层验证（不给队列任何消费者，run 仍完成后落盘）。
"""
import asyncio
import json
from pathlib import Path
from typing import ClassVar

import pytest
from synlys_harness import (
    ModelProviderConfig, ReasoningDelta, TextDelta, ToolCallChunk, Usage,
)

from app.services import agent_service as agent_service_mod

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
    run_id = await app.state.agent_service.chat(
        sid, user, assistant, cfg, "后台跑",
        workspace_root=await _workspace_root(app, "u-admin"))

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
        await svc.chat(sid, user, assistant, cfg, "会失败",
                       workspace_root=await _workspace_root(app, "u-admin"))
    assert not svc._runs  # 注册表无残留

    monkeypatch.setattr(app.state.event_repo, "list_events", real_list_events)
    # 不被残留占位卡成 429
    run_id = await svc.chat(sid, user, assistant, cfg, "重发",
                            workspace_root=await _workspace_root(app, "u-admin"))
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


# ---------- 会话绑定项目（agent 跑在项目目录而非用户目录） ----------


async def _make_project(client, headers, name: str = "p1") -> str:
    """建项目，返回项目 id。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        name: 项目名。

    Returns:
        新建项目 _id。
    """
    r = await client.post("/api/v1/projects", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["_id"]


async def _workspace_root(app, user_id: str) -> Path:
    """取该用户当前项目的根目录（测试直连 chat 时显式传 workspace_root 用）。

    Args:
        app: 已初始化 app（取 project_service）。
        user_id: 用户 sub。

    Returns:
        项目根目录 Path（files/output/tmp 已就绪）。
    """
    project = await app.state.project_service.resolve_active_project(user_id, None)
    return app.state.project_service.root_for(project)


async def test_session_binds_project_and_uses_project_workspace(client, user_headers):
    """建会话带 project_id：落库并原样返回（会话挂到指定项目）。"""
    pid = (await client.post("/api/v1/projects", json={"name": "p1"},
                             headers=user_headers)).json()["_id"]
    r = await client.post("/api/v1/sessions",
                          json={"assistant_id": "asst-research", "project_id": pid},
                          headers=user_headers)
    assert r.status_code == 201
    assert r.json()["project_id"] == pid

    # 列表回读同样带 project_id（前端据此渲染会话所属项目）
    r = await client.get("/api/v1/sessions", headers=user_headers)
    assert [s["project_id"] for s in r.json()] == [pid]


async def test_session_without_project_id_compatible(app, client, admin_headers,
                                                     user_headers, monkeypatch):
    """不传 project_id：会话照常创建（project_id 为 None），发消息回落默认项目仍正常收尾。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]

    r = await client.post("/api/v1/sessions", headers=user_headers,
                          json={"assistant_id": "asst-data"})
    assert r.status_code == 201, r.text
    assert r.json().get("project_id") is None

    await _chat_once(client, user_headers, r.json()["_id"])
    # 老会话发消息时自动补种了默认项目（后续会话有项目可挂）
    projects = await app.state.project_service.list_projects("u-user")
    assert len(projects) == 1


async def test_agent_workspace_is_project_root(app, client, admin_headers,
                                               user_headers, monkeypatch):
    """agent 实际拿到的工作区根目录 = 会话所属项目目录（不是用户目录）。

    观察方式：把 agent_service 模块内的 RunSession 换成记录 kwargs 的包装，
    再交给真实 RunSession —— 断言 workspace_root 这一实参，而非间接推断。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="一"), Usage()], [TextDelta(text="二"), Usage()]]

    captured: list = []
    real_run_session = agent_service_mod.RunSession

    def _capturing_run_session(*args, **kwargs):
        """记录 workspace_root 实参后转交真实 RunSession。

        Args:
            args: RunSession 位置参数。
            kwargs: RunSession 关键字参数（含 workspace_root）。

        Returns:
            真实 RunSession 实例。
        """
        captured.append(kwargs.get("workspace_root"))
        return real_run_session(*args, **kwargs)

    monkeypatch.setattr(agent_service_mod, "RunSession", _capturing_run_session)
    user_dir = app.state.settings.data_root / "workspaces" / "u-user"

    # 1) 显式绑定项目 → 工作区根 = 该项目目录
    pid = await _make_project(client, user_headers, "绑项目")
    sid = (await client.post("/api/v1/sessions", headers=user_headers,
                             json={"assistant_id": "asst-data",
                                   "project_id": pid})).json()["_id"]
    await _chat_once(client, user_headers, sid)
    project = await app.state.project_service.get("u-user", pid)
    assert captured[-1] == app.state.project_service.root_for(project)
    assert captured[-1] != user_dir
    assert captured[-1].parent == user_dir
    assert (captured[-1] / "tmp").is_dir()  # python.run 的 cwd 落在项目内

    # 2) 未绑定项目（老会话/项目已删）→ 回落第一个可用项目，仍不是用户目录
    sid2 = (await client.post("/api/v1/sessions", headers=user_headers,
                              json={"assistant_id": "asst-data"})).json()["_id"]
    await _chat_once(client, user_headers, sid2, "第二问")
    projects = await app.state.project_service.list_projects("u-user")
    assert captured[-1] == app.state.project_service.root_for(projects[0])
    assert captured[-1] != user_dir


# ---------- 平台提示词装配 / 技能渐进披露 ----------


def _capture_run_args(monkeypatch) -> list[dict]:
    """包住 agent_service.RunSession，记录每次构造的实参（观察实际装配结果）。

    Args:
        monkeypatch: pytest monkeypatch 夹具。

    Returns:
        逐轮累积的 kwargs 列表（新一轮对话 append 一项）。
    """
    captured: list[dict] = []
    real_run_session = agent_service_mod.RunSession

    def _wrapper(*args, **kwargs):
        """记录 kwargs 后转交真实 RunSession。

        Args:
            args: RunSession 位置参数。
            kwargs: RunSession 关键字参数（含 config/context_extra/workspace_root）。

        Returns:
            真实 RunSession 实例。
        """
        captured.append(kwargs)
        return real_run_session(*args, **kwargs)

    monkeypatch.setattr(agent_service_mod, "RunSession", _wrapper)
    return captured


async def test_system_prompt_contains_platform_sections_and_persona(
        app, client, admin_headers, monkeypatch):
    """system prompt = 平台默认段（SOUL/AGENT/工作区）+ 末尾追加专家 persona。

    观察方式：包 agent_service 模块内的 RunSession，断言其 config.system_prompt。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    prompt = captured[-1]["config"].system_prompt
    # 平台默认段按 priority 拼入
    assert "# 灵魂" in prompt and "核心原则" in prompt
    assert "# 工作方式" in prompt
    assert "# 工作区" in prompt
    # {{workspace}} 已替换为工作区绝对路径（as_posix 形式）
    workspace = captured[-1]["workspace_root"]
    assert workspace.as_posix() in prompt
    assert "{{workspace}}" not in prompt
    # 专家 persona 追加在末尾
    persona = (await app.state.assistant_repo.get("asst-data"))["system_prompt"]
    assert persona in prompt
    assert prompt.rstrip().endswith(persona.strip())


async def test_skill_index_injected_and_tools_available(
        app, client, admin_headers, monkeypatch):
    """技能索引（名+描述）进提示词、skill.list/skill.read 进工具表与工具上下文。

    同时守护渐进披露契约：技能正文绝不进 system prompt。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    config = captured[-1]["config"]
    prompt = config.system_prompt
    assert "# 技能" in prompt
    assert "`data-analysis`" in prompt
    assert "`pdf-extraction`" in prompt
    skill = next(s for s in app.state.skill_service.list_skills()
                 if s["name"] == "data-analysis")
    assert skill["description"] in prompt

    # 工具表：助手白名单 + 两个技能工具（无条件追加，去重后各一次）
    assert "skill.list" in config.tool_names
    assert "skill.read" in config.tool_names
    assert config.tool_names.count("skill.list") == 1
    assert config.tool_names.count("skill.read") == 1

    # 工具上下文：skill.read 按名取正文、skill.list 取描述
    extra = captured[-1]["context_extra"]
    assert extra["skills"]["data-analysis"] == skill["content"]
    assert extra["skill_meta"]["data-analysis"] == skill["description"]

    # 渐进披露：正文字符串不得出现在 system prompt 里（只有 skill.read 能取到）
    assert "## 决策规则" not in prompt
    assert skill["content"] not in prompt


async def test_requested_skills_filter_index(app, client, admin_headers, monkeypatch):
    """chat(requested_skills=...) 只装配选中技能（索引与工具上下文同步收窄）。"""
    provider = await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = await _make_session(client, admin_headers)

    decrypted = await app.state.provider_repo.get_decrypted(provider["_id"])
    cfg = ModelProviderConfig(
        name=decrypted["name"], base_url=decrypted["base_url"],
        api_key=decrypted["api_key"], model_id=decrypted["model_id"],
    )
    assistant = await app.state.assistant_repo.get("asst-data")
    user = {"sub": "u-admin", "username": "tester-admin", "role": "admin"}
    run_id = await app.state.agent_service.chat(
        sid, user, assistant, cfg, "选中技能",
        workspace_root=await _workspace_root(app, "u-admin"),
        requested_skills=["data-analysis"])

    prompt = captured[-1]["config"].system_prompt
    assert "`data-analysis`" in prompt
    assert "`pdf-extraction`" not in prompt
    assert list(captured[-1]["context_extra"]["skills"]) == ["data-analysis"]

    for _ in range(100):
        run = await app.state.store.get("runs", run_id)
        if run and run["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert run is not None and run["status"] == "completed"


async def test_concurrent_resolve_seeds_single_default_project(app):
    """并发首次解析项目只建一个默认项目，且两次解析是同一个项目（C-1 回归）。

    修复前：list_projects 与 create_project 各自加锁、两次加锁之间无原子性，
    并发首条消息（双击发送 / 双标签页 / 两条会话同时首条）会各自算出一个
    可用的目录名（默认项目、默认项目-2），磁盘上出现两个目录、库里两条记录。
    """
    svc = app.state.project_service
    a, b = await asyncio.gather(
        svc.resolve_active_project("u-first", None),
        svc.resolve_active_project("u-first", None),
    )
    assert a["_id"] == b["_id"]  # 不是"各建一条"

    projects = await svc.list_projects("u-first")
    assert len(projects) == 1
    assert projects[0]["name"] == "默认项目"        # 显示名仍是中文
    assert projects[0]["dir_name"] == "default"     # 目录名口径统一（M-2）
    user_dir = app.state.settings.data_root / "workspaces" / "u-first"
    assert [p.name for p in user_dir.iterdir()] == ["default"]


async def test_foreign_project_id_rejected_and_fallback_stays_own(
        app, client, admin_headers, user_headers, monkeypatch):
    """他人/不存在的 project_id：建会话 404；会话被塞他人 pid 时回落本人项目并写回。

    运行时靠回落兜住（不串到他人目录），但数据必须干净：脏 pid 既不落库（I-3），
    也不让下一轮继续漂移（回落后写回会话，I-2）。
    """
    admin_pid = await _make_project(client, admin_headers, "管理员项目")
    for pid in (admin_pid, "no-such-project"):
        r = await client.post("/api/v1/sessions", headers=user_headers,
                              json={"assistant_id": "asst-data", "project_id": pid})
        assert r.status_code == 404, r.text
    # 校验失败不产生副作用：该用户没有任何会话落库
    assert await app.state.store.list("sessions", filters={"user_id": "u-user"}) == []

    # 脏数据路径：直接把他人 pid 塞进会话文档，运行时必须回落到本人项目
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    my_pid = await _make_project(client, user_headers, "我的项目")
    sid = (await client.post("/api/v1/sessions", headers=user_headers,
                             json={"assistant_id": "asst-data"})).json()["_id"]
    await app.state.store.update("sessions", sid, {"project_id": admin_pid})

    captured: list = []
    real_run_session = agent_service_mod.RunSession

    def _capturing_run_session(*args, **kwargs):
        """记录 workspace_root 实参后转交真实 RunSession。

        Args:
            args: RunSession 位置参数。
            kwargs: RunSession 关键字参数（含 workspace_root）。

        Returns:
            真实 RunSession 实例。
        """
        captured.append(kwargs.get("workspace_root"))
        return real_run_session(*args, **kwargs)

    monkeypatch.setattr(agent_service_mod, "RunSession", _capturing_run_session)
    await _chat_once(client, user_headers, sid)

    assert captured[-1] == app.state.project_service.root_for(
        await app.state.project_service.get("u-user", my_pid))
    assert "u-admin" not in captured[-1].parts  # 没跑进他人目录
    # 回落后写回会话文档：后续轮次稳定命中本人项目
    doc = await app.state.store.get("sessions", sid)
    assert doc["project_id"] == my_pid


# ---------- 切换专家（PATCH assistant_id） ----------


async def test_session_patch_switch_assistant(app, client, admin_headers):
    """PATCH 切换专家：写回 assistant_id，列表回读即为新专家（只影响后续轮次）。"""
    sid = await _make_session(client, admin_headers, assistant_id="asst-research")
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"assistant_id": "asst-data"})
    assert r.status_code == 200, r.text
    assert r.json()["assistant_id"] == "asst-data"

    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["assistant_id"] == "asst-data"


async def test_session_patch_switch_assistant_404(app, client, admin_headers):
    """PATCH 传不存在的 assistant_id → 404，且会话文档未被改（无副作用）。"""
    sid = await _make_session(client, admin_headers, assistant_id="asst-research")
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"assistant_id": "asst-nope"})
    assert r.status_code == 404

    doc = await app.state.store.get("sessions", sid)
    assert doc["assistant_id"] == "asst-research"


async def test_session_patch_without_assistant_id_keeps_it(app, client, admin_headers):
    """不带 assistant_id 的 PATCH（只改 title）不影响 assistant_id。"""
    sid = await _make_session(client, admin_headers, assistant_id="asst-research")
    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"title": "只改名"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "只改名"
    assert r.json()["assistant_id"] == "asst-research"


async def test_session_patch_switch_assistant_used_next_turn(
        app, client, admin_headers, monkeypatch):
    """切换专家后下一轮 chat 用新助手（会话文档是每轮的事实源，不改历史事件）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = await _make_session(client, admin_headers, assistant_id="asst-research")

    r = await client.patch(f"/api/v1/sessions/{sid}", headers=admin_headers,
                           json={"assistant_id": "asst-data"})
    assert r.status_code == 200, r.text
    await _chat_once(client, admin_headers, sid)

    persona = (await app.state.assistant_repo.get("asst-data"))["system_prompt"]
    prompt = captured[-1]["config"].system_prompt
    assert persona in prompt


# ---------- 发消息透传本轮技能选择（skills → requested_skills） ----------


def _capture_chat_args(monkeypatch) -> list[dict]:
    """包住 AgentService.chat，记录每轮入参后转交真实实现。

    观察方式：直接 monkeypatch 类方法（sessions_api 经实例调用 → 落到类方法），
    断言 handler 透传的 requested_skills 实参，而非间接从提示词反推。

    Args:
        monkeypatch: pytest monkeypatch 夹具。

    Returns:
        逐轮累积的 kwargs 列表（每轮 append 一项）。
    """
    captured: list[dict] = []
    real_chat = agent_service_mod.AgentService.chat

    async def _wrapper(self, *args, **kwargs):
        """记录 kwargs 后转交真实 chat。

        Args:
            self: AgentService 实例。
            args: chat 位置参数。
            kwargs: chat 关键字参数（含 requested_skills）。

        Returns:
            真实 chat 返回的 run_id。
        """
        captured.append(kwargs)
        return await real_chat(self, *args, **kwargs)

    monkeypatch.setattr(agent_service_mod.AgentService, "chat", _wrapper)
    return captured


async def test_message_skills_passthrough(app, client, admin_headers, monkeypatch):
    """发消息 body 带 skills → requested_skills 原样透传给 AgentService.chat。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    sid = await _make_session(client, admin_headers)
    captured = _capture_chat_args(monkeypatch)

    r = await client.post(f"/api/v1/sessions/{sid}/messages", headers=admin_headers,
                          json={"text": "选技能", "skills": ["data-analysis"]})
    assert r.status_code == 200, r.text
    assert parse_sse(r.text)[-1][0] == "turn/end"
    assert captured[-1]["requested_skills"] == ["data-analysis"]


async def test_message_without_skills_defaults_none(app, client, admin_headers, monkeypatch):
    """不带 skills 发消息（老前端）→ requested_skills 为 None（向后兼容 = 全部技能）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    sid = await _make_session(client, admin_headers)
    captured = _capture_chat_args(monkeypatch)

    await _chat_once(client, admin_headers, sid, "不选技能")
    assert captured[-1]["requested_skills"] is None
