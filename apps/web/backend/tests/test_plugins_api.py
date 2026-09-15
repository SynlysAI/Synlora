"""插件管理 API 测试（列表 / 安装 / 更新配置 / 权限 / 密钥不回传）。"""
from __future__ import annotations

import pytest

from app.services.tool_registry import REGISTRY


@pytest.fixture(autouse=True)
async def clean_plugin_state(app):
    """用例结束后清理：注销插件工具、删配置与播种专家、清内存状态。

    共享注册表是进程级单例，安装测试注册进去的 spec.* 工具必须回收，
    否则会污染后续用例（如"注册表里没有插件工具"的断言）。
    """
    yield
    for name in list(REGISTRY.names):
        if name.startswith("spec."):
            REGISTRY.unregister(name)
    await app.state.store.delete("plugin_configs", "spec_agent")
    await app.state.store.delete("assistants", "asst-plugin-spec_agent")
    service = app.state.plugin_service
    service._configs.pop("spec_agent", None)
    service._installed.discard("spec_agent")
    service._attached.pop("spec_agent", None)


async def test_list_plugins_requires_admin(client, user_headers, admin_headers):
    """列表仅管理员可见（普通用户 403）。"""
    assert (await client.get("/api/v1/plugins", headers=user_headers)).status_code == 403
    resp = await client.get("/api/v1/plugins", headers=admin_headers)
    assert resp.status_code == 200
    items = {p["id"]: p for p in resp.json()}
    assert "spec_agent" in items
    spec = items["spec_agent"]
    assert spec["installed"] is False and spec["configured"] is False
    assert [f["key"] for f in spec["config_schema"]] == ["base_url", "token"]
    assert spec["config_schema"][1]["secret"] is True


async def test_install_requires_base_url(client, admin_headers):
    """缺必填字段 422（消息含字段名），且不产生安装记录。"""
    resp = await client.post("/api/v1/plugins/spec_agent/install",
                             json={"config": {"token": "t"}}, headers=admin_headers)
    assert resp.status_code == 422 and "base_url" in resp.json()["detail"]

    items = {p["id"]: p for p in (await client.get(
        "/api/v1/plugins", headers=admin_headers)).json()}
    assert items["spec_agent"]["installed"] is False


async def test_install_unknown_plugin_404(client, admin_headers):
    """未知插件 id → 404。"""
    resp = await client.post("/api/v1/plugins/nope/install",
                             json={"config": {}}, headers=admin_headers)
    assert resp.status_code == 404


async def test_install_registers_tools_and_never_returns_secret(app, client, admin_headers):
    """安装成功：工具进注册表、专家播种、技能可见、状态里不回传密钥明文。"""
    resp = await client.post(
        "/api/v1/plugins/spec_agent/install",
        json={"config": {"base_url": "http://spec.local", "token": "super-secret"}},
        headers=admin_headers)
    assert resp.status_code == 201
    state = resp.json()
    assert state["installed"] is True and state["configured"] is True
    assert "super-secret" not in str(state)
    assert state["secrets_set"] == {"token": True}
    assert state["config"] == {"base_url": "http://spec.local"}

    assert "spec.nmr.forward" in REGISTRY.names
    assert "spec.nmr.reverse" in REGISTRY.names
    assert "spec.nmr.search" in REGISTRY.names

    experts = await client.get("/api/v1/assistants", headers=admin_headers)
    assert "谱图解析专家" in [a["name"] for a in experts.json()]

    skills = await client.get("/api/v1/skills", headers=admin_headers)
    spec_skill = next(s for s in skills.json() if s["name"] == "spec-nmr")
    assert spec_skill["builtin"] is True

    # 注入回路已接通：AgentService 持有同一个 PluginService（T8）
    assert app.state.agent_service._plugin_service is app.state.plugin_service


async def test_update_config_keeps_secret_when_blank(app, client, admin_headers):
    """更新配置：敏感字段留空保持原值，非敏感字段可改。"""
    await client.post("/api/v1/plugins/spec_agent/install",
                      json={"config": {"base_url": "http://a", "token": "tok-1"}},
                      headers=admin_headers)
    resp = await client.put("/api/v1/plugins/spec_agent/config",
                            json={"config": {"base_url": "http://b", "token": ""}},
                            headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["config"]["base_url"] == "http://b"
    assert resp.json()["secrets_set"] == {"token": True}
    assert app.state.plugin_service.context_extra() == {
        "spec_agent": {"base_url": "http://b", "token": "tok-1"}}


async def test_put_config_before_install_409(client, admin_headers):
    """未安装就更新配置 → 409。"""
    resp = await client.put("/api/v1/plugins/spec_agent/config",
                            json={"config": {"base_url": "http://a"}},
                            headers=admin_headers)
    assert resp.status_code == 409
