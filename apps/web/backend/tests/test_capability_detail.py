"""能力详情接口测试（GET /api/v1/me/capabilities/{kind}/{item_id}）。"""
from __future__ import annotations


async def test_builtin_expert_detail(client, user_headers):
    """内置专家详情：返回 system_prompt / avatar / tool_whitelist，未安装时 origin='market'。"""
    res = await client.get("/api/v1/me/capabilities/expert/asst-data", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "expert"
    assert body["id"] == "asst-data"
    assert body["origin"] == "market"
    assert body["avatar"] == "📊"
    assert "数据分析助手" in body["system_prompt"]
    assert "python.run" in body["tool_whitelist"]


async def test_builtin_skill_detail(client, user_headers):
    """内置技能详情：返回 SKILL.md 正文。"""
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "skill"
    assert body["id"] == "office-doc"
    assert body["content"].strip() != ""


async def test_builtin_plugin_detail(client, user_headers):
    """内置插件详情：返回配置字段声明与附属清单。"""
    res = await client.get("/api/v1/me/capabilities/plugin/spec_agent", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "plugin"
    assert isinstance(body["config_schema"], list)
    assert isinstance(body["tools"], list)
    assert isinstance(body["experts"], list)
    assert isinstance(body["skills"], list)


async def test_installed_item_origin_installed(client, user_headers):
    """已安装条目 origin='installed'，enabled 反映安装态。"""
    install = await client.put(
        "/api/v1/me/capabilities/skill/office-doc",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "installed"
    assert body["enabled"] is True


async def test_my_skill_detail_origin_mine(client, user_headers):
    """自建技能详情：origin='mine' 且带正文。"""
    created = await client.post("/api/v1/me/skills", headers=user_headers, json={
        "name": "my-detail-skill",
        "description": "详情测试技能",
        "content": "# 我的技能\n\n正文若干。",
    })
    assert created.status_code == 201
    res = await client.get("/api/v1/me/capabilities/skill/my-detail-skill", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "mine"
    assert body["description"] == "详情测试技能"
    assert "正文若干" in body["content"]


async def test_my_expert_detail_origin_mine(client, user_headers):
    """自建专家详情：origin='mine' 且带回 system_prompt（编辑回填依赖此字段）。"""
    created = await client.post("/api/v1/me/experts", headers=user_headers, json={
        "name": "我的专家",
        "avatar": "🚀",
        "description": "自建",
        "system_prompt": "你是测试专家。",
        "tool_whitelist": ["python.run"],
    })
    assert created.status_code == 201
    expert_id = created.json()["id"]
    res = await client.get(f"/api/v1/me/capabilities/expert/{expert_id}", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "mine"
    assert body["system_prompt"] == "你是测试专家。"
    assert body["tool_whitelist"] == ["python.run"]


async def test_mine_isolation(client, user_headers, admin_headers):
    """他人自建技能对本用户 404（多租户隔离）。"""
    await client.post("/api/v1/me/skills", headers=user_headers, json={
        "name": "my-private-skill", "description": "私密", "content": "# x",
    })
    res = await client.get("/api/v1/me/capabilities/skill/my-private-skill", headers=admin_headers)
    assert res.status_code == 404


async def test_hidden_item_not_found(client, admin_headers, user_headers):
    """hidden 条目对普通用户 404（不泄露存在性）。"""
    put = await client.put(
        "/api/v1/admin/catalog/skill/pdf-extraction/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": False},
    )
    assert put.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/pdf-extraction", headers=user_headers)
    assert res.status_code == 404


async def test_unknown_kind_and_id_not_found(client, user_headers):
    """未知类型与未知条目一律 404。"""
    assert (await client.get(
        "/api/v1/me/capabilities/nope/x", headers=user_headers)).status_code == 404
    assert (await client.get(
        "/api/v1/me/capabilities/skill/no-such-skill", headers=user_headers)).status_code == 404
