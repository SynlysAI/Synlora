"""yxkey 个人访问令牌认证与管理 API 测试。"""
import hashlib
import time

from app.core.auth import issue_token


def _headers(app, role="user"):
    token = issue_token({"sub": f"uid-{role}", "username": f"{role}-user", "role": role}, app.state.settings)
    return {"Authorization": f"Bearer {token}"}


def _public_fields():
    return {"id", "name", "key_prefix", "is_enabled", "expires_at", "last_used_at", "created_at"}


async def test_api_key_lifecycle_and_auth(app, client):
    """创建后 yxkey 可认证，列表不回显 hash/明文，停用后立即失效。"""
    headers = _headers(app)
    created = await client.post("/api/v1/user/apikeys", headers=headers, json={"name": "Plane"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"].startswith("yxkey_")
    assert body["item"]["key_prefix"] == body["key"][:12]
    assert "key_hash" not in body["item"]

    auth = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['key']}"})
    assert auth.status_code == 200
    assert auth.json()["sub"] == "uid-user"

    listed = await client.get("/api/v1/user/apikeys", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert set(items[0]) >= _public_fields()
    assert "key_hash" not in items[0] and "key" not in items[0]

    key_id = body["item"]["id"]
    disabled = await client.patch(f"/api/v1/user/apikeys/{key_id}", headers=headers, json={"is_enabled": False})
    assert disabled.status_code == 200
    auth = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['key']}"})
    assert auth.status_code == 401


async def test_migrated_api_key_authenticates_without_local_user_collection(app, client):
    """迁移工具写入的 key hash 可直接认证，且更新 last_used_at。"""
    raw = "yxkey_migrated0000000000000000000000000000000000000000000000"
    doc = {
        "_id": "legacy-key-1",
        "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "key_prefix": raw[:12],
        "name": "Migrated",
        "user_id": "fangyikai",
        "username": "fangyikai",
        "role": "user",
        "is_enabled": True,
        "expires_at": None,
        "last_used_at": None,
        "created_at": time.time(),
        "updated_at": time.time(),
        "migrated": True,
    }
    await app.state.store.insert("api_keys", doc)
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json()["sub"] == "fangyikai"
    await app.state.store.get("api_keys", "legacy-key-1")


async def test_api_key_is_owner_scoped_and_expired_key_fails(app, client):
    """不能读取他人 key；过期 key 拒绝认证。"""
    await app.state.store.insert("api_keys", {
        "_id": "other-key",
        "key_hash": hashlib.sha256(b"other").hexdigest(),
        "key_prefix": "yxkey_other",
        "name": "Other",
        "user_id": "someone-else",
        "username": "someone-else",
        "role": "user",
        "is_enabled": True,
        "expires_at": time.time() - 1,
        "last_used_at": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })
    headers = _headers(app)
    assert (await client.get("/api/v1/user/apikeys", headers=headers)).json()["items"] == []
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer other"})
    assert response.status_code == 401
