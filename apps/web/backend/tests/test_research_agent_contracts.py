"""Plane delegated auth、capability manifest 与 v2 工具策略测试。"""
from app.core.auth import parse_token


def _delegated_body() -> dict:
    return {
        "schema_version": "plane-delegated-auth.v1",
        "account_link_id": "link-1",
        "workspace_id": "workspace-1",
        "plane_user_id": "user-1",
        "external_subject": "u_synlora",
    }


async def test_delegated_token_requires_plane_service_token(app, client, monkeypatch):
    """服务凭证正确时代签短效用户 token，错误时 fail closed。"""
    monkeypatch.setattr(app.state.settings, "plane_service_token", "service-token", raising=False)
    response = await client.post(
        "/api/v1/research/delegated-token",
        headers={"Authorization": "Bearer service-token"},
        json=_delegated_body(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "plane-delegated-auth.v1"
    assert payload["subject"] == "u_synlora"
    parsed = parse_token(payload["token"], app.state.settings)
    assert parsed is not None and parsed["sub"] == "u_synlora"

    rejected = await client.post(
        "/api/v1/research/delegated-token",
        headers={"Authorization": "Bearer wrong"},
        json=_delegated_body(),
    )
    assert rejected.status_code == 403


async def test_capability_manifest_is_user_scoped(app, client, user_headers):
    """manifest 只读投影注册工具和当前用户可见插件。"""
    response = await client.get("/api/v1/research/capabilities", headers=user_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "capability-manifest.v1"
    tool_names = {tool["name"] for tool in payload["tools"]}
    assert {"knowledge.search", "knowledge.list", "file.read"}.issubset(tool_names)
    assert all(tool["health"] in ("OK", "UNAVAILABLE") for tool in payload["tools"])


def test_v2_context_tool_policy_intersects_defensively():
    """Plane allowed_tools 是硬上界，不能被专家或插件放大。"""
    from app.services.agent_service import apply_research_tool_policy

    selected = ["file.read", "file.write", "knowledge.search", "web.search"]
    context = {
        "schema_version": "agent-context.v2",
        "allowed_tools": ["knowledge.search", "file.read"],
    }
    assert apply_research_tool_policy(selected, context) == ["file.read", "knowledge.search"]
    assert apply_research_tool_policy(selected, None) == selected


async def test_research_message_requires_context_token_each_turn(app, client, user_headers, monkeypatch):
    """每轮消息必须重新携带 Context token，旧会话不能自动续权。"""
    from tests.test_research_context import _metadata

    metadata = _metadata()
    async def fake_validate(**_kwargs):
        return None

    monkeypatch.setattr(app.state.research_context_adapter, "validate", fake_validate)
    created = await client.post(
        "/api/v1/sessions",
        headers={**user_headers, "X-Research-Context-Token": "opaque"},
        json={"research_context": metadata.model_dump(mode="json")},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["_id"]
    response = await client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers=user_headers,
        json={"text": "继续分析"},
    )
    assert response.status_code == 401
