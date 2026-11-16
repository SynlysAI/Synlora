"""自服务账户绑定：状态查询、双向绑定（按登录方定核心）、冲突与解绑。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import hash_password, issue_token
from app.core.settings import Settings

from app.main import create_app
from app.services.plane_auth import PlaneProfile


def _settings(**kw) -> Settings:
    """构造测试配置（sqlite 后端）。"""
    base = {
        "auth_secret": "unit-test-secret",
        "auth_enabled": True,
        "storage_backend": "sqlite",
        "plane_base_url": "http://plane.test",
    }
    base.update(kw)
    return Settings(**base)


def _plane_headers(app, plane_user_id: str = "uuid-1", email: str = "alice@plane.test") -> dict:
    """构造 OpenEVA 登录 token 头。"""
    token = issue_token(
        {
            "sub": f"plane:{plane_user_id}",
            "username": "Alice",
            "role": "user",
            "auth": "plane",
            "plane_user_id": plane_user_id,
            "email": email,
        },
        app.state.settings,
    )
    return {"Authorization": f"Bearer {token}"}


def _ai4ms_headers(app, sub: str = "xiaoxu") -> dict:
    """构造本方账户登录 token 头。"""
    token = issue_token(
        {"sub": sub, "username": sub, "role": "user"}, app.state.settings
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_account(store, username: str = "xiaoxu", password: str = "pw") -> None:
    """插入本方账户。"""
    await store.insert("local_users", {
        "_id": username,
        "username": username,
        "password_hash": hash_password(password),
        "status": "active",
        "display_name": f"{username}-显示名",
    })


@pytest.fixture
async def link_app(tmp_path):
    """跑完整 lifespan 的 app（sessions 别名等端点需要 repos）。"""
    from cryptography.fernet import Fernet

    app = create_app()
    app.state.settings = _settings(
        sqlite_path=str(tmp_path / "link.db"),
        data_dir=str(tmp_path / "data"),
        fernet_key=Fernet.generate_key().decode(),
    )
    async with app.router.lifespan_context(app):
        yield app, app.state.store


@pytest.fixture
async def client(link_app):
    app, _ = link_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def _patch_plane_auth(monkeypatch, plane_user_id: str = "uuid-9", email: str = "b@plane.test"):
    """把 me_api 的 plane 验证替换为固定档案。"""
    async def fake(email, password, settings, transport=None):
        return PlaneProfile(plane_user_id=plane_user_id, email=email, display_name="Bob")
    monkeypatch.setattr("app.api.me_api.plane_authenticate", fake)


# ---------- 状态查询 ----------


async def test_status_ai4ms_unbound(link_app, client):
    """本方登录未绑定：identity_kind=ai4ms、bound=False。"""
    app, _ = link_app
    r = await client.get("/api/v1/me/account-link", headers=_ai4ms_headers(app))
    assert r.status_code == 200
    assert r.json() == {
        "identity_kind": "ai4ms", "bound": False, "core": "",
        "plane_user_id": None, "plane_email": "",
        "linked_username": "", "linked_display_name": "", "linked_plane_user_id": "",
    }


async def test_status_plane_bound_shows_linked_account(link_app, client):
    """OpenEVA 登录 + 已有绑定：显示绑定账户与核心方。"""
    app, store = link_app
    await _seed_account(store)
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1",
        "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "",
        "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu",
        "delegated_subject": "plane:uuid-1",
        "core": "plane",
        "created_at": 1,
    })
    r = await client.get("/api/v1/me/account-link", headers=_plane_headers(app))
    assert r.status_code == 200
    body = r.json()
    assert body["identity_kind"] == "plane"
    assert body["bound"] is True
    assert body["core"] == "plane"
    assert body["linked_username"] == "xiaoxu"
    assert body["linked_display_name"] == "xiaoxu-显示名"


async def test_status_falls_back_to_stored_username(link_app, client):
    """绑定账户已查不到（如已删除）：回退记录里落库的用户名，不显示空。"""
    app, store = link_app
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1", "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "", "plane_user_id": "uuid-1",
        "synlora_user_id": "ghost", "synlora_username": "ghost",
        "delegated_subject": "plane:uuid-1", "core": "plane", "created_at": 1,
    })
    r = await client.get("/api/v1/me/account-link", headers=_plane_headers(app))
    assert r.status_code == 200
    assert r.json()["linked_username"] == "ghost"


async def test_status_backfills_plane_email_from_token(link_app, client):
    """存量记录缺 plane_email：OpenEVA 身份访问时自愈补写并随响应返回。"""
    app, store = link_app
    await _seed_account(store)
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1", "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "", "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu", "synlora_username": "xiaoxu",
        "delegated_subject": "plane:uuid-1", "core": "plane",
        "plane_email": "", "created_at": 1,
    })
    r = await client.get(
        "/api/v1/me/account-link",
        headers=_plane_headers(app, email="alice@plane.test"),
    )
    assert r.status_code == 200
    assert r.json()["plane_email"] == "alice@plane.test"
    link = await store.get("plane_account_links", "plane-self-uuid-1")
    assert link["plane_email"] == "alice@plane.test"


# ---------- 绑定：OpenEVA 登录 → 绑本方账户（core=plane） ----------


async def test_bind_ai4ms_from_plane_identity(link_app, client):
    """OpenEVA 登录 + 对方账户密码正确 → core=plane 绑定落库。"""
    app, store = link_app
    await _seed_account(store)
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_plane_headers(app),
        json={"kind": "ai4ms", "username": "xiaoxu", "password": "pw"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"bound": True, "core": "plane", "account_link_id": "plane-self-uuid-1"}
    link = await store.get("plane_account_links", "plane-self-uuid-1")
    assert link["synlora_user_id"] == "xiaoxu"
    assert link["core"] == "plane"
    assert link["delegated_subject"] == "plane:uuid-1"
    assert link["plane_user_id"] == "uuid-1"
    assert link["source"] == "self"
    assert link["plane_email"] == "alice@plane.test"  # 从当前 OpenEVA token 补写


async def test_bind_ai4ms_wrong_password_401(link_app, client):
    """对方账户密码错误 → 401 且不落库。"""
    app, store = link_app
    await _seed_account(store)
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_plane_headers(app),
        json={"kind": "ai4ms", "username": "xiaoxu", "password": "wrong"},
    )
    assert r.status_code == 401
    assert await store.list("plane_account_links") == []


async def test_bind_ai4ms_kind_mismatch_422(link_app, client):
    """本方登录想再绑本方账户 → 422。"""
    app, _ = link_app
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_ai4ms_headers(app),
        json={"kind": "ai4ms", "username": "xiaoxu", "password": "pw"},
    )
    assert r.status_code == 422


# ---------- 绑定：本方登录 → 绑 OpenEVA（core=ai4ms） ----------


async def test_bind_plane_from_ai4ms_identity(link_app, client, monkeypatch):
    """本方登录 + OpenEVA 邮箱密码正确 → core=ai4ms 绑定落库。"""
    app, store = link_app
    _patch_plane_auth(monkeypatch, plane_user_id="uuid-9")
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_ai4ms_headers(app, sub="xiaoxu"),
        json={"kind": "plane", "email": "Bob@Plane.test", "password": "pw"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["core"] == "ai4ms"
    assert body["account_link_id"] == "plane-self-uuid-9"
    link = await store.get("plane_account_links", "plane-self-uuid-9")
    assert link["plane_user_id"] == "uuid-9"
    assert link["synlora_user_id"] == "xiaoxu"
    assert link["core"] == "ai4ms"
    assert link["plane_email"] == "bob@plane.test"  # 归一为验证通过的实际值
    assert link["synlora_username"] == "xiaoxu"


async def test_bind_plane_conflicts_409(link_app, client, monkeypatch):
    """任一侧已绑定不同账户/账号 → 409。"""
    app, store = link_app
    _patch_plane_auth(monkeypatch, plane_user_id="uuid-9")
    # OpenEVA uuid-9 已绑定别的账户
    await store.insert("plane_account_links", {
        "_id": "other-link", "account_link_id": "other-link",
        "plane_workspace_id": "ws-1", "plane_user_id": "uuid-9",
        "synlora_user_id": "someone-else", "delegated_subject": "d-1",
        "created_at": 1,
    })
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_ai4ms_headers(app, sub="xiaoxu"),
        json={"kind": "plane", "email": "b@plane.test", "password": "pw"},
    )
    assert r.status_code == 409

    # 反向：账户已绑定别的 OpenEVA 账号
    await store.delete("plane_account_links", "other-link")
    await store.insert("plane_account_links", {
        "_id": "other-link-2", "account_link_id": "other-link-2",
        "plane_workspace_id": "ws-1", "plane_user_id": "uuid-8",
        "synlora_user_id": "xiaoxu", "delegated_subject": "d-2",
        "created_at": 1,
    })
    r2 = await client.post(
        "/api/v1/me/account-link",
        headers=_ai4ms_headers(app, sub="xiaoxu"),
        json={"kind": "plane", "email": "b@plane.test", "password": "pw"},
    )
    assert r2.status_code == 409


async def test_rebind_same_pair_idempotent_update(link_app, client):
    """重复绑定同一对：幂等更新（含 core 翻转），不报冲突。"""
    app, store = link_app
    await _seed_account(store)
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1", "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "ws-1", "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu", "delegated_subject": "old-subject",
        "core": "ai4ms", "created_at": 1,
    })
    r = await client.post(
        "/api/v1/me/account-link",
        headers=_plane_headers(app),
        json={"kind": "ai4ms", "username": "xiaoxu", "password": "pw"},
    )
    assert r.status_code == 200
    assert r.json()["core"] == "plane"  # 从 OpenEVA 侧重绑 → core 翻转为 plane
    link = await store.get("plane_account_links", "plane-self-uuid-1")
    assert link["core"] == "plane"
    assert link["delegated_subject"] == "plane:uuid-1"


# ---------- 解绑 ----------


async def test_unbind_removes_links(link_app, client):
    """解绑删除当前身份关联的绑定（双向查找）。"""
    app, store = link_app
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1", "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "", "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu", "delegated_subject": "plane:uuid-1",
        "core": "plane", "created_at": 1,
    })
    r = await client.delete("/api/v1/me/account-link", headers=_plane_headers(app))
    assert r.status_code == 200
    assert r.json() == {"bound": False, "removed": 1}
    assert await store.list("plane_account_links") == []

    # 本方账户侧解绑同样有效
    await store.insert("plane_account_links", {
        "_id": "l2", "account_link_id": "l2",
        "plane_workspace_id": "", "plane_user_id": "uuid-2",
        "synlora_user_id": "xiaoxu", "delegated_subject": "plane:uuid-2",
        "core": "ai4ms", "created_at": 1,
    })
    r2 = await client.delete("/api/v1/me/account-link", headers=_ai4ms_headers(app))
    assert r2.status_code == 200
    assert r2.json()["removed"] == 1
    assert await store.list("plane_account_links") == []


# ---------- 会话双向别名 ----------


async def test_sessions_merge_bidirectionally_after_bind(link_app, client):
    """绑定后：任一侧登录都能在会话列表看到两侧会话（含历史委托身份）。"""
    app, store = link_app
    await store.insert("plane_account_links", {
        "_id": "plane-self-uuid-1", "account_link_id": "plane-self-uuid-1",
        "plane_workspace_id": "", "plane_user_id": "uuid-1",
        "synlora_user_id": "xiaoxu",
        "delegated_subject": "plane-delegated-ws-1-user-uuid-1",
        "core": "plane", "created_at": 1,
    })
    for sid, uid in [
        ("sess-plane", "plane:uuid-1"),          # OpenEVA 身份会话
        ("sess-ai4ms", "xiaoxu"),                 # 本方账户会话
        ("sess-legacy", "plane-delegated-ws-1-user-uuid-1"),  # 历史委托会话
    ]:
        await store.insert("sessions", {
            "_id": sid, "user_id": uid, "assistant_id": None, "title": sid,
            "archived": False, "message_count": 0, "model_provider_id": None,
            "project_id": None, "origin": None, "created_at": 1, "updated_at": 1,
        })
    # OpenEVA 侧登录：三份会话都可见
    r = await client.get("/api/v1/sessions", headers=_plane_headers(app))
    assert r.status_code == 200
    assert {item["_id"] for item in r.json()} == {"sess-plane", "sess-ai4ms", "sess-legacy"}
    # 本方账户登录：同样三份可见
    r2 = await client.get("/api/v1/sessions", headers=_ai4ms_headers(app))
    assert r2.status_code == 200
    assert {item["_id"] for item in r2.json()} == {"sess-plane", "sess-ai4ms", "sess-legacy"}
    # 未绑定的第三方：只见自己的
    r3 = await client.get("/api/v1/sessions", headers=_ai4ms_headers(app, sub="stranger"))
    assert r3.status_code == 200
    assert r3.json() == []


# ---------- mongodb 下 find_user_by_id 的 _id 形态兼容 ----------


class _FakeCollection:
    """记录查询序列的 users 集合替身。"""

    def __init__(self, docs: dict) -> None:
        self.docs = docs
        self.queries: list = []

    async def find_one(self, q: dict) -> dict | None:
        self.queries.append(q["_id"])
        return self.docs.get(q["_id"])


class _FakeClient:
    """motor 客户端替身（ai4ms.users → 同一集合）。"""

    def __init__(self, coll: _FakeCollection) -> None:
        self.coll = coll

    def __getitem__(self, name: str):
        if name == "ai4ms":
            return self
        return self.coll


def _mongo_request(coll: _FakeCollection):
    """构造 storage_backend=mongodb 的伪 request（客户端走缓存注入）。"""
    import types

    req = types.SimpleNamespace()
    req.app = types.SimpleNamespace()
    req.app.state = types.SimpleNamespace(
        settings=_settings(storage_backend="mongodb"),
        _mongo_users_client=_FakeClient(coll),
    )
    return req


async def test_mongo_find_user_by_id_objectid():
    """绑定记录存字符串而 ai4ms.users._id 是 ObjectId：候选重试命中。"""
    from bson import ObjectId

    from app.api.auth_api import find_user_by_id

    oid = ObjectId("6a39ff914dec95d85f993e00")
    coll = _FakeCollection({oid: {"_id": oid, "username": "xiaoxu"}})
    account = await find_user_by_id(_mongo_request(coll), str(oid))
    assert account is not None
    assert account["username"] == "xiaoxu"
    # 先按原字符串查、miss 后用 ObjectId 重试
    assert coll.queries == [str(oid), oid]


async def test_mongo_find_user_by_id_plain_string():
    """_id 本就是字符串（如 sqlite 风格迁移）：首轮即命中。"""
    from app.api.auth_api import find_user_by_id

    coll = _FakeCollection({"xiaoxu": {"_id": "xiaoxu", "username": "xiaoxu"}})
    account = await find_user_by_id(_mongo_request(coll), "xiaoxu")
    assert account is not None
    assert coll.queries == ["xiaoxu"]


async def test_mongo_find_user_by_id_int():
    """_id 是 int（门户旧库形态）：数字串回退 int 候选命中。"""
    from app.api.auth_api import find_user_by_id

    coll = _FakeCollection({42: {"_id": 42, "username": "xiaoxu"}})
    account = await find_user_by_id(_mongo_request(coll), "42")
    assert account is not None
    assert coll.queries == ["42", 42]


async def test_mongo_find_user_by_id_missing():
    """查无此人：候选全部 miss 返回 None。"""
    from app.api.auth_api import find_user_by_id

    coll = _FakeCollection({})
    account = await find_user_by_id(_mongo_request(coll), "6a39ff914dec95d85f993e00")
    assert account is None
    assert len(coll.queries) == 2  # 字符串 + ObjectId，不含 int（非纯数字）
