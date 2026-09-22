"""共享测试夹具：DocumentStore 双后端参数化 + 完整 app/client/token 工厂。"""
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from app.core.auth import issue_token
from app.core.settings import Settings
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


@pytest.fixture
def seed_public_catalog(tmp_path) -> None:
    """数据目录预置钩子：在 app lifespan 扫描 catalog 之前调用。

    默认不预置任何内容；需要内置测试插件包的测试在本模块覆写本夹具，
    向 `tmp_path/data/public/catalog` 写入包目录即可。

    Args:
        tmp_path: pytest 临时目录夹具（即本 app 的 data_dir 上层）。
    """


def write_dummy_user_plugin(tmp_path) -> str:
    """向数据目录公共 catalog 根写入混合 scope 的测试插件包。

    schema：base_url（scope=admin 必填）/ api_key（用户侧 secret 必填）/
    top_k（用户侧可选文本）——覆盖「管理员公共配置 + 用户侧可填」的组合。

    Args:
        tmp_path: pytest 临时目录夹具（即本 app 的 data_dir 上层）。

    Returns:
        插件 id。
    """
    import json

    pkg = tmp_path / "data" / "public" / "catalog" / "plugins" / "dummy_user_cfg"
    pkg.mkdir(parents=True)
    (pkg / "tools.py").write_text('"""测试插件工具模块（无工具）。"""\n', encoding="utf-8")
    (pkg / "plugin.json").write_text(json.dumps({
        "id": "dummy_user_cfg",
        "name": "Dummy 用户配置插件",
        "version": "1.0.0",
        "description": "测试用混合 scope 插件",
        "tools_module": "tools.py",
        "config_schema": [
            {"key": "base_url", "label": "服务地址", "type": "text",
             "scope": "admin", "required": True},
            {"key": "api_key", "label": "API Key", "type": "password",
             "secret": True, "required": True},
            {"key": "top_k", "label": "Top K", "type": "text"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return "dummy_user_cfg"


@pytest.fixture
async def app(tmp_path, seed_public_catalog):
    """lifespan 完整初始化的测试 app（临时 Settings + sqlite store + repos + 种子助手）。

    Yields:
        已跑 lifespan 的 FastAPI 实例。
    """
    from app.main import create_app

    settings = Settings(
        auth_secret="api-test-secret",
        auth_enabled=True,
        dev_auth_token="devtok",
        storage_backend="sqlite",
        sqlite_path=str(tmp_path / "api.db"),
        data_dir=str(tmp_path / "data"),
        fernet_key=Fernet.generate_key().decode(),  # API 层 key 测试跑真实加解密路径
    )
    application = create_app()
    application.state.settings = settings
    async with application.router.lifespan_context(application):
        yield application


def make_token_headers(app, role: str) -> dict:
    """构造指定角色的 Bearer 请求头。

    Args:
        app: 已初始化 app（取 state.settings 签发）。
        role: admin 或 user。

    Returns:
        Authorization 头字典。
    """
    token = issue_token(
        {"sub": f"u-{role}", "username": f"tester-{role}", "role": role},
        app.state.settings,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(app) -> dict:
    """管理员 token 头。"""
    return make_token_headers(app, "admin")


@pytest.fixture
def user_headers(app) -> dict:
    """普通用户 token 头。"""
    return make_token_headers(app, "user")


@pytest.fixture
async def client(app):
    """httpx 异步客户端（ASGI 直连，app 已跑 lifespan）。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
