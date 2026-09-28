"""公共 MCP 管理端点测试（写入覆盖层 / 热重载 / 删除边界）。

说明：测试环境里数据目录条目本身就是"覆盖层"（is_overlaid 恒真），
"仓库内置层"无法落盘模拟——409 分支（内置不可删/无覆盖不可重置）用
monkeypatch 把 is_overlaid 置 False 确定性触发。
"""
import json

import pytest

import app.api.admin_mcp_api as admin_mcp_module

HTTP_OK = {
    "id": "new-one", "name": "新服务", "description": "d",
    "transport": "streamable-http", "url": "https://x.example.com/mcp",
}


@pytest.fixture
def seed_public_catalog(tmp_path) -> None:
    """预置一个数据目录公共 MCP 条目（等价部署中的"可编辑条目"）。"""
    d = tmp_path / "data" / "public" / "catalog" / "mcp" / "builtin-one"
    d.mkdir(parents=True)
    (d / "mcp.json").write_text(json.dumps({
        "id": "builtin-one", "name": "内置版", "description": "d",
        "transport": "streamable-http", "url": "https://b.example.com/mcp",
    }, ensure_ascii=False), encoding="utf-8")


async def test_create_public_mcp_writes_overlay_and_hot_reloads(
        client, admin_headers, tmp_path):
    resp = await client.post("/api/v1/admin/mcp", json=HTTP_OK,
                             headers=admin_headers)
    assert resp.status_code == 201
    assert (tmp_path / "data" / "public" / "catalog" / "mcp" / "new-one"
            / "mcp.json").is_file()
    # 热重载：无需重启，安装后面板立即可见（非内置需安装才可见）
    resp = await client.put("/api/v1/me/capabilities/mcp/new-one", json={
        "installed": True}, headers=admin_headers)
    assert resp.status_code == 200
    panel = await client.get("/api/v1/me/mcps/panel", headers=admin_headers)
    assert any(r["id"] == "new-one" for r in panel.json())


async def test_create_duplicate_conflict(client, admin_headers,
                                         seed_public_catalog):
    resp = await client.post("/api/v1/admin/mcp", json={
        "id": "builtin-one", "name": "x", "description": "d",
        "transport": "streamable-http", "url": "https://x.example.com/mcp"},
        headers=admin_headers)
    assert resp.status_code == 409


async def test_create_invalid_manifest_422(client, admin_headers):
    resp = await client.post("/api/v1/admin/mcp", json={
        "id": "bad", "name": "x", "description": "d",
        "transport": "stdio"},  # 缺 command
        headers=admin_headers)
    assert resp.status_code == 422


async def test_list_rows_carry_policy_and_status(client, admin_headers,
                                                 seed_public_catalog):
    resp = await client.get("/api/v1/admin/mcp", headers=admin_headers)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert rows["builtin-one"]["overlaid"] is True  # 数据目录条目即覆盖层
    assert rows["builtin-one"]["status"] == "unchecked"
    assert rows["builtin-one"]["default_enabled"] is False
    assert rows["builtin-one"]["visibility"] == "public"


async def test_edit_writes_overlay_and_hot_reloads(client, admin_headers,
                                                   seed_public_catalog,
                                                   tmp_path):
    resp = await client.put("/api/v1/admin/mcp/builtin-one", json={
        "id": "builtin-one", "name": "改过的内置", "description": "d",
        "transport": "streamable-http", "url": "https://y.example.com/mcp"},
        headers=admin_headers)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in
            (await client.get("/api/v1/admin/mcp", headers=admin_headers)).json()}
    assert rows["builtin-one"]["name"] == "改过的内置"
    # 热重载生效：安装后面板显示新名字
    resp = await client.put("/api/v1/me/capabilities/mcp/builtin-one", json={
        "installed": True}, headers=admin_headers)
    assert resp.status_code == 200
    panel = await client.get("/api/v1/me/mcps/panel", headers=admin_headers)
    assert any(r["name"] == "改过的内置" for r in panel.json())
    # 覆盖文件落数据目录
    data = json.loads((tmp_path / "data" / "public" / "catalog" / "mcp"
                       / "builtin-one" / "mcp.json").read_text(encoding="utf-8"))
    assert data["name"] == "改过的内置"


async def test_edit_id_change_422(client, admin_headers, seed_public_catalog):
    resp = await client.put("/api/v1/admin/mcp/builtin-one", json={
        "id": "renamed", "name": "x", "description": "d",
        "transport": "streamable-http", "url": "https://y.example.com/mcp"},
        headers=admin_headers)
    assert resp.status_code == 422


async def test_delete_builtin_without_overlay_409(client, admin_headers,
                                                  monkeypatch):
    """仓库内置层条目（无覆盖副本）不可删——monkeypatch 确定性模拟。"""
    monkeypatch.setattr(admin_mcp_module, "is_overlaid",
                        lambda *a, **k: False)
    resp = await client.post("/api/v1/admin/mcp", json=HTTP_OK,
                             headers=admin_headers)
    assert resp.status_code == 201
    resp = await client.delete("/api/v1/admin/mcp/new-one",
                               headers=admin_headers)
    assert resp.status_code == 409


async def test_delete_overlaid_entry_ok(client, admin_headers,
                                        seed_public_catalog):
    resp = await client.delete("/api/v1/admin/mcp/builtin-one",
                               headers=admin_headers)
    assert resp.status_code == 200
    rows = {r["id"] for r in
            (await client.get("/api/v1/admin/mcp", headers=admin_headers)).json()}
    assert "builtin-one" not in rows


async def test_delete_unknown_404(client, admin_headers):
    resp = await client.delete("/api/v1/admin/mcp/none-such",
                               headers=admin_headers)
    assert resp.status_code == 404


async def test_reset_overlay_removes_entry(client, admin_headers,
                                           seed_public_catalog):
    """重置 = 删覆盖副本。部署中其下还有仓库内置版时自然回退默认；
    测试环境（仅数据目录层）表现为条目消失。"""
    resp = await client.post("/api/v1/admin/mcp/builtin-one/reset",
                             headers=admin_headers)
    assert resp.status_code == 200
    rows = {r["id"] for r in
            (await client.get("/api/v1/admin/mcp", headers=admin_headers)).json()}
    assert "builtin-one" not in rows


async def test_reset_without_overlay_409(client, admin_headers, monkeypatch):
    monkeypatch.setattr(admin_mcp_module, "is_overlaid",
                        lambda *a, **k: False)
    resp = await client.post("/api/v1/admin/mcp/none-such/reset",
                             headers=admin_headers)
    assert resp.status_code == 409


async def test_non_admin_forbidden(client, user_headers):
    resp = await client.get("/api/v1/admin/mcp", headers=user_headers)
    assert resp.status_code == 403
