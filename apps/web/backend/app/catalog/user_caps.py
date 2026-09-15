"""用户能力安装记录（安装 = 存在记录，不复制任何文件）。

存储形态（集合 user_capabilities，_id = f"{user_id}:{kind}:{item_id}"）：
    {"_id": "u1:plugin:spec_agent", "user_id": "u1", "kind": "plugin",
     "item_id": "spec_agent", "enabled": True, "installed_at": ...}
设计取舍（用户已确认）：内置包留在仓库，安装只写记录——升级即生效、无副本漂移；
enabled=false 表示"已装但停用"（运行期与未装同样不可见）；用户自建内容落
data_root/users/<uid>/（由 SkillService / UserExpertService 管理）。
"""
from __future__ import annotations

import time
from typing import Any

USER_CAPS_COLLECTION = "user_capabilities"


def cap_id(user_id: str, kind: str, item_id: str) -> str:
    """安装记录文档 id。

    Args:
        user_id: 用户 sub。
        kind: expert | skill | plugin。
        item_id: 条目 id。

    Returns:
        f"{user_id}:{kind}:{item_id}"。
    """
    return f"{user_id}:{kind}:{item_id}"


class UserCapabilityRepo:
    """用户能力安装记录读写。"""

    def __init__(self, store: Any) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
        """
        self._store = store

    async def install(self, user_id: str, kind: str, item_id: str) -> None:
        """记录安装（幂等：已存在则不动，也不刷新 installed_at）。

        enabled 缺省 True：装上即启用。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。
        """
        doc_id = cap_id(user_id, kind, item_id)
        if await self._store.get(USER_CAPS_COLLECTION, doc_id) is not None:
            return
        try:
            await self._store.insert(USER_CAPS_COLLECTION, {
                "_id": doc_id, "user_id": user_id, "kind": kind, "item_id": item_id,
                "enabled": True, "installed_at": time.time(),
            })
        except ValueError:
            # 并发下已被他者插入：目标态（存在安装记录）已达成，保持幂等
            return

    async def uninstall(self, user_id: str, kind: str, item_id: str) -> bool:
        """删除安装记录。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            实际删除返回 True；未安装返回 False。
        """
        return await self._store.delete(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id))

    async def is_installed(self, user_id: str, kind: str, item_id: str) -> bool:
        """是否已安装。

        注：运行期可见性判定请用 install_states / is_enabled —— 本方法只看"是否有安装
        记录"，不体现用户的停用动作。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示已安装。
        """
        doc = await self._store.get(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id))
        return doc is not None

    async def set_enabled(self, user_id: str, kind: str, item_id: str,
                          enabled: bool) -> bool:
        """设置已安装条目的启用态。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。
            enabled: True 启用 / False 停用。

        Returns:
            True 表示记录存在并已更新；未安装返回 False。
        """
        doc_id = cap_id(user_id, kind, item_id)
        # update 为部分更新且不 upsert：记录不存在时返回 None（双后端语义一致）
        return await self._store.update(
            USER_CAPS_COLLECTION, doc_id, {"enabled": bool(enabled)}) is not None

    async def is_enabled(self, user_id: str, kind: str, item_id: str) -> bool:
        """已安装条目是否处于启用态（历史文档缺 enabled 字段按 True 处理）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示启用；未安装返回 False。
        """
        doc = await self._store.get(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id))
        if doc is None:
            return False
        return bool(doc.get("enabled", True))

    async def enabled_item_ids(self, user_id: str, kind: str) -> set[str]:
        """某用户某类型下“已安装且启用”的条目 id 集合。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。

        Returns:
            条目 id 集合。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION,
                                     filters={"user_id": user_id})
        return {
            str(d.get("item_id"))
            for d in docs
            if d.get("kind") == kind and d.get("item_id") and bool(d.get("enabled", True))
        }

    async def install_states(self, user_id: str, kind: str) -> dict[str, bool]:
        """某用户某类型下的安装状态（{item_id: 是否启用}）。

        调用方据此区分"未安装"（键不存在）与"已装但停用"（值为 False）；
        一次查询同时给出两个事实，避免调用方分别查 installed 与 enabled。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。

        Returns:
            {item_id: enabled} 映射（只含已安装条目）。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION, filters={"user_id": user_id})
        return {
            str(d.get("item_id")): bool(d.get("enabled", True))
            for d in docs
            if d.get("kind") == kind and d.get("item_id")
        }

    async def installed_plugin_ids(self) -> set[str]:
        """出现过用户安装记录的插件 id（启动装配用：只挂载不涉及可见性）。

        Returns:
            插件 id 集合（去重）。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION)
        return {str(d["item_id"]) for d in docs
                if d.get("kind") == "plugin" and d.get("item_id")}

    async def list_for_user(self, user_id: str, kind: str | None = None) -> list[str]:
        """某用户已安装的条目键列表。

        Args:
            user_id: 用户 sub。
            kind: 可选类型过滤（None = 全部类型）。

        Returns:
            排序后的 "kind:item_id" 列表。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION,
                                     filters={"user_id": user_id})
        keys: list[str] = []
        for d in docs:
            kind_val = str(d.get("kind") or "")
            item_val = str(d.get("item_id") or "")
            if not kind_val or not item_val:
                continue  # 脏文档（缺字段）：跳过，不抛异常
            if kind is None or kind_val == kind:
                keys.append(f"{kind_val}:{item_val}")
        return sorted(keys)
