"""管理员目录策略（可见性 + 默认启用）。

存储形态（集合 catalog_policy，_id = f"{kind}:{item_id}"）：
    {"_id": "plugin:spec_agent", "kind": "plugin", "item_id": "spec_agent",
     "visibility": "public"|"hidden", "default_enabled": true|false,
     "created_at": ..., "updated_at": ...}
缺省（无记录）= public + default_enabled=False：条目在市场可见，但需用户安装后才可用。
"""
from __future__ import annotations

import time
from typing import Any

POLICY_COLLECTION = "catalog_policy"
VISIBILITIES = ("public", "hidden")


def policy_id(kind: str, item_id: str) -> str:
    """策略文档 id。

    Args:
        kind: expert | skill | plugin。
        item_id: 条目 id。

    Returns:
        f"{kind}:{item_id}"。
    """
    return f"{kind}:{item_id}"


class CatalogPolicyRepo:
    """目录策略读写。"""

    def __init__(self, store: Any) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
        """
        self._store = store

    async def get(self, kind: str, item_id: str) -> dict:
        """取条目策略（无记录返回缺省值）。

        Args:
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            {"visibility", "default_enabled"}。
        """
        doc = await self._store.get(POLICY_COLLECTION, policy_id(kind, item_id))
        if doc is None:
            return {"visibility": "public", "default_enabled": False}
        return {
            "visibility": str(doc.get("visibility") or "")
            if str(doc.get("visibility") or "") in VISIBILITIES else "hidden",
            "default_enabled": bool(doc.get("default_enabled", True)),
        }

    async def all_policies(self) -> dict[str, dict]:
        """全部显式配置过的策略（缺省条目不出现在结果里）。

        Returns:
            {f"{kind}:{item_id}": {"visibility", "default_enabled"}}。
        """
        docs = await self._store.list(POLICY_COLLECTION)
        return {
            str(d["_id"]): {
                "visibility": str(d.get("visibility") or "")
                if str(d.get("visibility") or "") in VISIBILITIES else "hidden",
                "default_enabled": bool(d.get("default_enabled", False)),
            }
            for d in docs
        }

    async def set(self, kind: str, item_id: str, *, visibility: str,
                  default_enabled: bool) -> dict:
        """写入/更新条目策略。

        Args:
            kind: 条目类型。
            item_id: 条目 id。
            visibility: public | hidden。
            default_enabled: 是否默认对所有用户启用。

        Returns:
            写入后的策略。

        Raises:
            ValueError: visibility 非法。
        """
        if visibility not in VISIBILITIES:
            raise ValueError(
                f"非法 visibility: {visibility}（可选 {'/'.join(VISIBILITIES)}）")
        doc_id = policy_id(kind, item_id)
        body = {"kind": kind, "item_id": item_id, "visibility": visibility,
                "default_enabled": bool(default_enabled), "updated_at": time.time()}
        if await self._store.get(POLICY_COLLECTION, doc_id) is None:
            try:
                await self._store.insert(
                    POLICY_COLLECTION, {"_id": doc_id, **body, "created_at": time.time()})
            except ValueError:
                # 并发下已被他者插入：退化为更新（id 冲突与"非法 visibility"不是一类错误）
                await self._store.update(POLICY_COLLECTION, doc_id, body)
        else:
            await self._store.update(POLICY_COLLECTION, doc_id, body)
        return {"visibility": visibility, "default_enabled": bool(default_enabled)}
