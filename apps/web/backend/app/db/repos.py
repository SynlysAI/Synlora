"""基于 DocumentStore 的薄封装 repository 集合。

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


class ProjectRepo(BaseRepo):
    """用户项目（目录名唯一性由 ProjectService 的 free_dir_name 保证，此处不校验）。"""

    collection = "projects"

    async def list_for_user(self, user_id: str) -> list[dict]:
        """列出用户的未归档项目（updated_at 倒序）。

        Args:
            user_id: 用户 sub。

        Returns:
            未归档项目文档列表。
        """
        docs = await self._store.list(self.collection, filters={"user_id": user_id})
        return sorted(
            (d for d in docs if not d.get("archived")),
            key=lambda d: d.get("updated_at", 0), reverse=True,
        )

    async def create(self, *, user_id: str, name: str, dir_name: str) -> dict:
        """新建项目（_id/created_at/updated_at 由 BaseRepo 补齐）。

        Args:
            user_id: 用户 sub。
            name: 项目显示名（不要求唯一）。
            dir_name: 项目目录名（调用方保证可用）。

        Returns:
            新建的项目文档。
        """
        return await super().create({
            "user_id": user_id, "name": name, "dir_name": dir_name, "archived": False,
        })

    async def used_dir_names(self, user_id: str) -> set[str]:
        """该用户仍被占用的目录名（供 free_dir_name 去重）。

        Args:
            user_id: 用户 sub。

        Returns:
            目录名集合。
        """
        docs = await self._store.list(self.collection, filters={"user_id": user_id})
        return {d["dir_name"] for d in docs if d.get("dir_name")}

    async def delete(self, project_id: str) -> bool:
        """删除项目记录。

        Args:
            project_id: 项目 id。

        Returns:
            True 表示确实删掉了记录。
        """
        return await self._store.delete(self.collection, project_id)

    async def rename(self, project_id: str, *, name: str, dir_name: str) -> dict | None:
        """改名（同步更新目录名，updated_at 由 BaseRepo 刷新）。

        Args:
            project_id: 项目 id。
            name: 新显示名。
            dir_name: 新目录名。

        Returns:
            更新后的文档；不存在返回 None。
        """
        return await super().update(project_id, {"name": name, "dir_name": dir_name})


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

    async def abort_stale(self, ended_at: float) -> int:
        """把残留的 status=running 记录收敛为 aborted。

        进程重启后这些 run 必死（run 是进程内 asyncio task），但记录会永远
        停在 running；而 `chat()` 用 DB 计数判 `MAX_RUNS_PER_USER`，残留累积
        会把用户永久顶在"该用户已有 N 个运行中的对话"上，连重启都救不回。

        Args:
            ended_at: 结束时间戳（调用方传 `time.time()`）。

        Returns:
            被清理的记录数。
        """
        docs = await self.list(filters={"status": "running"})
        for doc in docs:
            await self.update(doc["_id"], {"status": "aborted", "ended_at": ended_at})
        return len(docs)


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
    """用户文件记录（user_id/project_id/filename/stored_path/size/mime）。

    Attributes:
        project_id: 文件所属项目 id（C6 之前的历史记录没有该字段；读取方须按磁盘实际
            位置解析归属——带该字段就只认它，缺失才遍历各项目 files/ 找文件，见
            files_api._resolve_file_project）。
        stored_path: 相对**项目根**的存储路径（如 files/a.txt），不是相对 files/。
    """

    collection = "files"


class JobRepo(BaseRepo):
    """后台任务记录（kind/plugin_id/status/session_id/user_id/external_id）。"""

    collection = "jobs"
