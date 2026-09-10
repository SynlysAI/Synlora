"""共享测试夹具：DocumentStore 双后端参数化。"""
import os
import uuid

import pytest

from app.db.store import create_store

MONGO_URI = os.environ.get("TEST_MONGODB_URI", "")


@pytest.fixture(params=["sqlite", "mongodb"])
async def store(request, tmp_path):
    """参数化 store 夹具：sqlite 全跑；mongodb 需 TEST_MONGODB_URI，未设置则跳过。

    Args:
        request: pytest 请求对象（取参数化后端名）。
        tmp_path: pytest 临时目录夹具。

    Yields:
        初始化完成的 DocumentStore 实例。
    """
    if request.param == "sqlite":
        s = create_store("sqlite", sqlite_path=str(tmp_path / "t.db"))
    else:
        if not MONGO_URI:
            pytest.skip("无测试 Mongo")
        db_name = f"synlys_store_test_{uuid.uuid4().hex[:8]}"
        s = create_store("mongodb", mongodb_uri=MONGO_URI, mongodb_db=db_name)
    await s.init()
    yield s
    await s.close()
    if request.param == "mongodb":
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(MONGO_URI)
        await client.drop_database(db_name)
        client.close()
