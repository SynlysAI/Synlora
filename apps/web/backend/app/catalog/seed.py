"""把 catalog 专家包播种成助手文档，并为平台基线技能播种默认策略。

与插件播种（`PluginService._seed_expert`）的区别：这里播的是"随仓库内置的独立
专家"（`catalog/experts/<dir>/expert.json`）；插件播种的专家（`asst-plugin-*`）
跟随其插件，不在此列。

专家播种带自愈升级：文档记 `_seed_hash`（播种内容指纹），重启时
- 记录缺失 → 补种；
- 内容指纹仍等于上次播种值（管理员没编辑过）且 catalog 有变化 → 刷新为 catalog 新版；
- 当前内容 ≠ 上次播种指纹（管理员在后台改过）→ 跳过不覆盖；
- 旧版播种的记录没有 `_seed_hash` → 视为未编辑，一次性刷新并补指纹。

技能策略播种同范式：`DEFAULT_ENABLED_SKILLS` 是平台基线能力（文档解析与办公交付，
官方技能形态），缺省策略是"市场可见 + 需安装"，这里为它们补写 `default_enabled=True`
（全员直接可用）；已有显式策略记录（管理员配置过）一律不动。
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from app.catalog.policy import POLICY_COLLECTION, CatalogPolicyRepo, policy_id
from app.db.repos import AssistantRepo

if TYPE_CHECKING:
    from app.catalog.loader import ExpertPackage

# 平台基线技能（文档四件套 + 探索性数据分析），全员默认启用；
# 其余深度技能（rdkit/matplotlib/DOE 等）走市场，用户按需自行安装
DEFAULT_ENABLED_SKILLS = (
    "docx", "xlsx", "pptx", "pdf", "exploratory-data-analysis",
)

# 参与指纹与同步的内容字段（model_provider_id / knowledge_base_ids 等运行态字段
# 不随 catalog 刷新，避免抹掉用户的绑定）
_SEED_FIELDS = (
    "name", "avatar", "description", "system_prompt",
    "tool_whitelist", "skill_refs", "mcp_refs", "suggested_prompts",
)


def _content_hash(doc: dict) -> str:
    """计算专家内容字段的指纹（判定"管理员是否编辑过"与"catalog 是否有变化"）。

    Args:
        doc: 助手文档（或等价字典）。

    Returns:
        sha256 十六进制摘要。
    """
    payload = json.dumps(
        {k: doc.get(k) for k in _SEED_FIELDS},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expert_body(expert: "ExpertPackage") -> dict:
    """ExpertPackage → assistants 文档的内容字段。

    Args:
        expert: catalog 专家包。

    Returns:
        内容字段字典（不含 _id / builtin / 运行态字段）。
    """
    return {
        "name": expert.name,
        "avatar": expert.avatar,
        "description": expert.description,
        "system_prompt": expert.system_prompt,
        "tool_whitelist": list(expert.tool_whitelist),
        "skill_refs": list(expert.skill_refs),
        "mcp_refs": list(expert.mcp_refs),
        "suggested_prompts": list(expert.suggested_prompts),
    }


async def seed_experts(store: Any, experts: dict[str, "ExpertPackage"]) -> None:
    """按 _id 幂等播种内置专家，未编辑过的记录随 catalog 自愈升级。

    Args:
        store: DocumentStore 实例。
        experts: catalog 专家包（{id: ExpertPackage}）。
    """
    repo = AssistantRepo(store)
    for expert in experts.values():
        body = _expert_body(expert)
        seed_hash = _content_hash(body)
        doc = await store.get("assistants", expert.id)
        if doc is not None:
            recorded = doc.get("_seed_hash")
            if recorded is not None and recorded != _content_hash(doc):
                continue  # 管理员编辑过（内容偏离上次播种指纹）：不覆盖
            if recorded == seed_hash:
                continue  # 无变化
            await store.update("assistants", expert.id,
                               {**body, "_seed_hash": seed_hash})
            continue
        await repo.create({
            "_id": expert.id,
            **body,
            "model_provider_id": None,
            "knowledge_base_ids": [],
            "builtin": True,
            "_seed_hash": seed_hash,
        })


async def seed_skill_policies(store: Any, skills: dict[str, Any]) -> None:
    """为平台基线技能幂等播种默认启用策略。

    Args:
        store: DocumentStore 实例。
        skills: catalog 技能包（{技能名: SkillPackage}，用于校验技能确实存在）。
    """
    repo = CatalogPolicyRepo(store)
    for name in DEFAULT_ENABLED_SKILLS:
        if name not in skills:
            continue  # 目录里没有该技能（被裁剪/改名）：不硬写悬空策略
        if await store.get(POLICY_COLLECTION, policy_id("skill", name)) is not None:
            continue  # 已有显式策略（管理员配置过）：不覆盖
        await repo.set("skill", name, visibility="public", default_enabled=True)
