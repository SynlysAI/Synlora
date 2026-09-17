"""专家种子（来自 catalog/experts 包）测试。"""
from __future__ import annotations

from pathlib import Path

from app.catalog.loader import scan_catalog
from app.catalog.seed import seed_experts

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
    """按 _id 幂等播种；缺失项补种，已存在项不覆盖。"""
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
    assert research["system_prompt"].startswith("你是 SynlysAgent 科研助手")
    assert research["model_provider_id"] is None
