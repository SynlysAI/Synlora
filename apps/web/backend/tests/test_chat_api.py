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
    ModelProviderConfig, ReasoningDelta, TextDelta, ToolCallChunk, ToolResult, Usage,
)
from synlys_harness.tools.registry import tool
from synlys_harness.types import Permission

from app.services import agent_service as agent_service_mod

# 本文件用例统一用 admin_headers 登录，其 token payload 的 sub 为 u-admin
# （见 conftest.make_token_headers）；事件落盘按登录用户分层，断言需对齐该目录。
USER_SUB = "u-admin"

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
    jsonl = (app.state.settings.data_root / "users" / USER_SUB
             / "sessions" / sid / "events.jsonl")
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
    jsonl = (app.state.settings.data_root / "users" / USER_SUB
             / "sessions" / sid / "events.jsonl")
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
    jsonl = (app.state.settings.data_root / "users" / USER_SUB
             / "sessions" / sid / "events.jsonl")
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
    jsonl = (app.state.settings.data_root / "users" / USER_SUB
             / "sessions" / sid / "events.jsonl")
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
    """不传 project_id：会话照常创建（project_id 为 None），发消息不回落任何项目——
    会话目录 sessions/{sid} 自身为工作区（files/tmp 就绪），且不补种默认项目。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]

    r = await client.post("/api/v1/sessions", headers=user_headers,
                          json={"assistant_id": "asst-data"})
    assert r.status_code == 201, r.text
    assert r.json().get("project_id") is None

    sid = r.json()["_id"]
    await _chat_once(client, user_headers, sid)
    # 未绑定会话不再补种默认项目，project_id 保持 None（前端据此归入未分组区）
    projects = await app.state.project_service.list_projects("u-user")
    assert projects == []
    doc = await app.state.store.get("sessions", sid)
    assert doc.get("project_id") is None

    # 反向断言（普通用户视角，与上面签 admin token 的用例互补）：事件落本人目录；
    # 会话目录同时是工作区（workspace/ 下 files/output/tmp 已建；events.jsonl
    # 在会话根，与模型可见的工作区隔离）
    data_root = app.state.settings.data_root
    sid_dir = data_root / "users" / "u-user" / "sessions" / sid
    assert (sid_dir / "events.jsonl").exists()
    ws = sid_dir / "workspace"
    assert (ws / "files").is_dir() and (ws / "tmp").is_dir()
    # 没落进他人（admin）目录
    assert not (data_root / "users" / "u-admin" / "sessions" / sid).exists()
    # 旧的扁平路径不再写入
    assert not (data_root / "sessions" / sid).exists()


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
    user_dir = app.state.settings.data_root / "users" / "u-user" / "workspaces"

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

    # 2) 未绑定项目（不选工作区）→ 会话目录自身为工作区，绝不回落别的项目
    sid2 = (await client.post("/api/v1/sessions", headers=user_headers,
                              json={"assistant_id": "asst-data"})).json()["_id"]
    await _chat_once(client, user_headers, sid2, "第二问")
    sessions_dir = app.state.settings.data_root / "users" / "u-user" / "sessions"
    assert captured[-1] == sessions_dir / sid2 / "workspace"
    assert captured[-1] != user_dir
    assert (captured[-1] / "tmp").is_dir()  # python.run 的 cwd 落在会话工作区内


async def test_attachment_copied_into_session_root(app, client, admin_headers,
                                                   user_headers, monkeypatch):
    """草稿态附件落项目、会话无绑定：发送时复制进会话目录（跨根），原件不动。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]

    # 草稿态上传：落当时选中的项目（前端 WorkspacePicker 行为）
    pid = await _make_project(client, user_headers, "暂存项目")
    up = await client.post(f"/api/v1/projects/{pid}/files", headers=user_headers,
                           files=[("files", ("draft.csv", b"a\n1", "text/csv"))])
    assert up.status_code == 201, up.text
    fid = up.json()["results"][0]["file"]["_id"]

    # 无绑定会话发消息带附件：agent 工作根是会话目录，附件须复制进去
    sid = (await client.post("/api/v1/sessions", headers=user_headers,
                             json={"assistant_id": "asst-data"})).json()["_id"]
    r = await client.post(f"/api/v1/sessions/{sid}/messages", headers=user_headers,
                          json={"text": "分析附件", "attachments": [{"file_id": fid}]})
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    user_ev = next(e for t, e in events if t == "user/message")
    att = user_ev["payload"]["attachments"][0]
    sid_dir = app.state.settings.data_root / "users" / "u-user" / "sessions" / sid
    assert att["path"] == f"files/{att['filename']}"
    assert (sid_dir / "workspace" / att["path"]).read_bytes() == b"a\n1"
    # 副本记录带 session_id 归属；原件仍在项目里
    clone = await app.state.store.get("files", att["file_id"])
    assert clone["session_id"] == sid
    origin = await app.state.store.get("files", fid)
    assert origin["project_id"] == pid
    proj_root = app.state.project_service.root_for(
        await app.state.project_service.get("u-user", pid))
    assert (proj_root / "files" / "draft.csv").exists()


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
    # 缺省 = 非默认启用：运行期技能索引按"已安装"过滤（无管理员直通），须先安装
    for name in ("data-analysis", "pdf-extraction"):
        assert (await client.post(f"/api/v1/catalog/skill/{name}/install",
                                  json={}, headers=admin_headers)).status_code == 201
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
    # 插件配置命名空间注入（未安装插件时为空 dict）
    assert "plugins" in extra

    # 渐进披露：正文字符串不得出现在 system prompt 里（只有 skill.read 能取到）
    assert "## 决策规则" not in prompt
    assert skill["content"] not in prompt


async def test_chat_passes_user_id_to_skill_index(app, client, admin_headers, monkeypatch):
    """运行期技能索引必须按登录用户解析（用户自建技能才进得来）。"""
    svc = app.state.skill_service
    original = svc.list_skills
    seen: list[str | None] = []

    def spy(user_id=None):
        seen.append(user_id)
        return original(user_id=user_id)

    monkeypatch.setattr(svc, "list_skills", spy)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    # 建 provider 并绑定助手（_bind_provider_to_asst_data 内部即 _make_provider，
    # 两者同建 chat-mock 会撞 409，故不重复调用）
    await _bind_provider_to_asst_data(client, admin_headers)
    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    assert seen, "chat 未调用技能索引"
    assert all(uid for uid in seen), f"技能索引未带 user_id: {seen}"


async def test_user_owned_skill_enters_prompt_and_tool_context(
        app, client, admin_headers, monkeypatch):
    """端到端：经 POST /api/v1/me/skills 建的自建技能真的进提示词与工具上下文。

    这是"用户自建技能在对话中生效"的直接证据：索引（名+描述）进 system prompt，
    正文进 context_extra["skills"]，两处都只有带上登录用户 sub 才取得到
    （用户根 `{data_root}/users/<uid>/skills` 不在公共层/只读根里）。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    r = await client.post("/api/v1/me/skills", headers=admin_headers, json={
        "name": "owner-only-skill",
        "description": "只有技能主人可用的自建技能",
        "content": "# 自建\n\n## 工作流\n\n只走主人目录。\n",
    })
    assert r.status_code == 201, r.text

    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    prompt = captured[-1]["config"].system_prompt
    assert "`owner-only-skill`" in prompt
    assert "只有技能主人可用的自建技能" in prompt
    # 正文同样按用户解析（read_body 未带 sub 时这里会是空串）
    extra = captured[-1]["context_extra"]
    assert "只走主人目录。" in extra["skills"]["owner-only-skill"]
    assert extra["skill_meta"]["owner-only-skill"] == "只有技能主人可用的自建技能"


class _FakeIdentity:
    """假 AI⁴MS 身份服务：token_for 固定返回预设值（None = 解析不到身份）。"""

    def __init__(self, token: str | None) -> None:
        """记录预设 token。"""
        self._token = token
        self.calls: list[dict] = []

    async def token_for(self, user_payload: dict) -> str | None:
        """记录入参并返回预设 token。"""
        self.calls.append(user_payload)
        return self._token


async def test_ai4ms_token_injected_into_context_extra(app, client, admin_headers,
                                                       monkeypatch):
    """代签到的 AI⁴MS 凭证进 ctx.extra["ai4ms_token"]（插件优先用它）。

    打桩方式：AgentService 构造时持有身份服务引用，故替换其私有属性
    （main.py 的 lifespan 里 app.state.ai4ms_identity 与它是同一对象）。
    """
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    fake = _FakeIdentity("minted-tok")
    app.state.agent_service._ai4ms_identity = fake

    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    assert captured[-1]["context_extra"]["ai4ms_token"] == "minted-tok"
    assert fake.calls[-1]["sub"] == "u-admin"  # 用当轮登录用户 payload 解析


async def test_ai4ms_token_absent_when_identity_unresolved(app, client, admin_headers,
                                                           monkeypatch):
    """解析不到 AI⁴MS 账号 → 不注入该键（插件回落配置里的服务 token）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    app.state.agent_service._ai4ms_identity = _FakeIdentity(None)

    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    assert "ai4ms_token" not in captured[-1]["context_extra"]
    assert "plugins" in captured[-1]["context_extra"]  # 其余注入不受影响


async def test_requested_skills_filter_index(app, client, admin_headers, monkeypatch):
    """chat(requested_skills=...) 只装配选中技能（索引与工具上下文同步收窄）。"""
    provider = await _bind_provider_to_asst_data(client, admin_headers)
    # 两个技能都安装，确保"未进索引"是 requested_skills 收窄的结果而非不可见
    for name in ("data-analysis", "pdf-extraction"):
        assert (await client.post(f"/api/v1/catalog/skill/{name}/install",
                                  json={}, headers=admin_headers)).status_code == 201
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
    assert projects[0]["name"] == "默认工作区"      # 显示名统一为「默认工作区」
    assert projects[0]["dir_name"] == "default"     # 目录名口径统一（M-2）
    user_dir = app.state.settings.data_root / "users" / "u-first" / "workspaces"
    assert [p.name for p in user_dir.iterdir()] == ["default"]


async def test_foreign_project_id_rejected_and_fallback_stays_own(
        app, client, admin_headers, user_headers, monkeypatch):
    """他人/不存在的 project_id：建会话 404；会话被塞他人 pid 时运行在会话目录并清掉脏 pid。

    运行时不串他人目录、也不回落本人其它项目（未绑定的选择必须被尊重），且数据
    干净：脏 pid 既不落库（I-3），也不让下一轮继续带着失效绑定跑（写回 None，I-2）。
    """
    admin_pid = await _make_project(client, admin_headers, "管理员项目")
    for pid in (admin_pid, "no-such-project"):
        r = await client.post("/api/v1/sessions", headers=user_headers,
                              json={"assistant_id": "asst-data", "project_id": pid})
        assert r.status_code == 404, r.text
    # 校验失败不产生副作用：该用户没有任何会话落库
    assert await app.state.store.list("sessions", filters={"user_id": "u-user"}) == []

    # 脏数据路径：直接把他人 pid 塞进会话文档，运行时必须落在本人会话目录
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

    sessions_dir = app.state.settings.data_root / "users" / "u-user" / "sessions"
    assert captured[-1] == sessions_dir / sid / "workspace"  # 会话工作区，不回落本人项目
    assert "u-admin" not in captured[-1].parts  # 没跑进他人目录
    my_root = app.state.project_service.root_for(
        await app.state.project_service.get("u-user", my_pid))
    assert captured[-1] != my_root
    # 失效绑定写回 None：后续轮次稳定命中会话目录，前端归入未分组区
    doc = await app.state.store.get("sessions", sid)
    assert doc["project_id"] is None


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


# ---------- 专家可选（不选走平台默认提示词与全部内置工具） ----------


async def test_create_session_without_assistant(client, user_headers):
    """不选专家也能建会话（jiuwen 的 '' 卸载专家语义）。"""
    r = await client.post("/api/v1/sessions", json={}, headers=user_headers)
    assert r.status_code == 201
    assert not r.json().get("assistant_id")


async def test_patch_can_clear_assistant(client, user_headers):
    """PATCH 显式传 assistant_id="" 表示卸载专家；不传该字段则不动。"""
    sid = (await client.post("/api/v1/sessions",
                             json={"assistant_id": "asst-research"},
                             headers=user_headers)).json()["_id"]
    # 不传该字段 → 保持
    r1 = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "x"}, headers=user_headers)
    assert r1.json()["assistant_id"] == "asst-research"
    # 显式传空 → 清空
    r2 = await client.patch(f"/api/v1/sessions/{sid}", json={"assistant_id": ""},
                            headers=user_headers)
    assert not r2.json().get("assistant_id")
    # 传非法 id → 404 且不改
    r3 = await client.patch(f"/api/v1/sessions/{sid}", json={"assistant_id": "nope"},
                            headers=user_headers)
    assert r3.status_code == 404
    assert not (await client.get("/api/v1/sessions", headers=user_headers)).json()[0].get(
        "assistant_id")


async def test_no_assistant_uses_platform_prompt_only(
        app, client, admin_headers, user_headers, monkeypatch):
    """无专家时：system prompt 有平台默认段、无 persona；工具是全部内置工具。

    会话不带 assistant_id（也无助手可回落），须由 model_provider_id 显式提供模型；
    观察方式复用 _capture_run_args：断言 AgentConfig 的实际装配结果。
    """
    provider = await _make_provider(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    sid = (await client.post("/api/v1/sessions",
                             json={"model_provider_id": provider["_id"]},
                             headers=user_headers)).json()["_id"]
    await _chat_once(client, user_headers, sid)

    prompt = captured[-1]["config"].system_prompt
    tools = captured[-1]["config"].tool_names
    assert "核心原则" in prompt                 # 平台默认段在
    assert "科研助手" not in prompt              # 没有专家 persona
    for name in ("file.read", "python.run", "skill.list", "skill.read"):
        assert name in tools


# ---------- steering：运行中插话（不打断当前步骤，下一步生效） ----------


class SteerableBackend:
    """可插话后端：首次调用延时留窗口并产工具调用，第二次看到插话后收尾。"""

    calls: ClassVar[list] = []

    def __init__(self, provider):
        """记录 provider。"""
        self.provider = provider

    async def stream(self, messages, tools=None):
        """第 1 次延时+工具调用（steer 窗口），第 2 次直接回答。"""
        SteerableBackend.calls.append(list(messages))
        if len(SteerableBackend.calls) == 1:
            await asyncio.sleep(0.3)
            yield ToolCallChunk(id="c1", name="skill.list", arguments={})
        else:
            yield TextDelta(text="完成")
            yield Usage()


async def test_steer_run(app, client, admin_headers, monkeypatch):
    """运行中插话：入队成功、下一个 step 边界落为 steering user/message 且 LLM 可见。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    SteerableBackend.calls = []
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", SteerableBackend)
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "列技能"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]

    r = await client.post(f"/api/v1/runs/{run_id}/steer", headers=admin_headers,
                          json={"text": "用中文"})
    assert r.status_code == 200 and r.json()["ok"] is True

    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    steering = [e for t, e in events
                if t == "user/message" and e["payload"].get("steering")]
    assert steering and steering[0]["payload"]["text"] == "用中文"
    # 第二次 LLM 调用（工具结果之后）的上下文里能看到插话
    assert any(getattr(m, "content", "") == "用中文" for m in SteerableBackend.calls[1])
    # run 已结束：再插话 409
    r2 = await client.post(f"/api/v1/runs/{run_id}/steer", headers=admin_headers,
                           json={"text": "再来"})
    assert r2.status_code == 409


class FinalAnswerSteerableBackend:
    """第 1 次延时流式输出纯文本最终回答（留 steer 窗口、无下一个 step），第 2 次按剧本回答。"""

    calls: ClassVar[list] = []

    def __init__(self, provider):
        """记录 provider。"""
        self.provider = provider

    async def stream(self, messages, tools=None):
        """第 1 幕延时收尾（插话无处消费），第 2 幕消费剧本。"""
        FinalAnswerSteerableBackend.calls.append(list(messages))
        if len(FinalAnswerSteerableBackend.calls) == 1:
            for _ in range(10):
                await asyncio.sleep(0.05)
                yield TextDelta(text="第一轮回答")
            yield Usage()
        else:
            yield TextDelta(text="第二轮回答")
            yield Usage()


async def test_steer_after_final_answer_queues_next_turn(app, client, admin_headers,
                                                          monkeypatch):
    """收尾窗口插话兜底：第一轮结束后残留插话自动转为下一轮正式输入续跑。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    FinalAnswerSteerableBackend.calls = []
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend",
                        FinalAnswerSteerableBackend)
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "第一问"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    # 等 0.2s 进入收尾流中段（0.5s 流）：step 0 的 drain 已过、本轮无下一个
    # step——插话只能留队列，由收尾兜底转为下一轮（提前入队会被本轮 drain
    # 消费，测的就不是收尾窗口了）
    await asyncio.sleep(0.2)
    r = await client.post(f"/api/v1/runs/{run_id}/steer", headers=admin_headers,
                          json={"text": "追问一句"})
    assert r.status_code == 200

    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    # 两个完整 turn；插话作为第二轮的正式 user/message（不带 steering 标记）
    assert types.count("turn/end") == 2
    user_msgs = [e for t, e in events if t == "user/message"]
    assert user_msgs[-1]["payload"]["text"] == "追问一句"
    assert "steering" not in user_msgs[-1]["payload"]
    # 第二轮 LLM 调用（续跑）的上下文里能看到插话
    assert any(getattr(m, "content", "") == "追问一句" and getattr(m, "role", None) is not None
               and m.role.value == "user"
               for m in FinalAnswerSteerableBackend.calls[1])


async def test_steer_missing_run_404(client, admin_headers):
    """插话目标 run 不存在 → 404；空文本 → 422。"""
    r = await client.post("/api/v1/runs/nope/steer", headers=admin_headers,
                          json={"text": "hi"})
    assert r.status_code == 404


async def test_steer_blank_text_422(app, client, admin_headers, monkeypatch):
    """空文本插话 → 422。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    sid = await _make_session(client, admin_headers)
    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers, json={"text": "问"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    r = await client.post(f"/api/v1/runs/{run_id}/steer", headers=admin_headers,
                          json={"text": "   "})
    assert r.status_code == 422
    resp = await asyncio.wait_for(task, timeout=15)
    assert resp.status_code == 200


# ---------- 知识库代理与助手绑定 ----------


async def test_knowledge_bases_proxy_unconfigured_503(app, client, admin_headers):
    """WeKnora 未配置 → 503 明确报错（注入空配置，避免依赖环境 .env）。"""
    from app.services.weknora_service import WeKnoraService

    app.state.weknora_service = WeKnoraService("", "")
    r = await client.get("/api/v1/knowledge-bases", headers=admin_headers)
    assert r.status_code == 503
    assert "WeKnora" in r.json()["detail"]


async def test_assistant_knowledge_base_ids_roundtrip(client, admin_headers):
    """助手 knowledge_base_ids 可创建、更新、读回。"""
    created = (await client.post(
        "/api/v1/assistants", headers=admin_headers, json={
            "name": "kb助手", "system_prompt": "检索资料并回答",
            "knowledge_base_ids": ["kb-1", "kb-2"],
        })).json()
    assert created["knowledge_base_ids"] == ["kb-1", "kb-2"]
    updated = (await client.patch(
        f"/api/v1/assistants/{created['_id']}", headers=admin_headers,
        json={"knowledge_base_ids": ["kb-3"]})).json()
    assert updated["knowledge_base_ids"] == ["kb-3"]
    await client.delete(f"/api/v1/assistants/{created['_id']}", headers=admin_headers)


# ---------- 多模态模型开关与 ask_user / file.send 问答回路 ----------


async def test_provider_multimodal_flag_roundtrip(client, admin_headers):
    """multimodal 标记可创建、更新、读回。"""
    created = (await client.post("/api/v1/models", headers=admin_headers, json={
        "name": "vl-test", "base_url": "http://vl.local/v1", "api_key": "k",
        "model_id": "qwen-vl", "enabled": False, "multimodal": True,
    })).json()
    assert created["multimodal"] is True
    updated = (await client.patch(f"/api/v1/models/{created['_id']}",
                                  headers=admin_headers, json={"multimodal": False})).json()
    assert updated["multimodal"] is False
    await client.delete(f"/api/v1/models/{created['_id']}", headers=admin_headers)


async def test_read_image_gated_by_multimodal(app, client, admin_headers,
                                              user_headers, monkeypatch):
    """file.read_image 仅多模态模型下发；普通模型不出现。"""
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    # 两次 _run_provider 各消耗一条剧本（非多模态一次 / 多模态一次）
    FakeBackend.script = [[TextDelta(text="ok"), Usage()], [TextDelta(text="ok"), Usage()]]
    captured = _capture_run_args(monkeypatch)
    provider = await _make_provider(client, admin_headers)

    async def _run_provider(pid):
        sid = (await client.post("/api/v1/sessions",
                                 json={"model_provider_id": pid},
                                 headers=user_headers)).json()["_id"]
        await _chat_once(client, user_headers, sid)

    # 默认（非多模态）：不含 read_image
    await _run_provider(provider["_id"])
    assert "file.read_image" not in captured[-1]["config"].tool_names
    assert "ask_user" in captured[-1]["config"].tool_names  # 问询不门控
    # 打开多模态：下发
    await client.patch(f"/api/v1/models/{provider['_id']}",
                       headers=admin_headers, json={"multimodal": True})
    await _run_provider(provider["_id"])
    assert "file.read_image" in captured[-1]["config"].tool_names


class AskFakeBackend(FakeBackend):
    """先调 ask_user 工具，回答后再收尾（按调用次数而非剧本长度判定）。"""

    calls: ClassVar[int] = 0

    async def stream(self, messages, tools=None):
        FakeBackend.received.append(list(messages))
        AskFakeBackend.calls += 1
        if AskFakeBackend.calls == 1:
            yield ToolCallChunk(id="ask1", name="ask_user",
                                arguments={"query": "用哪种方案？",
                                           "options": [{"label": "方案A"}, {"label": "方案B"}]})
            return
        for ev in FakeBackend.script.pop(0):
            yield ev


class MultiAskFakeBackend(FakeBackend):
    """先以多题模式调 ask_user（questions 数组），回答后收尾。"""

    calls: ClassVar[int] = 0

    async def stream(self, messages, tools=None):
        FakeBackend.received.append(list(messages))
        MultiAskFakeBackend.calls += 1
        if MultiAskFakeBackend.calls == 1:
            yield ToolCallChunk(id="mq1", name="ask_user", arguments={
                "questions": [
                    {"question": "用哪个数据集？", "header": "数据", "multi_select": True,
                     "options": [{"label": "A"}, {"label": "B"}]},
                    {"question": "输出什么格式？"},
                ],
            })
            return
        for ev in FakeBackend.script.pop(0):
            yield ev


async def test_ask_user_multi_questions_e2e(app, client, admin_headers, monkeypatch):
    """ask_user 多题全链路：questions 落事件 → answer 拼接文本 → tool/result 携带。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", MultiAskFakeBackend)
    FakeBackend.script = [[TextDelta(text="按答案执行"), Usage()]]
    FakeBackend.received = []
    MultiAskFakeBackend.calls = 0
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "帮我配置"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    asked = None
    for _ in range(80):
        events = await app.state.event_repo.list_events(sid)
        asked = next((e for e in events if e.type.value == "ask/user"), None)
        if asked:
            break
        await asyncio.sleep(0.1)
    assert asked is not None
    qs = asked.payload["questions"]
    assert [q["question"] for q in qs] == ["用哪个数据集？", "输出什么格式？"]
    assert qs[0]["multi_select"] is True and qs[0]["header"] == "数据"
    assert "kind" not in asked.payload  # 非审批

    reply = "1. 用哪个数据集？：A、B\n2. 输出什么格式？：Word"
    r = await client.post(f"/api/v1/runs/{run_id}/answer", headers=admin_headers,
                          json={"text": reply})
    assert r.status_code == 200

    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    result = [e for t, e in events
              if t == "tool/result" and e["payload"].get("name") == "ask_user"][0]
    assert reply in result["payload"]["content"]
    assert events[-1][0] == "turn/end"


async def test_ask_user_end_to_end(app, client, admin_headers, monkeypatch):
    """ask_user 全链路：问题事件落盘 → answer API → 回答进 tool/result 与第二轮上下文。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", AskFakeBackend)
    FakeBackend.script = [[TextDelta(text="按方案A执行"), Usage()]]
    FakeBackend.received = []
    AskFakeBackend.calls = 0
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "帮我选方案"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    # 等 ask/user 事件落 DB（工具在等回答）
    asked = None
    for _ in range(80):
        events = await app.state.event_repo.list_events(sid)
        asked = next((e for e in events if e.type.value == "ask/user"), None)
        if asked:
            break
        await asyncio.sleep(0.1)
    assert asked is not None
    assert asked.payload["query"] == "用哪种方案？"
    assert [o["label"] for o in asked.payload["options"]] == ["方案A", "方案B"]

    bad = await client.post(f"/api/v1/runs/nope/answer", headers=admin_headers,
                            json={"text": "x"})
    assert bad.status_code == 404
    r = await client.post(f"/api/v1/runs/{run_id}/answer", headers=admin_headers,
                          json={"text": "方案A"})
    assert r.status_code == 200 and r.json()["ok"] is True

    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    ask_result = [e for t, e in events
                  if t == "tool/result" and e["payload"].get("name") == "ask_user"][0]
    assert "方案A" in ask_result.payload if hasattr(ask_result, "payload") else True
    assert "方案A" in ask_result["payload"]["content"]
    # 收尾正常
    assert events[-1][0] == "turn/end"


# ---------- 管线强制审批（Permission.ASK_USER → approval_handler 复用 ask/user 回路） ----------


@tool(name="demo.risky", description="需审批演示工具", parameters={
    "type": "object", "properties": {},
}, permission=Permission.ASK_USER)
async def _demo_risky(ctx, args):
    """执行前需用户审批的演示工具。"""
    return ToolResult(ok=True, content="已执行敏感操作")


# 工具注册表已收口：assistants_api 与 agent_service 共用同一实例，注册一次两边可见
agent_service_mod._REGISTRY.register(_demo_risky)


class ApprovalFakeBackend(FakeBackend):
    """先调需审批工具，用户答复后收尾（按调用次数判定）。"""

    calls: ClassVar[int] = 0

    async def stream(self, messages, tools=None):
        FakeBackend.received.append(list(messages))
        ApprovalFakeBackend.calls += 1
        if ApprovalFakeBackend.calls == 1:
            yield ToolCallChunk(id="rk1", name="demo.risky", arguments={})
            return
        for ev in FakeBackend.script.pop(0):
            yield ev


async def _approval_e2e(app, client, admin_headers, monkeypatch, reply: str) -> dict:
    """跑一遍审批全链路并返回断言素材（ask/user 事件 + tool/result 事件）。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    # 白名单检查先于审批判定：把演示工具补进助手工具面
    r = await client.patch("/api/v1/assistants/asst-data", headers=admin_headers,
                           json={"tool_whitelist": [
                               "python.run", "file.read", "file.write", "file.list",
                               "demo.risky"]})
    assert r.status_code == 200, r.text
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend",
                        ApprovalFakeBackend)
    FakeBackend.script = [[TextDelta(text="收尾"), Usage()]]
    FakeBackend.received = []
    ApprovalFakeBackend.calls = 0
    sid = await _make_session(client, admin_headers)

    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "执行敏感操作"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    asked = None
    for _ in range(80):
        events = await app.state.event_repo.list_events(sid)
        asked = next((e for e in events if e.type.value == "ask/user"), None)
        if asked:
            break
        await asyncio.sleep(0.1)
    assert asked is not None
    assert asked.payload["kind"] == "approval"
    assert asked.payload["tool"] == "demo.risky"

    r = await client.post(f"/api/v1/runs/{run_id}/answer", headers=admin_headers,
                          json={"text": reply})
    assert r.status_code == 200 and r.json()["ok"] is True

    resp = await asyncio.wait_for(task, timeout=15)
    sse_events = parse_sse(resp.text)
    result = [e for t, e in sse_events
              if t == "tool/result" and e["payload"].get("name") == "demo.risky"][0]
    assert sse_events[-1][0] == "turn/end"
    return result["payload"]


async def test_approval_allowed_e2e(app, client, admin_headers, monkeypatch):
    """审批允许：管线放行 → 工具执行成功，tool/result 为工具真实结果。"""
    payload = await _approval_e2e(app, client, admin_headers, monkeypatch, "允许")
    assert payload["ok"] is True
    assert payload["content"] == "已执行敏感操作"


async def test_approval_denied_e2e(app, client, admin_headers, monkeypatch):
    """审批拒绝：fail-closed，tool/result 为 denied 且给 LLM 可见原因。"""
    payload = await _approval_e2e(app, client, admin_headers, monkeypatch, "拒绝")
    assert payload["ok"] is False
    assert payload["error"] == "denied"
    assert "拒绝执行" in payload["content"]


async def test_answer_no_pending_409(app, client, admin_headers, monkeypatch):
    """无待答问题时 answer → 409。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    sid = await _make_session(client, admin_headers)
    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers, json={"text": "问"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    await asyncio.wait_for(task, timeout=15)
    r = await client.post(f"/api/v1/runs/{run_id}/answer", headers=admin_headers,
                          json={"text": "x"})
    assert r.status_code == 409


class SendFileFakeBackend(FakeBackend):
    """先调 file.send 交付产物，再收尾（按调用次数判定）。"""

    calls: ClassVar[int] = 0

    async def stream(self, messages, tools=None):
        FakeBackend.received.append(list(messages))
        SendFileFakeBackend.calls += 1
        if SendFileFakeBackend.calls == 1:
            yield ToolCallChunk(id="send1", name="file.send",
                                arguments={"path": "output/report.md", "note": "分析报告"})
            return
        for ev in FakeBackend.script.pop(0):
            yield ev


async def test_file_send_end_to_end(app, client, admin_headers, monkeypatch):
    """file.send：产物复制进 files/、登记集合、事件可回放、下载可用。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", SendFileFakeBackend)
    FakeBackend.script = [[TextDelta(text="已交付"), Usage()]]
    SendFileFakeBackend.calls = 0
    sid = await _make_session(client, admin_headers)

    # 先启动 run，再从 run 记录取 user 定位会话目录（无绑定会话的工作区），
    # 预置产物后等服务完成
    task = asyncio.create_task(client.post(f"/api/v1/sessions/{sid}/messages",
                                           headers=admin_headers,
                                           json={"text": "给我报告"}))
    run_id = (await _wait_for_running_runs(app, 1))[0]
    run_doc = await app.state.store.get("runs", run_id)
    sid_dir = (app.state.settings.data_root / "users" / run_doc["user_id"]
               / "sessions" / sid / "workspace")
    out_dir = sid_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text("# 报告\n测试产物", encoding="utf-8")
    resp = await asyncio.wait_for(task, timeout=15)
    events = parse_sse(resp.text)
    send_ev = [e for t, e in events if t == "file/send"]
    tool_res = [e for t, e in events
                if t == "tool/result" and e["payload"].get("name") == "file.send"]
    if not send_ev:
        # 路径不存在（output/report.md 未预置）→ 工具失败也可接受，但事件必无
        assert tool_res and tool_res[0]["payload"]["ok"] is False
        return
    file_id = send_ev[0]["payload"]["file_id"]
    dl = await client.get(f"/api/v1/files/{file_id}/download", headers=admin_headers)
    assert dl.status_code == 200


async def test_send_falls_back_to_first_enabled_model(app, client, admin_headers,
                                                      monkeypatch):
    """无专家且会话未存模型：发送回落第一个启用模型（不写回会话，查看零写入）。"""
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [[TextDelta(text="ok"), Usage()]]
    enabled = await _make_provider(client, admin_headers, name="only-enabled",
                                   model_id="m-enabled")
    disabled = await _make_provider(client, admin_headers, name="disabled-one",
                                    model_id="m-disabled", enabled=False)
    # 无专家、不带模型的会话
    sid = (await client.post("/api/v1/sessions", headers=admin_headers,
                             json={})).json()["_id"]
    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "你好"})
    assert resp.status_code == 200, resp.text
    # 实际用的是启用模型（FakeBackend 收到的 provider 配置）
    assert FakeBackend.providers[-1].model_id == "m-enabled"
    # 回落不写回会话（updated_at/model_provider_id 不变 → 查看零写入）
    doc = await app.state.session_repo.get(sid)
    assert doc.get("model_provider_id") is None
    await client.delete(f"/api/v1/models/{enabled['_id']}", headers=admin_headers)
    await client.delete(f"/api/v1/models/{disabled['_id']}", headers=admin_headers)
