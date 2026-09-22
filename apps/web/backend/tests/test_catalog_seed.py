"""专家种子（来自 catalog/experts 包）与技能默认策略播种测试。"""
from __future__ import annotations

from pathlib import Path

from app.catalog.loader import scan_catalog
from app.catalog.policy import CatalogPolicyRepo
from app.catalog.seed import seed_experts, seed_skill_policies

REPO_CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def _experts():
    """仓库 catalog 里的专家包。"""
    return scan_catalog([REPO_CATALOG]).experts


async def test_experts_come_from_catalog(store):
    """专家包来自 catalog/experts（不再是代码常量）。"""
    experts = _experts()
    assert set(experts) == {"asst-research", "asst-data"}
    assert experts["asst-research"].name == "科研助手"
    assert "python.run" in experts["asst-research"].tool_whitelist
    assert experts["asst-research"].avatar == "🧪"
    assert experts["asst-data"].name == "数据分析助手"


async def test_seed_experts_idempotent_and_self_healing(store):
    """按 _id 幂等播种；缺失项补种，管理员编辑过的项不覆盖。"""
    experts = _experts()
    await seed_experts(store, experts)
    await seed_experts(store, experts)
    assert len(await store.list("assistants")) == 2

    await store.update("assistants", "asst-research", {"name": "改过的名字"})
    await seed_experts(store, experts)
    assert (await store.get("assistants", "asst-research"))["name"] == "改过的名字"

    await store.delete("assistants", "asst-data")
    await seed_experts(store, experts)
    assert await store.get("assistants", "asst-data") is not None


async def test_seed_experts_upgrades_unedited_records(store):
    """catalog 升级后：未编辑的记录刷新为新版（含提示词/技能引用），编辑过的保留。"""
    experts = _experts()
    await seed_experts(store, experts)

    # 模拟 catalog 升级：改人设与技能引用后重新播种
    upgraded = dict(experts)
    pkg = experts["asst-research"]
    upgraded["asst-research"] = type(pkg)(
        **{**{
            "id": pkg.id, "name": pkg.name, "avatar": pkg.avatar,
            "description": pkg.description, "system_prompt": "新版人设",
            "tool_whitelist": pkg.tool_whitelist, "skill_refs": ["pdf", "rdkit"],
            "mcp_refs": pkg.mcp_refs, "suggested_prompts": pkg.suggested_prompts,
            "directory": pkg.directory,
        }}
    )
    await seed_experts(store, upgraded)
    refreshed = await store.get("assistants", "asst-research")
    assert refreshed["system_prompt"] == "新版人设"
    assert refreshed["skill_refs"] == ["pdf", "rdkit"]

    # 管理员编辑过（内容偏离播种指纹）的另一条：升级不被强推
    await store.update("assistants", "asst-data", {"name": "我改的"})
    await seed_experts(store, experts)
    assert (await store.get("assistants", "asst-data"))["name"] == "我改的"


async def test_seed_experts_migrates_legacy_records_without_hash(store):
    """旧版播种的记录（无 _seed_hash）：视为未编辑，重启时一次性刷新并补指纹。"""
    experts = _experts()
    await seed_experts(store, experts)

    # 抹掉指纹模拟旧版记录（内容仍是旧播种值 = 未编辑）
    await store.update("assistants", "asst-data", {"_seed_hash": None})

    upgraded = dict(experts)
    pkg = experts["asst-data"]
    upgraded["asst-data"] = type(pkg)(
        **{**{
            "id": pkg.id, "name": pkg.name, "avatar": pkg.avatar,
            "description": pkg.description, "system_prompt": "迁移后的新版",
            "tool_whitelist": pkg.tool_whitelist, "skill_refs": pkg.skill_refs,
            "mcp_refs": pkg.mcp_refs, "suggested_prompts": pkg.suggested_prompts,
            "directory": pkg.directory,
        }}
    )
    await seed_experts(store, upgraded)
    doc = await store.get("assistants", "asst-data")
    assert doc["system_prompt"] == "迁移后的新版"
    assert doc["_seed_hash"]


def _skills():
    """仓库 catalog 里的技能包。"""
    return scan_catalog([REPO_CATALOG]).skills


async def test_seed_skill_policies_default_enables_baseline(store):
    """基线技能（文档四件套 + EDA）补种为默认启用；深度技能不写策略；幂等且不覆盖管理员配置。"""
    repo = CatalogPolicyRepo(store)
    await seed_skill_policies(store, _skills())
    await seed_skill_policies(store, _skills())  # 幂等

    for name in ("docx", "xlsx", "pptx", "pdf", "exploratory-data-analysis"):
        assert await repo.get("skill", name) == {
            "visibility": "public", "default_enabled": True}
    # 深度技能走市场：无策略记录（缺省 = 需安装）
    assert await repo.get("skill", "rdkit") == {
        "visibility": "public", "default_enabled": False}
    assert len(await store.list("catalog_policy")) == 5

    # 管理员显式关掉后，重启播种不得翻回来
    await repo.set("skill", "xlsx", visibility="public", default_enabled=False)
    await seed_skill_policies(store, _skills())
    assert await repo.get("skill", "xlsx") == {
        "visibility": "public", "default_enabled": False}


async def test_seeded_experts_are_builtin_and_have_whitelist(store):
    """播种的专家标记 builtin=True，且工具白名单与包一致。"""
    await seed_experts(store, _experts())
    research = await store.get("assistants", "asst-research")
    assert research["builtin"] is True
    assert sorted(research["tool_whitelist"]) == sorted([
        "file.read", "file.write", "file.list", "python.run", "shell.run",
        "knowledge.list", "knowledge.search", "http.request", "skill.list",
        "skill.read", "ask_user", "file.send", "job.submit", "job.status",
        "job.list", "job.cancel",
    ])
    assert research["system_prompt"].startswith("你是 Synlora 科研助手")
    assert research["model_provider_id"] is None
