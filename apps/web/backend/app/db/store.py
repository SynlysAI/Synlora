"""文档存储抽象：协议 + SQLite/MongoDB 双实现。

collection schema 形如 {"users": ["user_id", "seq"]}（第二项为提取为真实列的索引字段）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

COLLECTION_INDEXES: dict[str, list[str]] = {
    "assistants": ["name"],
    "providers": ["name"],
    "sessions": ["user_id", "assistant_id", "updated_at"],
    "events": ["session_id", "seq"],
    "files": ["user_id"],
    "runs": ["session_id", "user_id", "status"],
    "local_users": ["username"],
}


class DocumentStore(Protocol):
    """文档存储协议。"""

    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def insert(self, collection: str, doc: dict) -> dict: ...
    async def get(self, collection: str, doc_id: str) -> dict | None: ...
    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None: ...
    async def delete(self, collection: str, doc_id: str) -> bool: ...
    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]: ...


class SqliteStore:
    """SQLite 文档存储：_id 主键 + doc JSON 列 + 索引字段提取列。"""

    def __init__(self, path: str) -> None:
        """初始化（路径为空时内存库）。"""
        self._path = path
        self._db: aiosqlite.Connection | None = None
        # 读-改-写串行化锁：aiosqlite 单连接下 SELECT→UPDATE 之间存在 await 点，
        # 并发 update 会互相覆盖（丢失更新），insert 同理需防极端交错。
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """建库建表（幂等）。"""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path or ":memory:")
        self._db.row_factory = aiosqlite.Row
        for name, indexes in COLLECTION_INDEXES.items():
            cols = ", ".join(f'"{c}" TEXT' for c in indexes)
            await self._db.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" (_id TEXT PRIMARY KEY, doc TEXT NOT NULL, {cols})'
            )
            for c in indexes:
                await self._db.execute(f'CREATE INDEX IF NOT EXISTS "ix_{name}_{c}" ON "{name}"("{c}")')
        await self._db.commit()

    async def close(self) -> None:
        """关闭连接。"""
        if self._db:
            await self._db.close()

    @staticmethod
    def _row_to_doc(row: aiosqlite.Row) -> dict:
        """行 → 文档。"""
        return json.loads(row["doc"])

    async def insert(self, collection: str, doc: dict) -> dict:
        """插入文档（_id 必填，重复抛 ValueError，含不可序列化类型抛 ValueError）。"""
        async with self._lock:
            doc_id = doc["_id"]
            indexes = COLLECTION_INDEXES.get(collection, [])
            cols = ", ".join(["_id", "doc"] + indexes)
            marks = ", ".join("?" * (2 + len(indexes)))
            try:
                doc_json = json.dumps(doc, ensure_ascii=False)
            except TypeError as exc:
                raise ValueError(f"文档含不可 JSON 序列化类型: {doc.get('_id')}") from exc
            vals: list[Any] = [doc_id, doc_json] + [str(doc.get(c, "")) for c in indexes]
            try:
                await self._db.execute(f'INSERT INTO "{collection}" ({cols}) VALUES ({marks})', vals)
            except aiosqlite.IntegrityError as exc:
                raise ValueError(f"_id 已存在: {doc_id}") from exc
            await self._db.commit()
            return doc

    async def get(self, collection: str, doc_id: str) -> dict | None:
        """按 _id 取文档。"""
        cur = await self._db.execute(f'SELECT doc FROM "{collection}" WHERE _id = ?', (doc_id,))
        row = await cur.fetchone()
        return self._row_to_doc(row) if row else None

    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None:
        """合并更新（读-改-写，全程持锁防并发丢失更新）。"""
        async with self._lock:
            cur = await self._db.execute(f'SELECT doc FROM "{collection}" WHERE _id = ?', (doc_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            doc = self._row_to_doc(row)
            doc.update(fields)
            indexes = COLLECTION_INDEXES.get(collection, [])
            sets = ", ".join(['doc = ?'] + [f'"{c}" = ?' for c in indexes])
            vals: list[Any] = [json.dumps(doc, ensure_ascii=False)] + [str(doc.get(c, "")) for c in indexes] + [doc_id]
            await self._db.execute(f'UPDATE "{collection}" SET {sets} WHERE _id = ?', vals)
            await self._db.commit()
            return doc

    async def delete(self, collection: str, doc_id: str) -> bool:
        """按 _id 删除。"""
        cur = await self._db.execute(f'DELETE FROM "{collection}" WHERE _id = ?', (doc_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]:
        """按索引字段过滤 + 排序（排序与 filters 字段也必须在该集合的索引列中）。"""
        where, vals = "", []
        if filters:
            conds = []
            for k, v in filters.items():
                conds.append(f'"{k}" = ?')
                vals.append(str(v))
            where = "WHERE " + " AND ".join(conds)
        order = ""
        if sort:
            order = "ORDER BY " + ", ".join(f'"{c}" {"ASC" if d >= 0 else "DESC"}' for c, d in sort)
        lim = f"LIMIT {limit}" if limit else ""
        cur = await self._db.execute(
            f'SELECT doc FROM "{collection}" {where} {order} {lim}', vals
        )
        rows = await cur.fetchall()
        return [self._row_to_doc(r) for r in rows]


class MongoStore:
    """MongoDB 文档存储（motor）。filters 直接透传 Mongo 查询。"""

    def __init__(self, uri: str, db: str) -> None:
        """初始化连接参数。"""
        from motor.motor_asyncio import AsyncIOMotorClient  # 延迟导入：非 prod 无需装 motor
        self._client = AsyncIOMotorClient(uri)
        self._db = self._client[db]

    async def init(self) -> None:
        """连通性检查。"""
        await self._db.command("ping")

    async def close(self) -> None:
        """关闭连接。"""
        self._client.close()

    async def insert(self, collection: str, doc: dict) -> dict:
        """插入（重复 _id 抛 ValueError）。"""
        try:
            await self._db[collection].insert_one(dict(doc))
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise ValueError(f"_id 已存在: {doc['_id']}") from exc
            raise
        return doc

    async def get(self, collection: str, doc_id: str) -> dict | None:
        """按 _id 取。"""
        return await self._db[collection].find_one({"_id": doc_id})

    async def update(self, collection: str, doc_id: str, fields: dict) -> dict | None:
        """$set 合并更新。"""
        await self._db[collection].update_one({"_id": doc_id}, {"$set": fields})
        return await self.get(collection, doc_id)

    async def delete(self, collection: str, doc_id: str) -> bool:
        """删除。"""
        r = await self._db[collection].delete_one({"_id": doc_id})
        return r.deleted_count > 0

    async def list(self, collection: str, *, filters: dict | None = None,
                   sort: list[tuple[str, int]] | None = None, limit: int = 0) -> list[dict]:
        """过滤 + 排序 + limit。"""
        cursor = self._db[collection].find(filters or {})
        if sort:
            cursor = cursor.sort([(c, d) for c, d in sort])
        if limit:
            cursor = cursor.limit(limit)
        return [d async for d in cursor]


def create_store(backend: str, *, sqlite_path: str = "", mongodb_uri: str = "",
                 mongodb_db: str = "synlys_agent") -> DocumentStore:
    """按配置构造 store。

    Raises:
        ValueError: backend 未知。
    """
    if backend == "sqlite":
        return SqliteStore(sqlite_path)
    if backend == "mongodb":
        return MongoStore(mongodb_uri, mongodb_db)
    raise ValueError(f"未知 STORAGE_BACKEND: {backend}")
