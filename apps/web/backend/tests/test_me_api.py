"""「我的」端点：自建技能（本任务）+ 已安装的内置技能。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEV_AUTH_TOKEN", "devtok")
    app = create_app()
    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer devtok"}
BODY = {"name": "my-skill", "description": "自建演示", "content": "## 目标\n演示"}


def test_create_and_list_my_skill(client):
    created = client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    assert created.status_code == 201
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "my-skill")
    assert row["source"] == "mine" and row["enabled"] is True


def test_my_skill_name_conflict_with_builtin(client):
    """自建与内置同名 → 409 而非 422，这条才真正守住 except 顺序。

    "data-analysis" 是内置目录条目；自建同名抛 SkillNameTaken，它继承 ValueError，
    若 API 层把 `except ValueError` 写在 `except SkillNameTaken` 之前，会被截成 422，
    本用例断言 409 即失败。
    """
    dup = client.post("/api/v1/me/skills", json={
        "name": "data-analysis", "description": "我", "content": "## 目标\ny"},
        headers=HEADERS)
    assert dup.status_code == 409


def test_my_skill_invalid_name_is_422_not_409(client):
    """非法 kebab-case → 422 而非 409。"""
    bad = client.post("/api/v1/me/skills", json={
        "name": "Bad_Name", "description": "x", "content": "y"}, headers=HEADERS)
    assert bad.status_code == 422


def test_update_and_delete_only_own(client):
    client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    updated = client.patch("/api/v1/me/skills/my-skill", json={
        "description": "改过", "content": "## 目标\n改"}, headers=HEADERS)
    assert updated.status_code == 200 and updated.json()["description"] == "改过"
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 200
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 404


def test_cannot_edit_or_delete_non_own_skill(client):
    """内置技能不在用户目录里 → 改/删都 403/404，不能动到 catalog。"""
    assert client.patch("/api/v1/me/skills/data-analysis", json={
        "description": "x", "content": "y"}, headers=HEADERS).status_code == 403
    assert client.delete("/api/v1/me/skills/data-analysis", headers=HEADERS).status_code == 404


def test_my_skills_lists_installed_builtin(client):
    """已安装的内置技能出现在「我的」里，且带 enabled 状态。"""
    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"installed": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "data-analysis")
    assert row["source"] == "installed" and row["builtin"] is True and row["enabled"] is True

    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"enabled": False}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "data-analysis")
    assert row["enabled"] is False          # 停用后仍在「我的」里，但状态为停用
    assert row["installed"] is True


def test_my_skills_marks_revoked_installed_item(client):
    """管理员下架后，已安装条目在「我的」里标 revoked（仍列出，但不可用）。"""
    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"installed": True}, headers=HEADERS)
    client.put("/api/v1/admin/catalog/skill/data-analysis/policy",
               json={"visibility": "hidden", "default_enabled": False}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "data-analysis")
    assert row["revoked"] is True


def test_builtin_item_not_in_my_skills(client):
    """内置条目（default_enabled=True）不进「我的」：先安装再切内置，记录被口径忽略。"""
    client.put("/api/v1/me/capabilities/skill/data-analysis",
               json={"installed": True}, headers=HEADERS)
    client.put("/api/v1/admin/catalog/skill/data-analysis/policy",
               json={"visibility": "public", "default_enabled": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    assert all(r["name"] != "data-analysis" for r in rows)


EXPERT = {"name": "化学助手", "avatar": "🧪", "description": "演示",
          "system_prompt": "你是化学助手", "tool_whitelist": ["python.run"]}


def test_create_list_and_delete_my_expert(client):
    created = client.post("/api/v1/me/experts", json=EXPERT, headers=HEADERS)
    assert created.status_code == 201
    expert_id = created.json()["id"]
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    assert any(r["id"] == expert_id and r["source"] == "mine" for r in rows)
    assert client.delete(f"/api/v1/me/experts/{expert_id}",
                         headers=HEADERS).status_code == 200
    assert all(r["id"] != expert_id for r in
               client.get("/api/v1/me/experts", headers=HEADERS).json())


def test_my_experts_get_instantiates_missing_record(client):
    """GET /me/experts 会把「文件在、记录不在」的自建专家补种进 assistants。

    直接 POST 后断言不算数——那是 write() 自己写的记录；必须先抹掉记录，
    才能证明 list_my_experts 里的 ensure_instantiated 真在起作用。
    """
    created = client.post("/api/v1/me/experts", json=EXPERT, headers=HEADERS).json()
    expert_id = created["id"]

    # 抹掉 assistants 记录，制造「文件在、记录不在」的首访态（等价于历史数据/被清理过）。
    # 走 TestClient 的 blocking portal 调 store：与 app 同一条事件循环，避免跨 loop 用连接。
    store = client.app.state.store
    assert client.portal.call(store.delete, "assistants", expert_id) is True
    assert client.portal.call(store.get, "assistants", expert_id) is None

    # 已抹掉 → GET 应把它补回来（证明 ensure_instantiated 生效）
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    assert any(r["id"] == expert_id for r in rows)
    assistants = client.get("/api/v1/assistants", headers=HEADERS).json()
    assert any(a["_id"] == expert_id for a in assistants)


def test_my_experts_lists_installed_builtin_with_avatar(client):
    """已安装的内置专家进「我的」，并带上目录里的头像 emoji（此前恒为空串）。"""
    client.put("/api/v1/me/capabilities/expert/asst-data",
               json={"installed": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == "asst-data")
    assert row["source"] == "installed" and row["builtin"] is True
    assert row["avatar"] == "📊"


def test_my_experts_marks_revoked_installed_item(client):
    """管理员下架后，已安装专家在「我的」里标 revoked（对齐 list_my_skills 口径）。"""
    client.put("/api/v1/me/capabilities/expert/asst-data",
               json={"installed": True}, headers=HEADERS)
    # 未下架时不得带该标记（否则"恒 True"也能过）
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == "asst-data")
    assert row["revoked"] is False

    client.put("/api/v1/admin/catalog/expert/asst-data/policy",
               json={"visibility": "hidden", "default_enabled": False}, headers=HEADERS)
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == "asst-data")
    assert row["revoked"] is True


def test_update_my_expert(client):
    created = client.post("/api/v1/me/experts", json=EXPERT, headers=HEADERS).json()
    resp = client.patch(f"/api/v1/me/experts/{created['id']}",
                        json={**EXPERT, "description": "改过"}, headers=HEADERS)
    assert resp.status_code == 200
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == created["id"])
    assert row["description"] == "改过"


def test_cannot_update_others_expert(client):
    assert client.patch("/api/v1/me/experts/u-other:chem", json=EXPERT,
                        headers=HEADERS).status_code == 404


def test_my_expert_rejects_unknown_tool(client):
    """未注册的工具名必须 422，否则专家会静默失去全部工具。"""
    bad = {**EXPERT, "tool_whitelist": ["python.runx"]}
    assert client.post("/api/v1/me/experts", json=bad, headers=HEADERS).status_code == 422


def test_my_expert_accepts_registered_tool(client):
    """合法工具名仍可建（防"一律 422"的假修复）。"""
    ok = {**EXPERT, "tool_whitelist": ["python.run"]}
    assert client.post("/api/v1/me/experts", json=ok, headers=HEADERS).status_code == 201


# 插件唯一内置条目：spec_agent（catalog/plugins/spec_agent）。用户安装它会把
# spec.* 工具注册进进程级单例 REGISTRY，用例收尾必须回收，否则污染后续用例
# （照抄 tests/test_plugins_api.py 的 clean_plugin_state 模式）。
PLUGIN = "spec_agent"


@pytest.fixture(autouse=True)
def clean_plugin_tools():
    """用例收尾注销本文件安装插件时注册的工具。

    Yields:
        None（仅提供收尾清理）。
    """
    from app.services.tool_registry import REGISTRY

    yield
    for name in list(REGISTRY.names):
        if name.startswith("spec."):
            REGISTRY.unregister(name)


def test_my_plugins_lists_installed_plugin(client):
    """用户安装的插件进「我的插件」，未安装时列表为空。"""
    assert client.get("/api/v1/me/plugins", headers=HEADERS).json() == []
    client.put(f"/api/v1/me/capabilities/plugin/{PLUGIN}",
               json={"installed": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/plugins", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == PLUGIN)
    assert row["source"] == "installed" and row["builtin"] is True
    assert row["enabled"] is True and row["revoked"] is False
    assert row["name"] and row["description"]     # 名称/描述取自目录条目


def test_my_plugins_keeps_revoked_installed_row(client):
    """管理员下架后，已安装插件仍列出并标 revoked，且卸载路径依然通。

    这是本端点的存在理由：市场行被 hidden 过滤掉后，若「我的」也不再返回，
    安装记录就成了用户够不着的孤儿（启动装配还会一直挂载该插件）。
    """
    client.put(f"/api/v1/me/capabilities/plugin/{PLUGIN}",
               json={"installed": True}, headers=HEADERS)
    client.put(f"/api/v1/admin/catalog/plugin/{PLUGIN}/policy",
               json={"visibility": "hidden", "default_enabled": False}, headers=HEADERS)

    # 下架后市场已不再返回该行（孤儿的成因），「我的」必须仍然返回
    market = client.get("/api/v1/market/plugin", headers=HEADERS).json()
    assert all(r["id"] != PLUGIN for r in market)
    rows = client.get("/api/v1/me/plugins", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == PLUGIN)
    assert row["revoked"] is True and row["installed"] is True

    # 下架行的唯一合法动作是卸载（前端详情页按钮走同一端点）
    assert client.put(f"/api/v1/me/capabilities/plugin/{PLUGIN}",
                      json={"installed": False}, headers=HEADERS).status_code == 200
    assert all(r["id"] != PLUGIN for r in
               client.get("/api/v1/me/plugins", headers=HEADERS).json())


def test_builtin_plugin_not_in_my_plugins(client):
    """内置插件（default_enabled=True）不进「我的插件」：先安装再切内置，记录被口径忽略。"""
    client.put(f"/api/v1/me/capabilities/plugin/{PLUGIN}",
               json={"installed": True}, headers=HEADERS)
    client.put(f"/api/v1/admin/catalog/plugin/{PLUGIN}/policy",
               json={"visibility": "public", "default_enabled": True}, headers=HEADERS)
    rows = client.get("/api/v1/me/plugins", headers=HEADERS).json()
    assert all(r["id"] != PLUGIN for r in rows)


def test_my_plugins_skips_stale_record(client):
    """安装记录指向目录里已不存在的插件 → 静默跳过，不 500（对齐技能/专家口径）。"""
    client.put(f"/api/v1/me/capabilities/plugin/{PLUGIN}",
               json={"installed": True}, headers=HEADERS)
    store = client.app.state.store
    installs = client.app.state.capability_service.installs
    # 直接造一条"目录里没有的 id"的安装记录（等价于插件被下架并从仓库删除）
    user_id = client.portal.call(store.list, "user_capabilities")[0]["user_id"]
    client.portal.call(installs.install, user_id, "plugin", "ghost-plugin")

    resp = client.get("/api/v1/me/plugins", headers=HEADERS)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert "ghost-plugin" not in ids and PLUGIN in ids
