"""用户 MCP API 测试。"""

import pytest


@pytest.mark.asyncio
async def test_mcp_crud_does_not_return_secret(client):
    """MCP CRUD 可用且不会回传 Bearer Token 明文。"""
    headers = {"Authorization": "Bearer devtok"}
    created = await client.post("/api/v1/me/mcps", headers=headers, json={
        "id": "lab-db",
        "name": "实验数据库",
        "description": "查询实验数据",
        "url": "https://mcp.example.test/api",
        "headers": {"X-Tenant": "demo"},
        "bearer_token": "secret-token",
    })
    assert created.status_code == 201
    assert created.json()["bearer_token_set"] is True
    assert "secret-token" not in created.text

    rows = await client.get("/api/v1/me/mcps", headers=headers)
    assert rows.status_code == 200
    assert rows.json()[0]["header_names"] == ["X-Tenant"]

    updated = await client.patch(
        "/api/v1/me/mcps/lab-db", headers=headers,
        json={"description": "更新说明", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    deleted = await client.delete("/api/v1/me/mcps/lab-db", headers=headers)
    assert deleted.status_code == 200
