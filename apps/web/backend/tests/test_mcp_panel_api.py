"""「+」面板 MCP 合并视图端点测试。"""
import json

import pytest


@pytest.fixture
def seed_public_catalog(tmp_path) -> None:
    """预置公共 MCP（pub-ok 可见 / pub-hidden 供 hidden 用例）。"""
    base = tmp_path / "data" / "public" / "catalog" / "mcp"
    for mid in ("pub-ok", "pub-hidden"):
        d = base / mid
        d.mkdir(parents=True)
        (d / "mcp.json").write_text(json.dumps({
            "id": mid, "name": mid, "description": "测试公共 MCP",
            "transport": "streamable-http", "url": f"https://x.test/{mid}",
        }, ensure_ascii=False), encoding="utf-8")


async def test_panel_merges_user_and_catalog(client, user_headers,
                                              seed_public_catalog):
    resp = await client.post("/api/v1/me/mcps", json={
        "id": "mine", "name": "我的", "url": "https://mine.test/mcp"},
        headers=user_headers)
    assert resp.status_code == 201
    resp = await client.put("/api/v1/me/capabilities/mcp/pub-ok", json={
        "installed": True}, headers=user_headers)
    assert resp.status_code == 200
    resp = await client.get("/api/v1/me/mcps/panel", headers=user_headers)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert rows["mine"]["source"] == "user"
    assert rows["mine"]["transport"] == "streamable-http"
    assert rows["mine"]["status"] in {"connected", "unchecked", "error"}
    assert rows["pub-ok"]["source"] == "catalog"
    assert rows["pub-ok"]["transport"] == "streamable-http"


async def test_panel_hides_invisible_catalog(client, user_headers,
                                             seed_public_catalog):
    # 未安装：面板不出现
    resp = await client.get("/api/v1/me/mcps/panel", headers=user_headers)
    assert all(r["id"] != "pub-ok" for r in resp.json())


async def test_panel_hides_disabled_user_mcp(client, user_headers):
    resp = await client.post("/api/v1/me/mcps", json={
        "id": "off", "name": "停用", "url": "https://off.test/mcp",
        "enabled": False}, headers=user_headers)
    assert resp.status_code == 201
    resp = await client.get("/api/v1/me/mcps/panel", headers=user_headers)
    assert all(r["id"] != "off" for r in resp.json())


async def test_market_mcp_rows_carry_transport(client, user_headers,
                                               seed_public_catalog):
    resp = await client.put("/api/v1/me/capabilities/mcp/pub-ok", json={
        "installed": True}, headers=user_headers)
    assert resp.status_code == 200
    resp = await client.get("/api/v1/market/mcp", headers=user_headers)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert rows["pub-ok"]["transport"] == "streamable-http"
