"""可见性在运行期与列表 API 的落地测试。

覆盖：列表 API（技能/助手）按可见性过滤、用户市场安装、管理员策略写入、
运行期工具集过滤，以及「缺省策略 = public + 默认启用 ⇒ 升级后行为不变」的兼容线。
"""
from __future__ import annotations

import asyncio

import pytest
from synlys_harness import ModelProviderConfig, TextDelta, Usage

from app.core.auth import issue_token
from app.services import agent_service as agent_service_mod
from app.services.tool_registry import REGISTRY

SPEC_TOOLS = {"spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"}
BUILTIN_SKILLS = {"data-analysis", "pdf-extraction", "office-doc"}
PLUGIN_EXPERT = "谱图解析专家"


class _NoopBackend:
    """最小后端：产一条文本即收尾（只为让 run 正常完成）。"""

    def __init__(self, provider) -> None:
        """记录 provider。"""
        self.provider = provider

    async def stream(self, messages, tools=None):
        """产出结束所需的最小事件序列。"""
        yield TextDelta(text="ok")
        yield Usage()


def _capture_run_args(monkeypatch) -> list[dict]:
    """包住 agent_service.RunSession，记录每次构造的实参。

    Args:
        monkeypatch: pytest monkeypatch 夹具。

    Returns:
        逐轮累积的 kwargs 列表。
    """
    captured: list[dict] = []
    real_run_session = agent_service_mod.RunSession

    def _wrapper(*args, **kwargs):
        """记录 kwargs 后转交真实 RunSession。"""
        captured.append(kwargs)
        return real_run_session(*args, **kwargs)

    monkeypatch.setattr(agent_service_mod, "RunSession", _wrapper)
    return captured


def _headers(app, sub: str, role: str = "user") -> dict:
    """为任意 sub 签发请求头（用例需要"另一个普通用户"）。

    Args:
        app: 已初始化 app（取 settings 签发）。
        sub: 用户 sub。
        role: 角色。

    Returns:
        Authorization 头字典。
    """
    token = issue_token({"sub": sub, "username": sub, "role": role},
                        app.state.settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def clean_capability_state(app):
    """用例收尾清理：回收插件工具、删策略/安装/配置记录、清插件服务内存态。

    共享注册表是进程级单例，安装测试注册进去的 spec.* 工具必须回收，否则会
    污染后续用例（照抄 tests/test_plugins_api.py 的 clean_plugin_state 模式）。
    """
    yield
    for name in list(REGISTRY.names):
        if name.startswith("spec."):
            REGISTRY.unregister(name)
    store = app.state.store
    for collection in ("catalog_policy", "user_capabilities", "plugin_configs"):
        for doc in await store.list(collection):
            await store.delete(collection, doc["_id"])
    await store.delete("assistants", "asst-plugin-spec_agent")
    service = app.state.plugin_service
    service._configs.pop("spec_agent", None)
    service._installed.discard("spec_agent")
    service._attached.pop("spec_agent", None)


async def _install_plugin(client, admin_headers) -> None:
    """以管理员安装 spec_agent（注册工具、挂技能根、播种专家）。

    Args:
        client: httpx 异步客户端。
        admin_headers: 管理员请求头。
    """
    resp = await client.post("/api/v1/plugins/spec_agent/install",
                             json={"config": {"base_url": "http://spec.local"}},
                             headers=admin_headers)
    assert resp.status_code == 201, resp.text


async def _make_provider(client, admin_headers) -> dict:
    """建一个可用模型服务（运行期过滤用例需要 provider_cfg）。

    Args:
        client: httpx 异步客户端。
        admin_headers: 管理员请求头。

    Returns:
        provider 文档。
    """
    resp = await client.post("/api/v1/models", headers=admin_headers, json={
        "name": "enforce-mock", "base_url": "https://api.enforce.local/v1",
        "api_key": "sk-enforce", "model_id": "gpt-enforce", "enabled": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _wait_run_done(app, run_id: str, timeout_s: float = 5.0) -> None:
    """轮询 DB 直到该 run 结束。

    Args:
        app: 已初始化 app。
        run_id: 运行 id。
        timeout_s: 轮询超时。
    """
    for _ in range(int(timeout_s / 0.02)):
        run = await app.state.store.get("runs", run_id)
        if run and run["status"] != "running":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{timeout_s}s 内 run 未结束: {run_id}")


# ---------- 缺省策略：升级后行为完全不变 ----------


async def test_default_policy_keeps_previous_behavior(
        app, client, admin_headers, user_headers):
    """不动策略时：内置技能、插件播种技能、专家、插件一律对普通用户可见。"""
    await _install_plugin(client, admin_headers)

    skills = {s["name"] for s in
              (await client.get("/api/v1/skills", headers=user_headers)).json()}
    assert BUILTIN_SKILLS <= skills
    assert "spec-nmr" in skills  # 插件技能跟随插件可见性（缺省可见）

    names = {a["name"] for a in
             (await client.get("/api/v1/assistants", headers=user_headers)).json()}
    assert {"科研助手", "数据分析助手", PLUGIN_EXPERT} <= names

    catalog = (await client.get("/api/v1/catalog", headers=user_headers)).json()
    plugins = {r["id"]: r for r in catalog if r["kind"] == "plugin"}
    assert plugins["spec_agent"]["visible"] is True
    assert plugins["spec_agent"]["default_enabled"] is True


# ---------- 非默认插件：安装后才对该用户可见 ----------


async def test_not_default_plugin_needs_user_install(
        app, client, admin_headers, user_headers):
    """public + default_enabled=False：未安装不可见，安装后可见且不影响他人。"""
    await _install_plugin(client, admin_headers)
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "public", "default_enabled": False},
                     headers=admin_headers)

    skills = {s["name"] for s in
              (await client.get("/api/v1/skills", headers=user_headers)).json()}
    assert "spec-nmr" not in skills and "data-analysis" in skills
    names = {a["name"] for a in
             (await client.get("/api/v1/assistants", headers=user_headers)).json()}
    assert PLUGIN_EXPERT not in names
    # 管理员不受过滤影响
    admin_skills = {s["name"] for s in
                    (await client.get("/api/v1/skills", headers=admin_headers)).json()}
    assert "spec-nmr" in admin_skills
    admin_names = {a["name"] for a in
                   (await client.get("/api/v1/assistants", headers=admin_headers)).json()}
    assert PLUGIN_EXPERT in admin_names

    assert (await client.post("/api/v1/catalog/plugin/spec_agent/install",
                              json={}, headers=user_headers)).status_code == 201
    skills = {s["name"] for s in
              (await client.get("/api/v1/skills", headers=user_headers)).json()}
    assert "spec-nmr" in skills
    other = _headers(app, "u-other")
    other_skills = {s["name"] for s in
                    (await client.get("/api/v1/skills", headers=other)).json()}
    assert "spec-nmr" not in other_skills


# ---------- hidden：用户视同不存在，管理员仍可见 ----------


async def test_hidden_plugin_absent_from_user_catalog(
        app, client, admin_headers, user_headers):
    """hidden：普通用户市场不含它、install 404；管理员后台仍能看到。"""
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)

    rows = (await client.get("/api/v1/catalog", headers=user_headers)).json()
    assert "spec_agent" not in {r["id"] for r in rows if r["kind"] == "plugin"}
    assert (await client.post("/api/v1/catalog/plugin/spec_agent/install",
                              json={}, headers=user_headers)).status_code == 404

    admin_rows = (await client.get("/api/v1/admin/catalog", headers=admin_headers)).json()
    row = next(r for r in admin_rows
               if r["kind"] == "plugin" and r["id"] == "spec_agent")
    assert row["visibility"] == "hidden" and row["visible"] is False


# ---------- 技能 / 专家 策略过滤 ----------


async def test_skill_and_expert_policies_filter_lists(
        client, admin_headers, user_headers):
    """技能与专家列表按各自策略过滤；管理员不过滤。"""
    await client.put("/api/v1/admin/catalog/skill/office-doc/policy",
                     json={"visibility": "public", "default_enabled": False},
                     headers=admin_headers)
    await client.put("/api/v1/admin/catalog/expert/asst-data/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)

    skills = {s["name"] for s in
              (await client.get("/api/v1/skills", headers=user_headers)).json()}
    assert "office-doc" not in skills and "data-analysis" in skills
    names = {a["name"] for a in
             (await client.get("/api/v1/assistants", headers=user_headers)).json()}
    assert "数据分析助手" not in names and "科研助手" in names

    assert "office-doc" in {s["name"] for s in
                            (await client.get("/api/v1/skills",
                                              headers=admin_headers)).json()}
    assert "数据分析助手" in {a["name"] for a in
                             (await client.get("/api/v1/assistants",
                                               headers=admin_headers)).json()}


# ---------- 用户自建助手与用户维度插件配置 ----------


async def test_user_assistant_visible_and_personal_config_overrides(
        app, client, admin_headers, user_headers):
    """用户自建助手不受过滤；插件个人配置覆盖公共配置且互不串号。"""
    created = await client.post("/api/v1/assistants", headers=admin_headers,
                                json={"name": "自定义助手", "system_prompt": "p"})
    assert created.status_code == 201
    names = {a["name"] for a in
             (await client.get("/api/v1/assistants", headers=user_headers)).json()}
    assert "自定义助手" in names

    await client.post("/api/v1/plugins/spec_agent/install",
                      json={"config": {"base_url": "http://public", "token": "pub"}},
                      headers=admin_headers)
    resp = await client.post("/api/v1/catalog/plugin/spec_agent/install",
                             json={"config": {"base_url": "http://mine", "token": "mine"}},
                             headers=user_headers)
    assert resp.status_code == 201

    store = app.state.plugin_config_store
    assert await store.resolved_for_user("u-user", "spec_agent") == {
        "base_url": "http://mine", "token": "mine"}
    assert await store.resolved_for_user("u-other", "spec_agent") == {
        "base_url": "http://public", "token": "pub"}


# ---------- 安装/卸载与参数校验 ----------


async def test_install_missing_required_config_422(app, client, user_headers):
    """带 config 安装插件时缺必填字段 422，且不产生安装记录。"""
    resp = await client.post("/api/v1/catalog/plugin/spec_agent/install",
                             json={"config": {"token": "t"}}, headers=user_headers)
    assert resp.status_code == 422 and "base_url" in resp.json()["detail"]

    rows = (await client.get("/api/v1/catalog", headers=user_headers)).json()
    row = next(r for r in rows if r["kind"] == "plugin" and r["id"] == "spec_agent")
    assert row["installed"] is False


async def test_install_unknown_kind_and_uninstall(client, user_headers):
    """未知类型 404；卸载返回 removed 布尔且幂等。"""
    assert (await client.post("/api/v1/catalog/bogus/x/install", json={},
                              headers=user_headers)).status_code == 404
    await client.post("/api/v1/catalog/plugin/spec_agent/install", json={},
                      headers=user_headers)
    first = await client.delete("/api/v1/catalog/plugin/spec_agent/install",
                                headers=user_headers)
    assert first.status_code == 200 and first.json()["removed"] is True
    second = await client.delete("/api/v1/catalog/plugin/spec_agent/install",
                                 headers=user_headers)
    assert second.json()["removed"] is False


async def test_admin_policy_validation_and_permissions(client, admin_headers, user_headers):
    """visibility 非法 422、条目不存在 404；管理端点普通用户 403。"""
    bad = await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                           json={"visibility": "bogus"}, headers=admin_headers)
    assert bad.status_code == 422
    missing = await client.put("/api/v1/admin/catalog/plugin/nope/policy",
                               json={"visibility": "public"}, headers=admin_headers)
    assert missing.status_code == 404
    assert (await client.get("/api/v1/admin/catalog",
                             headers=user_headers)).status_code == 403
    assert (await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                             json={"visibility": "public"},
                             headers=user_headers)).status_code == 403


# ---------- 运行期工具集过滤 ----------


async def test_runtime_tool_names_filtered_by_visibility(
        app, client, admin_headers, user_headers, monkeypatch, tmp_path):
    """hidden 插件的工具不进本轮工具集，内置工具照旧（运行期装配断言）。"""
    await _install_plugin(client, admin_headers)  # 把 spec.* 注册进共享注册表
    provider = await _make_provider(client, admin_headers)
    monkeypatch.setattr("app.services.agent_service.OpenAICompatibleBackend",
                        _NoopBackend)
    # 工具映射为 PluginService 的活引用（装配时注入方法本身），此处按挂载记录
    # 注入本轮要断言的工具名（与 install 后 _attach 的写入同语义）
    app.state.plugin_service._attached["spec_agent"] = set(SPEC_TOOLS)
    captured = _capture_run_args(monkeypatch)

    decrypted = await app.state.provider_repo.get_decrypted(provider["_id"])
    cfg = ModelProviderConfig(
        name=decrypted["name"], base_url=decrypted["base_url"],
        api_key=decrypted["api_key"], model_id=decrypted["model_id"])
    user = {"sub": "u-user", "username": "tester-user", "role": "user"}

    async def _run_one(label: str) -> list[str]:
        """发起一轮无专家对话（工具放开全部注册表），返回装配后的工具名。

        Args:
            label: 消息文本（仅用于区分轮次）。

        Returns:
            AgentConfig.tool_names。
        """
        sid = (await client.post("/api/v1/sessions", headers=user_headers,
                                 json={"model_provider_id": provider["_id"]})).json()["_id"]
        run_id = await app.state.agent_service.chat(
            sid, user, None, cfg, label, workspace_root=tmp_path / label)
        await _wait_run_done(app, run_id)
        return captured[-1]["config"].tool_names

    default_tools = await _run_one("默认策略")
    assert SPEC_TOOLS <= set(default_tools)  # 缺省可见 → 插件工具保留

    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)
    hidden_tools = set(await _run_one("隐藏后"))
    assert not (SPEC_TOOLS & hidden_tools)  # 不可见 → 插件工具被剔除
    for name in ("python.run", "file.read", "skill.list"):
        assert name in hidden_tools  # 内置工具不受可见性影响


async def test_tool_mapping_reflects_runtime_install(app, client, admin_headers):
    """装配期传活引用：运行期新挂载的工具即时进入映射（不再依赖启动快照）。"""
    caps = app.state.capability_service
    plugin_service = app.state.plugin_service

    # 起点：尚未挂载任何插件，映射为空（固定快照会永远停留在此）
    assert plugin_service.tool_names_by_plugin() == {}

    # 运行期新装插件：工具在 install 时挂载，映射应即时反映
    await _install_plugin(client, admin_headers)
    assert "spec.nmr.forward" in caps.tool_names_by_plugin.get("spec_agent", set())

    # 模拟运行期新增挂载：直接往 PluginService 的已挂载记录里加一个假工具名
    plugin_service._attached.setdefault("spec_agent", set()).add("spec.nmr.fake")
    try:
        assert "spec.nmr.fake" in caps.tool_names_by_plugin["spec_agent"]
    finally:
        plugin_service._attached["spec_agent"].discard("spec.nmr.fake")
    assert "spec.nmr.fake" not in caps.tool_names_by_plugin["spec_agent"]


# ---------- 安装即挂载：能力可用性与可见性解耦 ----------


async def test_user_install_registers_previously_uninstalled_plugin(
        app, client, admin_headers, user_headers):
    """用户自行安装"管理员从未公共安装"的插件：工具被注册、技能可读。

    注册（attach）= 能力可用性（进程级，谁装都该挂）；可见性 = 按用户过滤
    （CapabilityService），是另一层的事。插件在管理员侧为非默认（用户须自己
    安装才可见），但历史缺口是：个人安装只写记录、不挂资源 ⇒ 该用户其实用不了。
    """
    plugin_service = app.state.plugin_service
    assert "spec.nmr.forward" not in REGISTRY.names  # 起手未挂载（无公共配置）

    # 管理员把插件设为 public + 非默认（真实场景：用户自己在能力中心安装）
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "public", "default_enabled": False},
                     headers=admin_headers)

    resp = await client.post("/api/v1/catalog/plugin/spec_agent/install",
                             json={"config": {"base_url": "http://mine"}},
                             headers=user_headers)
    assert resp.status_code == 201

    assert "spec.nmr.forward" in REGISTRY.names
    assert plugin_service.ensure_attached("spec_agent") is True  # 幂等
    caps = app.state.capability_service
    assert "spec.nmr.forward" in await caps.visible_tool_names("u-user")
    names = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=user_headers)).json()]
    assert "spec-nmr" in names
    # 另一个用户仍不可见（插件非默认且未安装）
    assert await caps.visible_tool_names("u-other") == set()


async def test_startup_attaches_plugins_with_only_user_installs(store, tmp_path):
    """重启装配：只有用户个人安装、无公共配置的插件也要挂载。"""
    from cryptography.fernet import Fernet
    from synlys_harness import ToolRegistry, register_builtin_tools

    from app.catalog.user_caps import UserCapabilityRepo
    from app.core.settings import Settings
    from app.db.repos import AssistantRepo
    from app.plugins.config_store import PluginConfigStore
    from app.plugins.loader import plugin_roots, scan_plugins
    from app.plugins.service import PluginService
    from app.services.skill_service import SkillService

    settings = Settings(data_dir=str(tmp_path), fernet_key=Fernet.generate_key().decode())
    packages = scan_plugins(plugin_roots(settings))
    registry = ToolRegistry()
    register_builtin_tools(registry)
    user_caps = UserCapabilityRepo(store)
    await user_caps.install("u1", "plugin", "spec_agent")  # 仅个人安装，无公共配置

    # 启动装配的取数口径：该插件必须出现在"额外挂载"集合里
    assert await user_caps.installed_plugin_ids() == {"spec_agent"}

    service = PluginService(
        registry=registry, config_store=PluginConfigStore(store, settings.fernet_key),
        packages=packages, skill_service=SkillService(tmp_path / "data"),
        assistant_repo=AssistantRepo(store))
    await service.startup(extra_plugin_ids=await user_caps.installed_plugin_ids())
    assert "spec.nmr.forward" in registry.names
    assert service.ensure_attached("spec_agent") is True  # 幂等：重复挂载不抛
    # 无公共配置 ⇒ 不进公共配置缓存（用户维度配置由 agent_service 另算）
    assert service.context_extra() == {}


# ---------- 技能过滤：黑名单口径（公共技能始终可见） ----------


async def test_public_admin_skill_stays_visible_to_users(
        app, client, admin_headers, user_headers):
    """管理员在公共技能目录自建的技能（非能力目录条目）对所有用户可见。"""
    app.state.skill_service.write_skill(
        name="team-convention", description="团队约定", content="正文内容")
    try:
        names = [s["name"] for s in (await client.get(
            "/api/v1/skills", headers=user_headers)).json()]
        assert "team-convention" in names
    finally:
        app.state.skill_service.delete_skill("team-convention")


async def test_hidden_builtin_skill_invisible_to_user(
        app, client, admin_headers, user_headers):
    """被策略隐藏的内置技能：普通用户不可见，管理员可见。"""
    await client.put("/api/v1/admin/catalog/skill/office-doc/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)
    user_names = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=user_headers)).json()]
    assert "office-doc" not in user_names
    admin_names = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=admin_headers)).json()]
    assert "office-doc" in admin_names


async def test_plugin_skill_follows_plugin_policy(app, client, admin_headers, user_headers):
    """插件技能跟随其插件：插件被隐藏时技能也不可见。"""
    await _install_plugin(client, admin_headers)
    await client.put("/api/v1/admin/catalog/plugin/spec_agent/policy",
                     json={"visibility": "hidden", "default_enabled": False},
                     headers=admin_headers)
    names = [s["name"] for s in (await client.get(
        "/api/v1/skills", headers=user_headers)).json()]
    assert "spec-nmr" not in names
