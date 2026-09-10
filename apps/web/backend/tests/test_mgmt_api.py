"""模型/助手管理 API 测试：权限、key 不外泄、输入校验、连通性 mock。"""
from synlys_harness import OpenAICompatibleBackend, TextDelta

PROVIDER_BODY = {
    "name": "openai-mock",
    "base_url": "https://api.mock.local/v1",
    "api_key": "sk-1",
    "model_id": "gpt-test",
    "enabled": True,
}


async def _create_provider(client, headers, **override) -> dict:
    """建一个 provider，返回 201 响应 JSON。

    Args:
        client: httpx 异步客户端。
        headers: 请求头（管理员）。
        override: 覆盖字段。

    Returns:
        创建成功的响应体。
    """
    r = await client.post("/api/v1/models", headers=headers,
                          json={**PROVIDER_BODY, **override})
    assert r.status_code == 201, r.text
    return r.json()


async def _create_assistant(client, headers, **override) -> dict:
    """建一个助手，返回 201 响应 JSON。

    Args:
        client: httpx 异步客户端。
        headers: 请求头（管理员）。
        override: 覆盖字段。

    Returns:
        创建成功的响应体。
    """
    body = {"name": "测试助手", "system_prompt": "你是测试助手",
            "tool_whitelist": ["python.run"]}
    body.update(override)
    r = await client.post("/api/v1/assistants", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- models：CRUD 与权限 ----------


async def test_create_and_list_no_key_leak(client, admin_headers, user_headers):
    """admin 创建 → admin/user 列表均可见，且无任何 key 字段。"""
    created = await _create_provider(client, admin_headers)
    assert created["has_key"] is True
    assert "api_key" not in created and "api_key_enc" not in created

    for headers in (admin_headers, user_headers):
        r = await client.get("/api/v1/models", headers=headers)
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        item = items[0]
        assert item["_id"] == created["_id"]
        assert item["name"] == "openai-mock"
        assert item["has_key"] is True
        assert "api_key" not in item and "api_key_enc" not in item


async def test_user_post_provider_403(client, user_headers):
    """普通用户创建 provider 应 403。"""
    r = await client.post("/api/v1/models", headers=user_headers, json=PROVIDER_BODY)
    assert r.status_code == 403


async def test_duplicate_name_409(client, admin_headers):
    """同名 provider 重复创建应 409。"""
    await _create_provider(client, admin_headers)
    r = await client.post("/api/v1/models", headers=admin_headers, json=PROVIDER_BODY)
    assert r.status_code == 409


async def test_invalid_base_url_422(client, admin_headers):
    """base_url 非 http(s) 前缀应 422。"""
    r = await client.post("/api/v1/models", headers=admin_headers,
                          json={**PROVIDER_BODY, "base_url": "ftp://x"})
    assert r.status_code == 422


async def test_enabled_filter_and_admin_all(client, admin_headers, user_headers):
    """默认只列 enabled；管理员 all=true 看全部；普通用户 all=true 仍只见 enabled。"""
    await _create_provider(client, admin_headers, name="on")
    await _create_provider(client, admin_headers, name="off", enabled=False)

    user_view = await client.get("/api/v1/models", headers=user_headers)
    assert [i["name"] for i in user_view.json()] == ["on"]

    user_all = await client.get("/api/v1/models?all=true", headers=user_headers)
    assert [i["name"] for i in user_all.json()] == ["on"]

    admin_all = await client.get("/api/v1/models?all=true", headers=admin_headers)
    assert sorted(i["name"] for i in admin_all.json()) == ["off", "on"]


async def test_patch_key_reencrypted(client, admin_headers, app):
    """PATCH 带 api_key → 重加密入库，直查 repo 解密拿回新 key（真实加解密路径）。"""
    created = await _create_provider(client, admin_headers, api_key="sk-old")
    pid = created["_id"]
    assert (await app.state.provider_repo.get_decrypted(pid))["api_key"] == "sk-old"
    raw = await app.state.store.get("providers", pid)
    assert raw["api_key_enc"] != "sk-old"  # 入库即密文，不存明文
    assert raw["api_key_encrypted"] is True

    r = await client.patch(f"/api/v1/models/{pid}", headers=admin_headers,
                           json={"api_key": "sk-new", "model_id": "gpt-9"})
    assert r.status_code == 200
    assert r.json()["model_id"] == "gpt-9"
    assert "api_key" not in r.json() and "api_key_enc" not in r.json()

    raw = await app.state.store.get("providers", pid)
    assert raw["api_key_enc"] != "sk-new"  # PATCH 后重加密覆写
    assert raw["api_key_encrypted"] is True
    dec = await app.state.provider_repo.get_decrypted(pid)
    assert dec["api_key"] == "sk-new"
    assert dec["model_id"] == "gpt-9"


async def test_patch_rename_conflict_409(client, admin_headers):
    """PATCH 改名撞到现有名应 409。"""
    await _create_provider(client, admin_headers, name="a")
    b = await _create_provider(client, admin_headers, name="b")
    r = await client.patch(f"/api/v1/models/{b['_id']}", headers=admin_headers,
                           json={"name": "a"})
    assert r.status_code == 409


async def test_missing_provider_404(client, admin_headers):
    """PATCH/DELETE/test 不存在的 provider 应 404。"""
    r = await client.patch("/api/v1/models/nope", headers=admin_headers,
                           json={"name": "x"})
    assert r.status_code == 404
    r = await client.delete("/api/v1/models/nope", headers=admin_headers)
    assert r.status_code == 404
    r = await client.post("/api/v1/models/nope/test", headers=admin_headers)
    assert r.status_code == 404


async def test_delete_provider_referenced_409(client, admin_headers):
    """被助手引用的 provider 不可删（409），解除引用后可删（200）。"""
    provider = await _create_provider(client, admin_headers)
    pid = provider["_id"]
    asst = await _create_assistant(client, admin_headers, model_provider_id=pid)

    blocked = await client.delete(f"/api/v1/models/{pid}", headers=admin_headers)
    assert blocked.status_code == 409
    assert "引用" in blocked.json()["detail"]

    ok_asst = await client.delete(f"/api/v1/assistants/{asst['_id']}",
                                  headers=admin_headers)
    assert ok_asst.status_code == 200
    freed = await client.delete(f"/api/v1/models/{pid}", headers=admin_headers)
    assert freed.status_code == 200


# ---------- models：连通性测试端点（mock 上游） ----------


async def test_connectivity_ok(client, admin_headers, monkeypatch):
    """mock 流返回 TextDelta("pong") → ok=True 且 latency_ms>=0。"""
    created = await _create_provider(client, admin_headers)

    async def fake_stream(self, messages, tools=None):
        """替代 OpenAICompatibleBackend.stream 的假流。"""
        yield TextDelta(text="pong")

    monkeypatch.setattr(OpenAICompatibleBackend, "stream", fake_stream)
    r = await client.post(f"/api/v1/models/{created['_id']}/test",
                          headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0
    assert body["error"] is None


async def test_connectivity_failure_returns_error(client, admin_headers, monkeypatch):
    """mock 流抛 RuntimeError → 200 但 ok=False 且 error 含异常信息。"""
    created = await _create_provider(client, admin_headers)

    async def boom(self, messages, tools=None):
        """首个事件前抛错的假流。"""
        raise RuntimeError("上游连接失败")
        yield  # noqa: 使其成为 async generator

    monkeypatch.setattr(OpenAICompatibleBackend, "stream", boom)
    r = await client.post(f"/api/v1/models/{created['_id']}/test",
                          headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "上游连接失败" in body["error"]


async def test_connectivity_user_403(client, user_headers):
    """普通用户调用连通性测试应 403。"""
    r = await client.post("/api/v1/models/x/test", headers=user_headers)
    assert r.status_code == 403


async def test_connectivity_timeout(client, admin_headers, monkeypatch):
    """流长时间无事件 → 超时保护返回 ok=False 且 error 含"连接超时"（测试中缩短超时）。"""
    import asyncio

    from app.api import models_api

    created = await _create_provider(client, admin_headers)

    async def slow_stream(self, messages, tools=None):
        """长时间不产出事件的假流。"""
        await asyncio.sleep(5)
        yield TextDelta(text="late")

    monkeypatch.setattr(OpenAICompatibleBackend, "stream", slow_stream)
    monkeypatch.setattr(models_api, "TEST_TIMEOUT_SECONDS", 0.1)
    r = await client.post(f"/api/v1/models/{created['_id']}/test",
                          headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "连接超时" in body["error"]


# ---------- assistants：CRUD、校验与权限 ----------


async def test_list_includes_seeds(client, user_headers):
    """GET 助手列表含两个 lifespan 种子（builtin 标记正确）。"""
    r = await client.get("/api/v1/assistants", headers=user_headers)
    assert r.status_code == 200
    items = {a["_id"]: a for a in r.json()}
    assert "asst-research" in items and "asst-data" in items
    assert items["asst-research"]["builtin"] is True
    assert items["asst-data"]["builtin"] is True
    assert items["asst-research"]["model_name"] is None


async def test_user_post_assistant_403(client, user_headers):
    """普通用户创建助手应 403。"""
    r = await client.post("/api/v1/assistants", headers=user_headers,
                          json={"name": "x", "system_prompt": "p"})
    assert r.status_code == 403


async def test_admin_create_assistant_full(client, admin_headers):
    """admin 全字段创建助手成功，builtin=False。"""
    provider = await _create_provider(client, admin_headers)
    created = await _create_assistant(
        client, admin_headers,
        name="自定义助手", description="描述",
        tool_whitelist=["python.run", "file.read"],
        model_provider_id=provider["_id"],
    )
    assert created["name"] == "自定义助手"
    assert created["tool_whitelist"] == ["python.run", "file.read"]
    assert created["model_provider_id"] == provider["_id"]
    assert created["builtin"] is False


async def test_empty_system_prompt_422(client, admin_headers):
    """system_prompt 为空应 422。"""
    r = await client.post("/api/v1/assistants", headers=admin_headers,
                          json={"name": "x", "system_prompt": ""})
    assert r.status_code == 422


async def test_invalid_tool_whitelist_422(client, admin_headers):
    """tool_whitelist 含未注册工具名应 422 且 detail 指明非法项。"""
    r = await client.post("/api/v1/assistants", headers=admin_headers,
                          json={"name": "x", "system_prompt": "p",
                                "tool_whitelist": ["hack.tool"]})
    assert r.status_code == 422
    assert "hack.tool" in r.json()["detail"]


async def test_provider_not_found_422(client, admin_headers):
    """model_provider_id 不存在应 422。"""
    r = await client.post("/api/v1/assistants", headers=admin_headers,
                          json={"name": "x", "system_prompt": "p",
                                "model_provider_id": "nope"})
    assert r.status_code == 422


async def test_provider_disabled_422(client, admin_headers):
    """引用已停用 provider 创建助手应 422。"""
    provider = await _create_provider(client, admin_headers, enabled=False)
    r = await client.post("/api/v1/assistants", headers=admin_headers,
                          json={"name": "x", "system_prompt": "p",
                                "model_provider_id": provider["_id"]})
    assert r.status_code == 422


async def test_delete_builtin_409(client, admin_headers):
    """删除内置种子助手应 409。"""
    r = await client.delete("/api/v1/assistants/asst-research",
                            headers=admin_headers)
    assert r.status_code == 409


async def test_patch_assistant_validations(client, admin_headers):
    """PATCH 校验提供的字段：空白 system_prompt/非法工具 422，合法更新 200。"""
    created = await _create_assistant(client, admin_headers)

    blank = await client.patch(f"/api/v1/assistants/{created['_id']}",
                               headers=admin_headers, json={"system_prompt": "  "})
    assert blank.status_code == 422

    bad_tool = await client.patch(f"/api/v1/assistants/{created['_id']}",
                                  headers=admin_headers,
                                  json={"tool_whitelist": ["nope.tool"]})
    assert bad_tool.status_code == 422

    ok = await client.patch(f"/api/v1/assistants/{created['_id']}",
                            headers=admin_headers,
                            json={"name": "改名", "tool_whitelist": ["file.read"]})
    assert ok.status_code == 200
    body = ok.json()
    assert body["name"] == "改名"
    assert body["tool_whitelist"] == ["file.read"]
    assert body["system_prompt"] == "你是测试助手"  # 未提供字段不丢


async def test_patch_missing_assistant_404(client, admin_headers):
    """PATCH 不存在的助手应 404。"""
    r = await client.patch("/api/v1/assistants/nope", headers=admin_headers,
                           json={"name": "x"})
    assert r.status_code == 404


# ---------- assistants：provider 名称联查 ----------


async def test_list_joins_provider_name(client, admin_headers, user_headers):
    """联查 provider 名称；provider 停用后标记 (已停用)，数据漂移删除后标记 (已删除)。"""
    provider = await _create_provider(client, admin_headers, name="主模型")
    asst = await _create_assistant(client, admin_headers,
                                   model_provider_id=provider["_id"])

    lst = await client.get("/api/v1/assistants", headers=user_headers)
    items = {a["_id"]: a for a in lst.json()}
    assert items[asst["_id"]]["model_name"] == "主模型"

    await client.patch(f"/api/v1/models/{provider['_id']}", headers=admin_headers,
                       json={"enabled": False})
    lst = await client.get("/api/v1/assistants", headers=user_headers)
    items = {a["_id"]: a for a in lst.json()}
    assert items[asst["_id"]]["model_name"] == "(已停用)"

    # 解除引用后 provider 可正常删除
    del_asst = await client.delete(f"/api/v1/assistants/{asst['_id']}",
                                   headers=admin_headers)
    assert del_asst.status_code == 200
    del_provider = await client.delete(f"/api/v1/models/{provider['_id']}",
                                       headers=admin_headers)
    assert del_provider.status_code == 200


async def test_list_marks_deleted_provider(app, client, admin_headers, user_headers):
    """助手引用的 provider 被直删（绕过 API 409 防护）时标 (已删除)。"""
    provider = await _create_provider(client, admin_headers)
    asst = await _create_assistant(client, admin_headers,
                                   model_provider_id=provider["_id"])
    await app.state.provider_repo.delete(provider["_id"])

    r = await client.get("/api/v1/assistants", headers=user_headers)
    items = {a["_id"]: a for a in r.json()}
    assert items[asst["_id"]]["model_name"] == "(已删除)"
