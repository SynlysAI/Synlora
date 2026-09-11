"""端到端链路测试（Plan 2 Task 8）。

E2E-1 数据分析闭环（设计文档 §9 场景 2 后端侧）：上传 CSV → asst-data 会话
→ mock 剧本触发 python.run 真实子进程（cwd=tmp/，经 ../files/ 读上传文件）
→ 均值写入 output/result.txt → SSE 事件完整 + 产物真实落盘 + 计数正确。

E2E-2 断连恢复：消息不经 SSE 消费（等价客户端断连）→ _drive 后台独立完成
→ 轮询 GET events 补齐第一轮 → 第二轮正常收流 → 两轮 seq 全程无重复
（瞬态 delta 不落盘留有洞位）且 after_seq 增量恰好返回第二轮。
"""
import asyncio

import pytest
from synlys_harness import ModelProviderConfig, TextDelta, ToolCallChunk, Usage

from tests.test_chat_api import (
    FakeBackend,
    _bind_provider_to_asst_data,
    _make_session,
    _workspace_root,
    parse_sse,
)

# python.run 在 {workspace}/tmp 下执行（隔离模式子进程）：
# 经 ../files/ 读上传的 CSV，算双列均值写入 ../output/result.txt 并打印。
ANALYSIS_CODE = """import csv, pathlib
rows = list(csv.DictReader(open("../files/data.csv", encoding="utf-8")))
a = [float(r["a"]) for r in rows]
b = [float(r["b"]) for r in rows]
mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
pathlib.Path("../output/result.txt").write_text(
    f"mean_a={mean_a:g}\\nmean_b={mean_b:g}\\n", encoding="utf-8")
print(f"mean_a={mean_a:g} mean_b={mean_b:g}")
"""


@pytest.fixture(autouse=True)
def _reset_scripts():
    """每个测试前后重置脚本与记录，避免跨模块残留。"""
    FakeBackend.script = []
    FakeBackend.received = []
    yield
    FakeBackend.script = []
    FakeBackend.received = []


# ---------- E2E-1 完整数据分析闭环 ----------


async def test_e2e_data_analysis_loop(app, client, admin_headers, monkeypatch):
    """数据分析闭环：上传 → 对话 → python.run 真实执行 → 产物落盘 → 事件/计数齐备。"""
    await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)

    # 1. 上传 CSV：落 {data_root}/workspaces/u-admin/files/data.csv
    r = await client.post("/api/v1/files", headers=admin_headers,
                          files=[("files", ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv"))])
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["results"][0]["file"]["filename"] == "data.csv"

    # 2. asst-data 会话 + 剧本：第一幕调 python.run 读文件算均值，第二幕总结
    FakeBackend.script = [
        [ToolCallChunk(id="c1", name="python.run", arguments={"code": ANALYSIS_CODE})],
        [TextDelta(text="分析完成：a 列均值 2，b 列均值 3"), Usage()],
    ]
    sid = await _make_session(client, admin_headers, "asst-data")

    # 3. 发消息收 SSE：完整事件序列（工具真实子进程执行）
    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "分析上传的 CSV 均值"})
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    types = [t for t, _ in events]
    assert types == [
        "turn/start", "user/message", "tool/call", "tool/result",
        "llm/delta", "assistant/message", "turn/end",
    ]

    # 4. 工具结果 ok 且 stdout 含双列均值
    tool_result = dict(events[types.index("tool/result")][1]["payload"])
    assert tool_result["name"] == "python.run"
    assert tool_result["ok"] is True, tool_result
    assert "mean_a=2" in tool_result["content"]
    assert "mean_b=3" in tool_result["content"]

    # 5. 工作区产物真实存在且内容正确：agent 跑在会话所属项目目录（此处为首个项目
    #    = 迁移旧布局后补种的 default），python.run 的 cwd=项目 tmp/，故 ../output
    #    即项目 output/；第 4 步能读出均值也证明上传的 CSV 已在项目 files/ 内
    projects = await app.state.project_service.list_projects("u-admin")
    result_txt = app.state.project_service.root_for(projects[0]) / "output" / "result.txt"
    assert result_txt.exists(), f"产物未落盘: {result_txt}"
    assert result_txt.read_text(encoding="utf-8") == "mean_a=2\nmean_b=3\n"

    # 6. 回放事件完整（不含瞬态 llm/delta，seq 4 缺位成洞、turn/end 收尾）+ 首条消息计数为 1
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["seq"] for e in replayed] == [0, 1, 2, 3, 5, 6]
    assert replayed[-1]["type"] == "turn/end"
    r = await client.get("/api/v1/sessions", headers=admin_headers)
    mine = next(s for s in r.json() if s["_id"] == sid)
    assert mine["message_count"] == 1


# ---------- E2E-2 断连恢复（SSE 无消费者 → 后台完成 → 重连补齐） ----------


async def _poll_events_until_turn_end(client, headers, sid: str,
                                      timeout_s: float = 5.0) -> list[dict]:
    """轮询 GET events 直到 turn/end 落盘并返回事件列表。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        sid: 会话 id。
        timeout_s: 轮询超时秒数。

    Returns:
        落盘完成的会话事件列表。
    """
    for _ in range(int(timeout_s / 0.05)):
        r = await client.get(f"/api/v1/sessions/{sid}/events", headers=headers)
        events = r.json()
        if events and events[-1]["type"] == "turn/end":
            return events
        await asyncio.sleep(0.05)
    raise AssertionError(f"{timeout_s}s 内未等到 turn/end 落盘")


async def test_e2e_disconnect_recovery(app, client, admin_headers, monkeypatch):
    """断连恢复：无 SSE 消费者的消息后台完成 → 轮询补齐 → 第二轮后 seq 连续、增量正确。"""
    provider = await _bind_provider_to_asst_data(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend", FakeBackend)
    FakeBackend.script = [
        [TextDelta(text="断连期间完成的回答"), Usage()],
        [TextDelta(text="重连后的回答"), Usage()],
    ]
    sid = await _make_session(client, admin_headers, "asst-data")

    # 1. service 层直接 chat()：SSE 队列自始至终无消费者（等价客户端断连）
    decrypted = await app.state.provider_repo.get_decrypted(provider["_id"])
    cfg = ModelProviderConfig(
        name=decrypted["name"], base_url=decrypted["base_url"],
        api_key=decrypted["api_key"], model_id=decrypted["model_id"],
    )
    assistant = await app.state.assistant_repo.get("asst-data")
    user = {"sub": "u-admin", "username": "tester-admin", "role": "admin"}
    await app.state.agent_service.chat(
        sid, user, assistant, cfg, "第一条（断连）",
        workspace_root=await _workspace_root(app, "u-admin"))

    # 2. 轮询 GET events 直到第一轮 turn/end（客户端重连补齐语义）；
    #    回放不含瞬态 llm/delta（seq 2 缺位成洞），全文由 assistant/message 承载
    first = await _poll_events_until_turn_end(client, admin_headers, sid)
    assert [e["type"] for e in first] == [
        "turn/start", "user/message", "assistant/message", "turn/end",
    ]
    first_last_seq = first[-1]["seq"]
    assert first_last_seq == 4  # 第一轮 5 个事件占号 seq 0..4（其中 seq 2 为瞬态）

    # 3. 第二条消息正常走 HTTP + SSE
    resp = await client.post(f"/api/v1/sessions/{sid}/messages",
                             headers=admin_headers, json={"text": "第二条（重连）"})
    assert resp.status_code == 200, resp.text
    assert parse_sse(resp.text)[-1][0] == "turn/end"

    # 4. 两轮齐全：seq 全程无重复（每轮一个瞬态洞位）、两条 user 消息按序在场
    r = await client.get(f"/api/v1/sessions/{sid}/events", headers=admin_headers)
    replayed = r.json()
    assert [e["seq"] for e in replayed] == [0, 1, 3, 4, 5, 6, 8, 9]
    user_texts = [e["payload"]["text"] for e in replayed
                  if e["type"] == "user/message"]
    assert user_texts == ["第一条（断连）", "第二条（重连）"]

    # 5. after_seq=第一轮最后 seq → 增量恰好返回第二轮（seq 5..9 去掉瞬态洞 7）
    r = await client.get(f"/api/v1/sessions/{sid}/events?after_seq={first_last_seq}",
                         headers=admin_headers)
    tail = r.json()
    assert [e["seq"] for e in tail] == [5, 6, 8, 9]
    assert tail[0]["type"] == "turn/start"
    assert tail[-1]["type"] == "turn/end"
    tail_user = [e["payload"]["text"] for e in tail if e["type"] == "user/message"]
    assert tail_user == ["第二条（重连）"]
