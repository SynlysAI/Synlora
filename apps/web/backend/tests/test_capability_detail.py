"""能力详情接口测试（GET /api/v1/me/capabilities/{kind}/{item_id}）+ 用户插件个人配置更新。

个人配置更新（PUT /api/v1/me/plugins/{id}/config）与详情回显同属"详情页编辑配置"
一条链，故合在本文件：详情给表单预填值与已配置标记，更新端点写回。
"""
from __future__ import annotations

import pytest

# 插件唯一内置条目：spec_agent（catalog/plugins/spec_agent）。用户安装它会把
# spec.* 工具注册进进程级单例 REGISTRY，用例收尾必须回收，否则污染后续用例
# （照抄 tests/test_plugins_api.py 的 clean_plugin_state 模式）。
PLUGIN = "spec_agent"


@pytest.fixture(autouse=True)
def clean_plugin_tools():
    """收尾注销本文件安装插件时注册的 spec.* 工具。

    Yields:
        None（仅提供收尾清理）。
    """
    from app.services.tool_registry import REGISTRY

    yield
    for name in list(REGISTRY.names):
        if name.startswith("spec."):
            REGISTRY.unregister(name)


async def _install_spec_agent(client, headers: dict, **config) -> None:
    """以给定个人配置安装 spec_agent（校验安装路径本身可用）。

    Args:
        client: 异步测试客户端。
        headers: 请求头。
        **config: 个人配置字段（base_url 等）。
    """
    res = await client.post(f"/api/v1/catalog/plugin/{PLUGIN}/install",
                            headers=headers, json={"config": config})
    assert res.status_code == 201


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


async def test_revoked_installed_item_detail_readable(client, admin_headers, user_headers):
    """已安装后被下架的条目详情仍可读（origin='installed' + revoked=True）。

    列表（/me/skills）刻意保留该行供用户卸载，详情若 404 则用户无路可走；
    先安装再下架——hidden 之后连安装都进不来。
    """
    install = await client.put(
        "/api/v1/me/capabilities/skill/data-analysis",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    put = await client.put(
        "/api/v1/admin/catalog/skill/data-analysis/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": False},
    )
    assert put.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/data-analysis", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "installed"
    assert body["revoked"] is True


async def test_revoked_installed_item_can_be_uninstalled(client, admin_headers, user_headers):
    """下架但已安装的条目仍可卸载（PUT installed=false），卸载后详情回到 404。

    「我的」列表刻意保留下架行，卸载是它唯一动作；若这里 404，用户就没有
    任何途径把该行清掉。卸载掉记录即不再豁免，详情恢复 404。
    """
    install = await client.put(
        "/api/v1/me/capabilities/skill/data-analysis",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    put = await client.put(
        "/api/v1/admin/catalog/skill/data-analysis/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": False},
    )
    assert put.status_code == 200
    res = await client.put(
        "/api/v1/me/capabilities/skill/data-analysis",
        headers=user_headers, json={"installed": False},
    )
    assert res.status_code == 200
    assert res.json() == {
        "kind": "skill", "id": "data-analysis", "installed": False, "enabled": False,
    }
    detail = await client.get(
        "/api/v1/me/capabilities/skill/data-analysis", headers=user_headers)
    assert detail.status_code == 404


async def test_hidden_uninstalled_switch_still_not_found(client, admin_headers, user_headers):
    """hidden 且未安装：安装与卸载一律 404（卸载豁免不得放大到未安装者）。"""
    put = await client.put(
        "/api/v1/admin/catalog/skill/pdf-extraction/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": False},
    )
    assert put.status_code == 200
    uninstall = await client.put(
        "/api/v1/me/capabilities/skill/pdf-extraction",
        headers=user_headers, json={"installed": False},
    )
    assert uninstall.status_code == 404
    install = await client.put(
        "/api/v1/me/capabilities/skill/pdf-extraction",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 404


async def test_hidden_builtin_with_stale_install_detail_not_found(app, client,
                                                                  admin_headers,
                                                                  user_headers):
    """hidden + 内置 + 残留安装记录：详情仍 404（该组合在任何列表里都不可达）。

    构造顺序：先在可安装态装下（内置态下 409 装不了），再让管理员一并改为
    hidden + default_enabled——正是"安装记录残留"的由来。此时 /me/skills 跳过
    内置行、市场被 hidden 挡住，详情不该再豁免（旧口径只看 installed 就会放行）。
    """
    install = await client.put(
        "/api/v1/me/capabilities/skill/data-analysis",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    put = await client.put(
        "/api/v1/admin/catalog/skill/data-analysis/policy",
        headers=admin_headers, json={"visibility": "hidden", "default_enabled": True},
    )
    assert put.status_code == 200
    # 前置确认组合成立：安装记录确实还在（否则该用例只是复读 hidden+未安装）
    assert await app.state.capability_service.installs.is_installed(
        "u-user", "skill", "data-analysis") is True
    assert (await client.get(
        "/api/v1/me/capabilities/skill/data-analysis",
        headers=user_headers)).status_code == 404


async def test_normal_item_has_no_revoked_flag(client, user_headers):
    """正常条目不带 revoked 键（该标记只在有意义时出现）。"""
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    assert "revoked" not in res.json()


async def test_builtin_item_origin_builtin(client, admin_headers, user_headers):
    """管理员配为内置（default_enabled=True）的条目：origin='builtin' 且恒为启用态。"""
    put = await client.put(
        "/api/v1/admin/catalog/skill/pdf-extraction/policy",
        headers=admin_headers, json={"visibility": "public", "default_enabled": True},
    )
    assert put.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/pdf-extraction", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "builtin"
    assert body["enabled"] is True


async def test_installed_but_disabled_item(client, user_headers):
    """已安装但停用：origin='installed' 且 enabled=False。"""
    install = await client.put(
        "/api/v1/me/capabilities/skill/office-doc",
        headers=user_headers, json={"installed": True},
    )
    assert install.status_code == 200
    disable = await client.put(
        "/api/v1/me/capabilities/skill/office-doc",
        headers=user_headers, json={"enabled": False},
    )
    assert disable.status_code == 200
    res = await client.get("/api/v1/me/capabilities/skill/office-doc", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "installed"
    assert body["enabled"] is False


async def test_my_expert_detail_isolation(client, user_headers, admin_headers):
    """他人自建专家对本用户 404（多租户隔离；专家走另一条解析路径）。"""
    created = await client.post("/api/v1/me/experts", headers=user_headers, json={
        "name": "私密专家",
        "description": "私密",
        "system_prompt": "你是私密专家。",
    })
    assert created.status_code == 201
    expert_id = created.json()["id"]
    assert (await client.get(
        f"/api/v1/me/capabilities/expert/{expert_id}",
        headers=user_headers)).status_code == 200
    assert (await client.get(
        f"/api/v1/me/capabilities/expert/{expert_id}",
        headers=admin_headers)).status_code == 404


async def test_plugin_detail_returns_personal_config_without_secret(client, user_headers):
    """详情回显个人配置：非敏感字段给明文，敏感字段只给"已配置"布尔，明文不出库。"""
    await _install_spec_agent(client, user_headers,
                              base_url="http://u.local", token="u-secret")
    res = await client.get(f"/api/v1/me/capabilities/plugin/{PLUGIN}", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "installed"
    assert body["config"] == {"base_url": "http://u.local"}
    assert body["secrets_set"] == {"token": True}
    assert "u-secret" not in res.text


async def test_plugin_detail_without_personal_layer_has_no_config_keys(client, user_headers):
    """未安装（市场可见）的插件不带 config / secrets_set：没有个人层可编辑。

    前端据此不渲染「编辑配置」入口；若给了空表，反而会显示一个存不进去的入口。
    """
    res = await client.get(f"/api/v1/me/capabilities/plugin/{PLUGIN}", headers=user_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "market"
    assert "config" not in body and "secrets_set" not in body


async def test_update_plugin_config_changes_non_secret(client, user_headers):
    """更新非敏感字段：写回个人层，响应给出可直接刷新的最新快照。"""
    await _install_spec_agent(client, user_headers,
                              base_url="http://a", token="tok-1")
    res = await client.put(f"/api/v1/me/plugins/{PLUGIN}/config",
                           headers=user_headers, json={"config": {"base_url": "http://b"}})
    assert res.status_code == 200
    assert res.json() == {
        "kind": "plugin", "id": PLUGIN,
        "config": {"base_url": "http://b"}, "secrets_set": {"token": True},
    }
    detail = await client.get(
        f"/api/v1/me/capabilities/plugin/{PLUGIN}", headers=user_headers)
    assert detail.json()["config"] == {"base_url": "http://b"}


async def test_update_plugin_config_blank_secret_keeps_stored_value(app, client, user_headers):
    """敏感字段留空 = 保持已存值（核心行为）：既不回显明文，也不因空串被清掉。"""
    await _install_spec_agent(client, user_headers,
                              base_url="http://a", token="tok-1")
    res = await client.put(f"/api/v1/me/plugins/{PLUGIN}/config", headers=user_headers,
                           json={"config": {"base_url": "http://b", "token": "   "}})
    assert res.status_code == 200
    assert res.json()["config"] == {"base_url": "http://b"}
    assert res.json()["secrets_set"] == {"token": True}
    assert "tok-1" not in res.text
    # 直读存储：旧凭证确实还在（"留空保持不变"必须落到库上，而不只是响应好看）
    assert await app.state.plugin_config_store.resolved_for_user("u-user", PLUGIN) == {
        "base_url": "http://b", "token": "tok-1"}


async def test_update_plugin_config_not_installed_404(app, client, user_headers,
                                                      admin_headers):
    """未安装（含"别人装过"）→ 404：归属以安装记录为准，且不写任何人个人层。"""
    # 先验未安装就写 → 404（不是 422/409，与未知插件同一响应，不泄露存在性）
    assert (await client.put(f"/api/v1/me/plugins/{PLUGIN}/config",
                             headers=user_headers,
                             json={"config": {"base_url": "http://x"}})).status_code == 404
    # 另一个用户装过，不等于本用户可写：管理员（u-admin）依旧 404
    await _install_spec_agent(client, user_headers, base_url="http://a", token="tok-1")
    assert (await client.put(f"/api/v1/me/plugins/{PLUGIN}/config",
                             headers=admin_headers,
                             json={"config": {"base_url": "http://hijack"}})).status_code == 404
    assert await app.state.plugin_config_store.get_doc(
        "user:u-admin:" + PLUGIN) is None


async def test_update_plugin_config_unknown_plugin_404(client, user_headers):
    """未知插件 id → 404（不因"未安装"而误报 422）。"""
    res = await client.put("/api/v1/me/plugins/no-such-plugin/config",
                           headers=user_headers, json={"config": {"a": "b"}})
    assert res.status_code == 404
