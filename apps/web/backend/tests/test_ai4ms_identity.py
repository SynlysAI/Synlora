"""AI⁴MS 身份代签测试：token 签发/验签闭环 + 按用户解析身份（无 DB 依赖）。

覆盖两条解析路径的边界：payload 里已带 `ai4ms_user_id`（登录快路径，不查库）
与 sqlite/匿名（无 AI⁴MS 账号 → None，调用方回落插件配置里的服务 token）。
"""
from __future__ import annotations


def test_mint_ai4ms_token_roundtrip():
    """代签的 token 能被本方 parse_token 验签通过，且 sub 是 AI⁴MS user_id。"""
    from app.core.auth import mint_ai4ms_token, parse_token
    from app.core.settings import Settings

    settings = Settings(auth_secret="unit-test-secret")
    token = mint_ai4ms_token("u_abc", "xiaoxu", "user", settings)
    payload = parse_token(token, settings)
    assert payload is not None
    assert payload["sub"] == "u_abc" and payload["username"] == "xiaoxu"
    assert payload["exp"] - payload["iat"] == 3600


def test_mint_ai4ms_token_ttl_is_configurable():
    """ttl_hours 决定有效期（子平台侧的短效凭证，不是本地登录的 7 天）。"""
    from app.core.auth import mint_ai4ms_token, parse_token
    from app.core.settings import Settings

    settings = Settings(auth_secret="unit-test-secret")
    payload = parse_token(
        mint_ai4ms_token("u_abc", "xiaoxu", "admin", settings, ttl_hours=4), settings)
    assert payload is not None and payload["role"] == "admin"
    assert payload["exp"] - payload["iat"] == 4 * 3600


async def test_identity_uses_claim_fast_path():
    """payload 已带 ai4ms_user_id 时直接代签（不查库）。"""
    from app.core.auth import parse_token
    from app.core.settings import Settings
    from app.services.ai4ms_identity import Ai4msIdentityService

    settings = Settings(auth_secret="s", storage_backend="sqlite")
    service = Ai4msIdentityService(settings)
    token = await service.token_for({"sub": "6a39...", "username": "xiaoxu",
                                     "role": "user", "ai4ms_user_id": "u_abc"})
    assert token and token.split(".")[0]  # 有 token
    assert parse_token(token, settings)["sub"] == "u_abc"


async def test_identity_returns_none_without_ai4ms_account():
    """sqlite 模式、payload 无 ai4ms_user_id → None（调用方回落服务 token）。"""
    from app.core.settings import Settings
    from app.services.ai4ms_identity import Ai4msIdentityService

    service = Ai4msIdentityService(Settings(auth_secret="s", storage_backend="sqlite"))
    assert await service.token_for({"sub": "dev", "username": "dev",
                                    "role": "admin"}) is None


async def test_identity_fails_silent_when_mongo_unreachable():
    """mongodb 模式查询异常 → 记 warning 后 None（代签失败绝不打断对话）。"""
    from app.core.settings import Settings
    from app.services.ai4ms_identity import Ai4msIdentityService

    settings = Settings(
        auth_secret="s", storage_backend="mongodb",
        # 端口 1 必然拒绝；缩短 serverSelectionTimeout 避免用例等默认 30s
        mongodb_uri="mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300")
    service = Ai4msIdentityService(settings)
    # 库不可达：解析不到身份，静默降级为 None
    assert await service.token_for({"sub": "6a39...", "username": "nobody",
                                    "role": "user"}) is None
