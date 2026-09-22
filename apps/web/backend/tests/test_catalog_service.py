"""CapabilityService 可见性判定测试。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.catalog.items import CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.policy import CatalogPolicyRepo
from app.catalog.service import CapabilityService
from app.catalog.user_caps import UserCapabilityRepo
from app.core.settings import Settings

SPEC_TOOLS = {"spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"}


@pytest.fixture
def caps(store, tmp_path) -> CapabilityService:
    """组装 CapabilityService（真实 store + 仓库插件包 + 注入工具映射）。"""
    settings = Settings(data_dir=str(tmp_path), fernet_key=Fernet.generate_key().decode())
    return CapabilityService(
        catalog=CatalogService(index=scan_catalog(catalog_roots(settings))),
        policy=CatalogPolicyRepo(store),
        installs=UserCapabilityRepo(store),
        tool_names_by_plugin={"spec_agent": set(SPEC_TOOLS)},
    )


async def test_default_policy_requires_install(caps):
    """缺省策略（public + 非默认）→ 未安装不可见，安装后仅本人可见。"""
    assert await caps.is_visible("u1", "plugin", "spec_agent") is False
    assert await caps.visible_ids("u2", "plugin") == set()

    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.is_visible("u1", "plugin", "spec_agent") is True
    assert await caps.visible_ids("u1", "plugin") == {"spec_agent"}


async def test_hidden_invisible_even_if_installed(caps):
    """hidden → 普通用户不可见（已安装也不可见）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.is_visible("u1", "plugin", "spec_agent") is False
    assert await caps.visible_ids("u1", "plugin") == set()


async def test_not_default_requires_install(caps):
    """public + 非默认 → 未安装不可见，安装后可见，且只影响安装者本人。

    非默认取值已与缺省相同，"未安装不可见"单看是恒真的；先设一次"默认启用"
    作对照，证明策略确实在放行/拦截。
    """
    await caps.policy.set("skill", "rdkit", visibility="public", default_enabled=True)
    assert await caps.is_visible("u1", "skill", "rdkit") is True

    await caps.policy.set("skill", "rdkit", visibility="public", default_enabled=False)
    assert await caps.is_visible("u1", "skill", "rdkit") is False
    await caps.installs.install("u1", "skill", "rdkit")
    assert await caps.is_visible("u1", "skill", "rdkit") is True
    assert await caps.is_visible("u2", "skill", "rdkit") is False


async def test_unknown_item_is_not_visible(caps):
    """目录里不存在的条目一律不可见（防止凭 id 绕过）。"""
    assert await caps.is_visible("u1", "plugin", "nope") is False
    assert await caps.is_visible("u1", "bogus-kind", "spec_agent") is False


async def test_visible_plugin_tools(caps):
    """插件工具名按可见插件推导（运行期工具过滤用）。"""
    # 对照：显式设为"默认启用" ⇒ 未安装即可见。这一半才有判别力——新缺省是
    # public + 非默认，若直接断言"未安装不可见"，策略 PUT 失效也会通过
    await caps.policy.set("plugin", "spec_agent", visibility="public",
                          default_enabled=True)
    assert await caps.visible_tool_names("u1") == SPEC_TOOLS

    # 非默认 ⇒ 未安装不可见（策略真正承担过滤职责），安装后仅本人可见
    await caps.policy.set("plugin", "spec_agent", visibility="public",
                          default_enabled=False)
    assert await caps.visible_tool_names("u1") == set()
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.visible_tool_names("u1") == SPEC_TOOLS


async def test_market_listing_marks_state(caps):
    """市场列表：条目 + 策略 + 安装状态（供 UI 渲染）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="public", default_enabled=False)
    await caps.installs.install("u1", "plugin", "spec_agent")
    items = await caps.market_items("u1", "plugin")
    row = next(i for i in items if i["id"] == "spec_agent")
    assert row["installed"] is True and row["visible"] is True
    assert row["default_enabled"] is False and row["visibility"] == "public"
    assert row["name"] == "Spec_Agent 谱图解析" and row["kind"] == "plugin"


async def test_market_items_hides_hidden_from_users(caps):
    """普通用户视角不出现 hidden 条目；管理员视角出现。

    市场列表含全部非 hidden 插件（如 poly_agent），故按条目存在性断言而非
    精确列表——本测试只关心 spec_agent 的 hidden 语义。
    """
    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    assert "spec_agent" not in {r["id"] for r in await caps.market_items("u1", "plugin")}
    admin_ids = {r["id"] for r in await caps.market_items("admin1", "plugin", admin=True)}
    assert "spec_agent" in admin_ids


async def test_exists_and_can_install(caps):
    """exists 判存在；can_install 对 hidden 条目拒绝。"""
    assert await caps.exists("plugin", "spec_agent") is True
    assert await caps.exists("plugin", "nope") is False
    assert await caps.can_install("u1", "plugin", "spec_agent") is True

    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    assert await caps.can_install("u1", "plugin", "spec_agent") is False
    assert await caps.can_install("u1", "plugin", "nope") is False


async def test_visible_skill_names_includes_plugin_skills(caps):
    """插件技能跟随其插件可见性（不是目录条目也要能算出来）。"""
    # 缺省 = 非默认启用：先给 u1 装上深度技能与插件，才谈得上"可见"
    for name in ("rdkit", "matplotlib", "seaborn"):
        await caps.installs.install("u1", "skill", name)
    await caps.installs.install("u1", "plugin", "spec_agent")

    names = await caps.visible_skill_names("u1")
    assert {"rdkit", "matplotlib", "seaborn"} <= names
    assert "spec-nmr" in names  # 来自 spec_agent 插件的 skills 声明

    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    names_after = await caps.visible_skill_names("u1")
    assert "spec-nmr" not in names_after
    assert "rdkit" in names_after  # 目录技能不受插件策略影响


async def test_market_plugin_rows_include_config_schema(caps):
    """插件行带 config_schema（用户侧市场据此渲染安装表单）。

    scope=admin 的字段是管理员公共配置（管理后台插件页填写），用户侧 schema
    必须过滤为空——用户安装不出配置表单（一键安装），缺配置在调用期报错。
    """
    rows = await caps.market_items("u1", "plugin")
    spec_row = next(r for r in rows if r["id"] == "spec_agent")
    keys = [f["key"] for f in spec_row["config_schema"]]
    assert "base_url" not in keys and "token" not in keys
    # 非插件行不带该字段
    expert_rows = await caps.market_items("u1", "expert")
    assert all("config_schema" not in r for r in expert_rows)


async def test_market_expert_rows_include_avatar(caps):
    """专家行带 avatar（卡片据此渲染 emoji）；技能/插件无此概念，不加该字段。"""
    # 先断言具体 emoji：目录里确有非空头像，否则字段写成常量空串也能通过
    rows = await caps.market_items("u1", "expert")
    row = next(r for r in rows if r["id"] == "asst-data")
    assert row["avatar"] == "📊"
    assert all("avatar" in r for r in rows)   # 每个专家行都有该键（无头像时为 ""）
    # 与 config_schema 同约定：只有专家才加该字段
    assert all("avatar" not in r for r in await caps.market_items("u1", "skill"))


async def test_market_rows_put_default_enabled_first(caps):
    """市场列表内置（default_enabled）条目排前，组内按 id 升序。"""
    # 管理员把一个普通技能配成内置：它应排到全部非内置技能之前
    await caps.policy.set("skill", "seaborn", visibility="public", default_enabled=True)
    rows = await caps.market_items("u1", "skill")
    flags = [r["default_enabled"] for r in rows]
    # True 连续在前（内置组），False 连续在后（列表本身即展示顺序）
    assert flags == sorted(flags, reverse=True)
    builtin_ids = [r["id"] for r in rows if r["default_enabled"]]
    other_ids = [r["id"] for r in rows if not r["default_enabled"]]
    assert builtin_ids == sorted(builtin_ids)
    assert other_ids == sorted(other_ids)
    assert "seaborn" in builtin_ids
    assert all("avatar" not in r for r in await caps.market_items("u1", "plugin"))


async def test_visible_ids_matches_per_item_visibility(caps):
    """批量计算与逐条判定结果一致（优化不改变语义）。"""
    await caps.policy.set("skill", "rdkit", visibility="public", default_enabled=False)
    await caps.installs.install("u1", "skill", "rdkit")
    batch = await caps.visible_ids("u1", "skill")
    per_item = {i.id for i in caps.catalog.list_items("skill")
                if await caps.is_visible("u1", "skill", i.id)}
    assert batch == per_item

    # 停用后仍须一致（上一轮遗漏：这条不变量原先只覆盖了启用态）
    await caps.installs.set_enabled("u1", "skill", "rdkit", False)
    assert await caps.visible_ids("u1", "skill") == {
        i.id for i in caps.catalog.list_items("skill")
        if await caps.is_visible("u1", "skill", i.id)
    }
    # 且该条目确实被判为不可见（防两侧同时退化为"恒可见"而假绿）
    assert await caps.is_visible("u1", "skill", "rdkit") is False


async def test_disabled_install_is_not_visible(caps):
    """已装但停用 → 与未装同等不可见，并进入技能黑名单。"""
    await caps.installs.install("u1", "skill", "rdkit")
    assert "rdkit" in await caps.visible_ids("u1", "skill")
    await caps.installs.set_enabled("u1", "skill", "rdkit", False)
    assert "rdkit" not in await caps.visible_ids("u1", "skill")
    assert "rdkit" in await caps.hidden_skill_names("u1")


async def test_market_items_expose_enabled(caps):
    """市场行暴露 enabled：已装启用 → True/True/True；停用后 → True/False/False。"""
    await caps.installs.install("u1", "skill", "rdkit")
    # 正例先断言：否则 enabled 写成常量 False 也能过（字段本身没被验真）
    rows = await caps.market_items("u1", "skill")
    row = next(r for r in rows if r["id"] == "rdkit")
    assert row["installed"] is True and row["enabled"] is True and row["visible"] is True

    await caps.installs.set_enabled("u1", "skill", "rdkit", False)
    rows = await caps.market_items("u1", "skill")
    row = next(r for r in rows if r["id"] == "rdkit")
    assert row["installed"] is True and row["enabled"] is False and row["visible"] is False


async def test_builtin_item_ignores_user_records(caps):
    """内置条目（default_enabled=True）全员强制可见：历史安装/停用记录一并忽略，
    用户停用压不过"内置"（内置优先，用户侧只读）。"""
    await caps.policy.set("skill", "rdkit", visibility="public", default_enabled=True)
    assert await caps.is_visible("u1", "skill", "rdkit") is True   # 没装：内置
    await caps.installs.install("u1", "skill", "rdkit")
    assert await caps.is_visible("u1", "skill", "rdkit") is True   # 装了：仍内置
    await caps.installs.set_enabled("u1", "skill", "rdkit", False)
    assert await caps.is_visible("u1", "skill", "rdkit") is True   # 停用记录被忽略
    assert "rdkit" in await caps.visible_ids("u1", "skill")
    assert "rdkit" not in await caps.hidden_skill_names("u1")


async def test_disabled_plugin_tools_are_filtered(caps):
    """非内置插件停用后，其工具退出可见集（插件配置注入同理，见 agent_service）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="public",
                          default_enabled=False)
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.visible_tool_names("u1") == SPEC_TOOLS
    await caps.installs.set_enabled("u1", "plugin", "spec_agent", False)
    assert await caps.visible_tool_names("u1") == set()
