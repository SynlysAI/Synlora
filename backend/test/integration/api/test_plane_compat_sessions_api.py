"""通过真实 HTTP 与 PostgreSQL 验证 Plane v1 Sessions 兼容面主链路。"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.main import app
from server.utils.auth_middleware import get_current_user, get_db
from yuxi.storage.postgres.manager import PostgresManager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """该测试使用独立 Schema，不依赖运行中的 API 数据库版本。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """该测试不创建知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """该测试不创建 Sandbox 资源。"""
    yield


async def _seed(session_factory):
    """种子数据：联邦用户与默认智能体。"""
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (username, uid, password_hash, role, is_deleted, login_failed_count)"
                " VALUES ('plane_it', 'plane:it-user', 'x', 'user', 0, 0)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO agents (slug, backend_id, name, pics, config_json, share_config, is_default, is_subagent)"
                " VALUES ('default-chatbot', 'chatbot', 'IT', '[]'::jsonb, '{}'::jsonb,"
                ' (\'{ "version": 2, "read_scope": { "access_level": "global",'
                ' "department_ids": [], "user_uids": [] } }\')::jsonb, TRUE, FALSE)'
            )
        )
        await session.commit()


async def test_plane_v1_sessions_http_round_trip(monkeypatch):
    """会话 CRUD、run 提交幂等、事件回放与取消在真实 HTTP+PG 上闭环。"""
    schema = f"pytest_plane_api_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = scoped_engine
    manager._initialized = True
    session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)

    async def provide_db():
        async with session_factory() as session:
            yield session

    test_user = SimpleNamespace(id=1, uid="plane:it-user", username="plane_it", role="user")

    async def provide_user(authorization: str | None = Header(None)):
        # 模拟真实中间件行为：无凭证返回 None（端点统一 401）
        if not authorization:
            return None
        return test_user

    await manager.create_business_tables()
    await _seed(session_factory)

    import yuxi.services.plane_session_compat_service as compat_service

    submitted: list[dict] = []

    async def fake_submit(*, request_input, current_user, db):
        submitted.append({"request_id": request_input.request_id, "policy": request_input.queue_policy})
        return {"run_id": "run-http-1", "status": "dispatched"}

    monkeypatch.setattr(compat_service, "submit_agent_request", fake_submit)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_current_user] = provide_user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["deployment_id"]

            unauth = await client.get("/api/v1/sessions")
            assert unauth.status_code == 401

            created = await client.post(
                "/api/v1/sessions",
                json={"title": "IT 会话", "approval_mode": "ask_for_dangerous"},
                headers={"Authorization": "Bearer test"},
            )
            assert created.status_code == 201
            session_doc = created.json()
            thread_id = session_doc["_id"]
            assert session_doc["origin"] == "plane"
            assert session_doc["approval_mode"] == "ask_for_dangerous"

            listed = await client.get("/api/v1/sessions", headers={"Authorization": "Bearer test"})
            assert [item["_id"] for item in listed.json()] == [thread_id]

            patched = await client.patch(
                f"/api/v1/sessions/{thread_id}",
                json={"approval_mode": "always_allow"},
                headers={"Authorization": "Bearer test"},
            )
            assert patched.status_code == 200
            assert patched.json()["approval_mode"] == "always_allow"

            run = await client.post(
                f"/api/v1/sessions/{thread_id}/runs",
                json={"text": "你好", "external_run_id": "ext-run-1"},
                headers={"Authorization": "Bearer test"},
            )
            assert run.status_code == 202
            v1_run_id = run.json()["run_id"]
            assert submitted and submitted[-1]["policy"] == "reject"

            replay = await client.post(
                f"/api/v1/sessions/{thread_id}/runs",
                json={"text": "你好", "external_run_id": "ext-run-1"},
                headers={"Authorization": "Bearer test"},
            )
            assert replay.status_code == 202
            assert replay.json()["run_id"] == v1_run_id
            assert len(submitted) == 1  # 幂等重放未重复提交

            events = await client.get(f"/api/v1/sessions/{thread_id}/events", headers={"Authorization": "Bearer test"})
            assert events.status_code == 200
            types = [item["type"] for item in events.json()]
            assert types == ["turn/start", "user/message"]
            assert all(item["payload"]["run_id"] == v1_run_id for item in events.json())

            runs = await client.get(f"/api/v1/sessions/{thread_id}/runs", headers={"Authorization": "Bearer test"})
            assert runs.status_code == 200
            assert runs.json()[0]["external_run_id"] == "ext-run-1"

            cancel = await client.post(f"/api/v1/runs/{v1_run_id}/cancel", headers={"Authorization": "Bearer test"})
            assert cancel.status_code == 200
            assert cancel.json() == {"ok": False}  # 内部 run 不存在于隔离库

            deleted = await client.delete(f"/api/v1/sessions/{thread_id}", headers={"Authorization": "Bearer test"})
            assert deleted.status_code == 200
            after_delete = await client.get(
                f"/api/v1/sessions/{thread_id}/events", headers={"Authorization": "Bearer test"}
            )
            assert after_delete.status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await scoped_engine.dispose()
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_plane_session_attaches_to_workspace_project(monkeypatch):
    """带 plane_meta 的会话归属确定性 Plane 工作区项目（uuid5 确定性 id）。"""
    import yuxi.services.plane_context_service as context_service
    import yuxi.services.plane_session_compat_service as compat_service
    from yuxi.services.plane_session_compat_service import PlaneSessionCompatService

    schema = f"pytest_plane_proj_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = scoped_engine
    manager._initialized = True
    session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)

    async def provide_db():
        async with session_factory() as session:
            yield session

    test_user = SimpleNamespace(id=1, uid="plane:it-user", username="plane_it", role="user")

    async def provide_user(authorization: str | None = Header(None)):
        return test_user if authorization else None

    async def fake_validate(*, token, expected):
        return None

    await manager.create_business_tables()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (username, uid, password_hash, role, is_deleted, login_failed_count)"
                " VALUES ('plane_it', 'plane:it-user', 'x', 'user', 0, 0)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO agents (slug, backend_id, name, pics, config_json, share_config, is_default, is_subagent)"
                " VALUES ('default-chatbot', 'chatbot', 'IT', '[]'::jsonb, '{}'::jsonb,"
                " ('{ \"version\": 2, \"read_scope\": { \"access_level\": \"global\","
                " \"department_ids\": [], \"user_uids\": [] } }')::jsonb, TRUE, FALSE)"
            )
        )
        await session.commit()

    monkeypatch.setattr(context_service, "validate_research_context", fake_validate)
    import server.routers.plane_compat_router as router_mod

    monkeypatch.setattr(router_mod, "validate_research_context", fake_validate)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_current_user] = provide_user
    try:
        plane_meta = {"workspace": {"id": "ws-uuid-1", "slug": "ws", "name": "研发工作区"}, "target": {"type": "WORKSPACE"}}
        research_context = {
            "schema_version": "agent-context.v3",
            "context_id": "ctx-1",
            "context_hash": "h1",
            "issuer": "http://plane.test",
            "target": {"type": "WORKSPACE", "workspace_id": "ws-uuid-1", "workspace_slug": "ws"},
            "subject": {"plane_user_id": "u1"},
            "policy": {"policy_id": "p1", "policy_hash": "ph1", "allowed_tools": []},
        }
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/sessions",
                json={"title": "项目归属", "plane_meta": plane_meta, "research_context": research_context},
                headers={"Authorization": "Bearer test", "X-Research-Context-Token": "t"},
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["project_id"]
            import uuid as uuid_mod

            assert str(uuid_mod.UUID(project_id)) == project_id  # 必须是合法 UUID
            async with session_factory() as session:
                row = await session.execute(
                    text("SELECT id, name, selection_status FROM projects WHERE id = :pid"), {"pid": project_id}
                )
                project = row.one()
            assert project.name.startswith("Plane · ")
            assert project.selection_status == "selectable"
            # 再次创建同工作区会话 → 复用同一项目
            again = await client.post(
                "/api/v1/sessions",
                json={"title": "第二会话", "plane_meta": plane_meta, "research_context": research_context},
                headers={"Authorization": "Bearer test", "X-Research-Context-Token": "t"},
            )
            assert again.json()["project_id"] == project_id
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await scoped_engine.dispose()
        await admin_engine.dispose()
