"""会话 enabled_mcp 字段校验与流转测试。"""
import json

import pytest


@pytest.fixture
def seed_public_catalog(tmp_path) -> None:
    """预置两个公共 MCP 条目（pub-ok 常规可见 / pub-hidden 供测试 hidden）。"""
    base = tmp_path / "data" / "public" / "catalog" / "mcp"
    for mid in ("pub-ok", "pub-hidden"):
        d = base / mid
        d.mkdir(parents=True)
        (d / "mcp.json").write_text(json.dumps({
            "id": mid, "name": mid, "description": "测试公共 MCP",
            "transport": "streamable-http", "url": f"https://x.test/{mid}",
        }, ensure_ascii=False), encoding="utf-8")


async def _new_session(client, headers) -> str:
    resp = await client.post("/api/v1/sessions", json={"title": "t"},
                             headers=headers)
    assert resp.status_code == 201
    return resp.json()["_id"]


async def test_create_session_with_enabled_mcp_ok(client, user_headers):
    resp = await client.post("/api/v1/me/mcps", json={
        "id": "mine", "name": "我的", "url": "https://mine.test/mcp"},
        headers=user_headers)
    assert resp.status_code == 201
    resp = await client.post("/api/v1/sessions", json={
        "title": "t", "enabled_mcp": ["mine"]}, headers=user_headers)
    assert resp.status_code == 201
    assert resp.json()["enabled_mcp"] == ["mine"]


async def test_patch_enabled_mcp_unknown_id_404(client, user_headers):
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["ghost"]}, headers=user_headers)
    assert resp.status_code == 404


async def test_patch_enabled_mcp_own_id_ok(client, user_headers):
    resp = await client.post("/api/v1/me/mcps", json={
        "id": "mine", "name": "我的", "url": "https://mine.test/mcp"},
        headers=user_headers)
    assert resp.status_code == 201
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["mine"]}, headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled_mcp"] == ["mine"]
    # 显式清空 = 不附加
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": []}, headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled_mcp"] == []


async def test_patch_enabled_mcp_catalog_installed_ok(client, user_headers,
                                                      seed_public_catalog):
    resp = await client.put("/api/v1/me/capabilities/mcp/pub-ok", json={
        "installed": True}, headers=user_headers)
    assert resp.status_code == 200
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["pub-ok"]}, headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled_mcp"] == ["pub-ok"]


async def test_patch_enabled_mcp_catalog_uninstalled_404(client, user_headers,
                                                         seed_public_catalog):
    # 非内置公共条目未安装：勾选不能放大可见性
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["pub-ok"]}, headers=user_headers)
    assert resp.status_code == 404


async def test_patch_enabled_mcp_catalog_hidden_404(client, user_headers,
                                                    admin_headers,
                                                    seed_public_catalog):
    resp = await client.put("/api/v1/admin/catalog/mcp/pub-hidden/policy",
                            json={"visibility": "hidden"}, headers=admin_headers)
    assert resp.status_code == 200
    resp = await client.put("/api/v1/me/capabilities/mcp/pub-hidden", json={
        "installed": True}, headers=user_headers)
    assert resp.status_code == 404  # hidden 条目市场不可见，也无法安装
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["pub-hidden"]}, headers=user_headers)
    assert resp.status_code == 404


async def test_patch_enabled_mcp_other_users_mcp_404(client, admin_headers,
                                                     user_headers):
    # 管理员名下自建 MCP，普通用户不可引用
    resp = await client.post("/api/v1/me/mcps", json={
        "id": "admins", "name": "管理员的", "url": "https://a.test/mcp"},
        headers=admin_headers)
    assert resp.status_code == 201
    sid = await _new_session(client, user_headers)
    resp = await client.patch(f"/api/v1/sessions/{sid}", json={
        "enabled_mcp": ["admins"]}, headers=user_headers)
    assert resp.status_code == 404
