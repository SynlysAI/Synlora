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
    await caps.policy.set("skill", "office-doc", visibility="public", default_enabled=True)
    assert await caps.is_visible("u1", "skill", "office-doc") is True

    await caps.policy.set("skill", "office-doc", visibility="public", default_enabled=False)
    assert await caps.is_visible("u1", "skill", "office-doc") is False
    await caps.installs.install("u1", "skill", "office-doc")
    assert await caps.is_visible("u1", "skill", "office-doc") is True
    assert await caps.is_visible("u2", "skill", "office-doc") is False


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
    """普通用户视角不出现 hidden 条目；管理员视角出现。"""
    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    assert await caps.market_items("u1", "plugin") == []
    admin_rows = await caps.market_items("admin1", "plugin", admin=True)
    assert [r["id"] for r in admin_rows] == ["spec_agent"]


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
    # 缺省 = 非默认启用：先给 u1 装上内置技能与插件，才谈得上"可见"
    for name in ("data-analysis", "pdf-extraction", "office-doc"):
        await caps.installs.install("u1", "skill", name)
    await caps.installs.install("u1", "plugin", "spec_agent")

    names = await caps.visible_skill_names("u1")
    assert {"data-analysis", "pdf-extraction", "office-doc"} <= names
    assert "spec-nmr" in names  # 来自 spec_agent 插件的 skills 声明

    await caps.policy.set("plugin", "spec_agent", visibility="hidden", default_enabled=False)
    names_after = await caps.visible_skill_names("u1")
    assert "spec-nmr" not in names_after
    assert "data-analysis" in names_after  # 内置技能不受插件策略影响


async def test_market_plugin_rows_include_config_schema(caps):
    """插件行带 config_schema（用户侧市场据此渲染安装表单）。"""
    rows = await caps.market_items("u1", "plugin")
    spec_row = next(r for r in rows if r["id"] == "spec_agent")
    assert [f["key"] for f in spec_row["config_schema"]] == ["base_url", "token"]
    # 非插件行不带该字段
    expert_rows = await caps.market_items("u1", "expert")
    assert all("config_schema" not in r for r in expert_rows)


async def test_visible_ids_matches_per_item_visibility(caps):
    """批量计算与逐条判定结果一致（优化不改变语义）。"""
    await caps.policy.set("skill", "office-doc", visibility="public", default_enabled=False)
    await caps.installs.install("u1", "skill", "office-doc")
    batch = await caps.visible_ids("u1", "skill")
    per_item = {i.id for i in caps.catalog.list_items("skill")
                if await caps.is_visible("u1", "skill", i.id)}
    assert batch == per_item

    # 停用后仍须一致（上一轮遗漏：这条不变量原先只覆盖了启用态）
    await caps.installs.set_enabled("u1", "skill", "office-doc", False)
    assert await caps.visible_ids("u1", "skill") == {
        i.id for i in caps.catalog.list_items("skill")
        if await caps.is_visible("u1", "skill", i.id)
    }
    # 且该条目确实被判为不可见（防两侧同时退化为"恒可见"而假绿）
    assert await caps.is_visible("u1", "skill", "office-doc") is False


async def test_disabled_install_is_not_visible(caps):
    """已装但停用 → 与未装同等不可见，并进入技能黑名单。"""
    await caps.installs.install("u1", "skill", "office-doc")
    assert "office-doc" in await caps.visible_ids("u1", "skill")
    await caps.installs.set_enabled("u1", "skill", "office-doc", False)
    assert "office-doc" not in await caps.visible_ids("u1", "skill")
    assert "office-doc" in await caps.hidden_skill_names("u1")


async def test_market_items_expose_enabled(caps):
    """市场行暴露 enabled：已装启用 → True/True/True；停用后 → True/False/False。"""
    await caps.installs.install("u1", "skill", "office-doc")
    # 正例先断言：否则 enabled 写成常量 False 也能过（字段本身没被验真）
    rows = await caps.market_items("u1", "skill")
    row = next(r for r in rows if r["id"] == "office-doc")
    assert row["installed"] is True and row["enabled"] is True and row["visible"] is True

    await caps.installs.set_enabled("u1", "skill", "office-doc", False)
    rows = await caps.market_items("u1", "skill")
    row = next(r for r in rows if r["id"] == "office-doc")
    assert row["installed"] is True and row["enabled"] is False and row["visible"] is False


async def test_user_disable_overrides_default_enabled(caps):
    """已装条目停用后，即使策略是"默认启用"，也按不可见处理。"""
    await caps.policy.set("skill", "office-doc", visibility="public", default_enabled=True)
    assert await caps.is_visible("u1", "skill", "office-doc") is True   # 没装：默认启用
    await caps.installs.install("u1", "skill", "office-doc")
    assert await caps.is_visible("u1", "skill", "office-doc") is True   # 装了且启用
    await caps.installs.set_enabled("u1", "skill", "office-doc", False)
    assert await caps.is_visible("u1", "skill", "office-doc") is False  # 停用优先
    assert "office-doc" not in await caps.visible_ids("u1", "skill")
    assert "office-doc" in await caps.hidden_skill_names("u1")


async def test_disabled_plugin_tools_are_filtered(caps):
    """停用插件后，其工具退出可见集（插件配置注入同理，见 agent_service）。"""
    await caps.policy.set("plugin", "spec_agent", visibility="public",
                          default_enabled=True)
    await caps.installs.install("u1", "plugin", "spec_agent")
    assert await caps.visible_tool_names("u1") == SPEC_TOOLS
    await caps.installs.set_enabled("u1", "plugin", "spec_agent", False)
    assert await caps.visible_tool_names("u1") == set()
