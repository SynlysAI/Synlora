"""把 catalog 专家包播种成助手文档。

与插件播种（`PluginService._seed_expert`）的区别：这里播的是"随仓库内置的独立
专家"（`catalog/experts/<dir>/expert.json`）；插件播种的专家（`asst-plugin-*`）
跟随其插件，不在此列。按 `_id` 幂等：已存在跳过（不覆盖管理员改动），缺失补种。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.db.repos import AssistantRepo

if TYPE_CHECKING:
    from app.catalog.loader import ExpertPackage


async def seed_experts(store: Any, experts: dict[str, "ExpertPackage"]) -> None:
    """逐条按 _id 幂等插入内置专家。

    Args:
        store: DocumentStore 实例。
        experts: catalog 专家包（{id: ExpertPackage}）。
    """
    repo = AssistantRepo(store)
    for expert in experts.values():
        if await store.get("assistants", expert.id) is not None:
            continue
        await repo.create({
            "_id": expert.id,
            "name": expert.name,
            "avatar": expert.avatar,
            "description": expert.description,
            "system_prompt": expert.system_prompt,
            "tool_whitelist": list(expert.tool_whitelist),
            "model_provider_id": None,
            "knowledge_base_ids": [],
            "builtin": True,
        })
