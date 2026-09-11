"""六个 repository + 种子助手（基于 DocumentStore 的薄封装）。

统一模式：create 自动补 _id/created_at/updated_at；update 自动补 updated_at；
get/list/delete 直接透传 store。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from cryptography.fernet import InvalidToken
from synlys_harness import EventType, SessionEvent

from app.core.crypto import decrypt_key, encrypt_key


def _new_id() -> str:
    """生成 12 位 hex 文档 id。"""
    return uuid.uuid4().hex[:12]


class BaseRepo:
    """单 collection 通用 CRUD 薄封装。"""

    collection: str = ""

    def __init__(self, store: Any) -> None:
        """保存 store。

        Args:
            store: DocumentStore 实例。
        """
        self._store = store

    async def create(self, doc: dict) -> dict:
        """插入文档（自动补 _id/created_at/updated_at）。

        Args:
            doc: 文档字段（已含 _id 时尊重之）。

        Returns:
            插入后的完整文档。
        """
        body = dict(doc)
        body.setdefault("_id", _new_id())
        now = time.time()
        body.setdefault("created_at", now)
        body["updated_at"] = now
        return await self._store.insert(self.collection, body)

    async def get(self, doc_id: str) -> dict | None:
        """按 id 取文档。"""
        return await self._store.get(self.collection, doc_id)

    async def list(self, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None,
                   limit: int = 0) -> list[dict]:
        """过滤/排序/限量列表（参数透传 store）。"""
        return await self._store.list(self.collection, filters=filters, sort=sort, limit=limit)

    async def update(self, doc_id: str, fields: dict) -> dict | None:
        """合并更新（自动补 updated_at）。

        Args:
            doc_id: 文档 id。
            fields: 待合并字段。

        Returns:
            更新后的文档；不存在返回 None。
        """
        body = {**fields, "updated_at": time.time()}
        return await self._store.update(self.collection, doc_id, body)

    async def delete(self, doc_id: str) -> bool:
        """按 id 删除文档。"""
        return await self._store.delete(self.collection, doc_id)


class ProviderRepo(BaseRepo):
    """模型 provider 配置（api_key 写前 Fernet 加密）。"""

    collection = "providers"

    def __init__(self, store: Any, fernet_key: str = "") -> None:
        """保存 store 与加密 key。

        Args:
            store: DocumentStore 实例。
            fernet_key: Fernet key（为空时明文存储，开发模式）。
        """
        super().__init__(store)
        self._fernet_key = fernet_key

    async def create(self, doc: dict) -> dict:
        """插入 provider（api_key 加密为 api_key_enc，不存明文字段）。

        Args:
            doc: provider 字段，可含明文 api_key。

        Returns:
            插入后的完整文档（无明文 api_key 字段）。
        """
        body = dict(doc)
        api_key = body.pop("api_key", "")
        if api_key:
            enc, encrypted = encrypt_key(api_key, self._fernet_key)
        else:
            enc, encrypted = "", False
        body["api_key_enc"] = enc
        body["api_key_encrypted"] = encrypted
        return await super().create(body)

    async def update_with_key(self, doc_id: str, fields: dict,
                              api_key: str | None = None) -> dict | None:
        """合并更新 provider（api_key 提供时重加密覆写）。

        Args:
            doc_id: provider id。
            fields: 待合并字段（忽略其中可能混入的明文 api_key）。
            api_key: 新明文 key（None 表示不变；空串表示清除）。

        Returns:
            更新后的完整文档（无明文 api_key 字段）；不存在返回 None。
        """
        body = {k: v for k, v in fields.items() if k != "api_key"}
        if api_key is not None:
            if api_key:
                enc, encrypted = encrypt_key(api_key, self._fernet_key)
            else:
                enc, encrypted = "", False
            body["api_key_enc"] = enc
            body["api_key_encrypted"] = encrypted
        return await super().update(doc_id, body)

    async def get_public(self, doc_id: str) -> dict | None:
        """公共视图：去掉密文字段，带 has_key 标志。

        Args:
            doc_id: 文档 id。

        Returns:
            无 api_key_enc/api_key 字段的文档副本；不存在返回 None。
        """
        doc = await self.get(doc_id)
        if doc is None:
            return None
        public = {k: v for k, v in doc.items() if k != "api_key_enc"}
        public["has_key"] = bool(doc.get("api_key_enc"))
        return public

    async def get_decrypted(self, doc_id: str) -> dict | None:
        """解密副本（组装 ModelProviderConfig 用）。

        Args:
            doc_id: 文档 id。

        Returns:
            含明文 api_key 字段的文档副本；不存在返回 None。
        """
        doc = await self.get(doc_id)
        if doc is None:
            return None
        out = {k: v for k, v in doc.items() if k != "api_key_enc"}
        stored = doc.get("api_key_enc", "")
        if not stored:
            out["api_key"] = ""
            return out
        try:
            out["api_key"] = decrypt_key(stored, self._fernet_key,
                                         bool(doc.get("api_key_encrypted")))
        except (InvalidToken, ValueError) as exc:
            # key 轮换/缺失（如加密数据配空 key 时 Fernet 构造抛 ValueError）统一转友好错误，
            # 避免底层异常裸抛到 API 层。
            raise RuntimeError("api_key 解密失败：FERNET_KEY 与加密时不一致或缺失") from exc
        return out


class AssistantRepo(BaseRepo):
    """助手配置（builtin 种子不可删）。"""

    collection = "assistants"

    async def delete(self, doc_id: str) -> bool:
        """删除助手（builtin=True 抛 ValueError）。

        Raises:
            ValueError: 内置助手不可删除。
        """
        doc = await self.get(doc_id)
        if doc is None:
            return False
        if doc.get("builtin"):
            raise ValueError("内置助手不可删除")
        return await self._store.delete(self.collection, doc_id)


class EventRepo(BaseRepo):
    """会话事件副本（_id=f"{session_id}:{seq}"，seq 数值升序读取）。"""

    collection = "events"

    async def append(self, session_id: str, event: SessionEvent) -> dict:
        """追加事件文档。

        Args:
            session_id: 会话 id。
            event: harness 会话事件。

        Returns:
            插入的事件文档。
        """
        doc = {
            "_id": f"{session_id}:{event.seq}",
            "session_id": session_id,
            "seq": event.seq,
            "type": event.type.value,
            "payload": event.payload,
            "ts": event.ts,
        }
        return await self._store.insert(self.collection, doc)

    async def list_events(self, session_id: str) -> list[SessionEvent]:
        """按 session_id 取事件，Python 端按 seq 数值升序排序。

        说明：sqlite 索引列为 TEXT，store 层按 seq 排序是字典序（"10"<"2"），
        必须在此修正为数值序。

        Args:
            session_id: 会话 id。

        Returns:
            seq 升序的 SessionEvent 列表。
        """
        docs = await self._store.list(self.collection, filters={"session_id": session_id})
        return [
            SessionEvent(seq=d["seq"], type=EventType(d["type"]),
                         payload=d["payload"], ts=d["ts"])
            for d in sorted(docs, key=lambda d: int(d["seq"]))
        ]


class RunRepo(BaseRepo):
    """对话运行记录（status: running|completed|aborted|failed）。"""

    collection = "runs"


class SessionRepo(BaseRepo):
    """会话元数据（user_id/assistant_id/title/archived/message_count）。"""

    collection = "sessions"

    def __init__(self, store: Any) -> None:
        """保存 store 与计数串行锁。

        Args:
            store: DocumentStore 实例。
        """
        super().__init__(store)
        # bump 的 get→update 之间存在 await 间隙，并发调用会读到同值互覆盖；
        # 依赖 app 单例 repo（app.state.session_repo），实例锁即全局串行化。
        self._count_lock = asyncio.Lock()

    async def bump_message_count(self, session_id: str, delta: int = 1) -> dict | None:
        """原子增减 message_count（锁内读-改-写，并发调用不丢更新）。

        Args:
            session_id: 会话 id。
            delta: 增量（默认 1，可为负）。

        Returns:
            更新后的会话文档；会话不存在返回 None。
        """
        async with self._count_lock:
            doc = await self.get(session_id)
            if doc is None:
                return None
            count = int(doc.get("message_count", 0)) + delta
            # 直接走 store.update 并显式补 updated_at：
            # sessions 的索引列含 updated_at，需同步刷新。
            return await self._store.update("sessions", session_id, {
                "message_count": count, "updated_at": time.time()})


class FileRepo(BaseRepo):
    """用户文件记录（user_id/path/size/mime/sha256）。"""

    collection = "files"


SEED_ASSISTANTS: list[dict] = [
    {
        "_id": "asst-research",
        "name": "科研助手",
        "avatar": "🧪",
        "description": "文献检索、数据分析、文件处理通用科研助手",
        "system_prompt": (
            "你是 SynlysAgent 科研助手，帮助科研人员完成文献检索、数据分析、"
            "文件处理等任务。回答保持准确、简洁，需要时主动使用工具。"
        ),
        "tool_whitelist": [
            "file.read", "file.write", "file.list",
            "python.run", "knowledge.search", "http.request",
        ],
        "builtin": True,
    },
    {
        "_id": "asst-data",
        "name": "数据分析助手",
        "avatar": "📊",
        "description": "优先用 python.run 做统计分析与可视化",
        "system_prompt": (
            "你是数据分析助手。优先使用 python.run 工具对用户上传的数据做统计分析"
            "与可视化，结果图表保存到 output/ 目录并在回复中说明结论。"
        ),
        "tool_whitelist": ["python.run", "file.read", "file.write", "file.list"],
        "builtin": True,
    },
]


async def seed_assistants(store: Any) -> None:
    """逐条按 _id 幂等插入种子助手（已存在的跳过，缺失的补种自愈）。

    Args:
        store: DocumentStore 实例。
    """
    repo = AssistantRepo(store)
    for seed in SEED_ASSISTANTS:
        if await store.get("assistants", seed["_id"]) is None:
            await repo.create(dict(seed))
