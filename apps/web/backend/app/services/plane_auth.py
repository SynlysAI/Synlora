"""Plane 账号登录代理：复用 Plane 浏览器登录流验证邮箱密码。

Plane 没有面向服务端的 JSON 密码校验接口，``/auth/sign-in/`` 是浏览器 302 跳转
流程（表单 POST、失败以 ``?error_code=`` 回传、成功种 sessionid cookie）。本模块
在服务端按同一路径三步走（cookie jar 贯穿），把它当作密码验证预言机：

1. ``GET /auth/get-csrf-token/`` 取 csrf_token（同时种 csrftoken cookie）；
2. ``POST /auth/sign-in/``（form + X-CSRFToken）：302 且无 ``error_code`` 且拿到
   会话 cookie（Plane 定制名 ``session-id``）= 密码正确，有 ``error_code`` = 失败
   （映射 401/429/503）；
3. ``GET /api/users/me/``（session cookie）取 plane_user_id / display_name。

不在本方任何库中落 Plane 用户数据（纯联邦验证）；Plane 改密码即时生效。限流由
Plane 侧 ``AUTHENTICATION_RATE_LIMIT``（缺省 10 次/分钟/IP）兜底——注意所有
Synlora 用户的 Plane 登录共享同一出口 IP 的这份配额。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from app.core.settings import Settings

# 登录链路超时：sign-in 里 Plane 要做 PBKDF2 高迭代哈希 + 两次跳转，比普通
# API 调用慢，取配置超时与 10s 的较大值。
_LOGIN_TIMEOUT_SECONDS = 10.0

# Plane AUTHENTICATION_ERROR_CODES 中需要精确映射的错误码（其余一律 401）
_CODE_RATE_LIMIT = "5900"  # RATE_LIMIT_EXCEEDED
_CODE_INSTANCE_NOT_CONFIGURED = "5000"
_CODE_PASSWORD_LOGIN_DISABLED = "5018"  # PASSWORD_LOGIN_DISABLED
_CODE_ACCOUNT_DEACTIVATED = "5019"  # USER_ACCOUNT_DEACTIVATED


class PlaneAuthError(Exception):
    """Plane 登录验证失败；status_code 直接映射 HTTP 状态。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class PlaneProfile:
    """验证通过后的 Plane 用户最小档案（不含邮箱以外的联系方式）。"""

    plane_user_id: str
    email: str
    display_name: str


def resolve_auth_base_url(settings: Settings) -> str:
    """Plane 登录验证地址：优先专用配置，回落通用 base_url。

    Args:
        settings: 应用配置。

    Returns:
        去尾斜杠的基地址；未配置返回空串（调用方报 503）。
    """
    return (settings.plane_auth_base_url or settings.plane_base_url or "").rstrip("/")


def _error_from_redirect(location: str) -> tuple[str, str]:
    """从 sign-in 302 的 Location 提取 (error_code, error_message)。"""
    query = parse_qs(urlsplit(location).query)
    code = str((query.get("error_code") or [""])[0])
    message = str((query.get("error_message") or [""])[0])
    return code, message


def _map_error(code: str) -> PlaneAuthError:
    """把 Plane 错误码映射为本方 HTTP 语义。

    Args:
        code: Plane 的 error_code 字符串（如 "5065"）。

    Returns:
        携带目标状态码与中文文案的 PlaneAuthError。
    """
    if code == _CODE_RATE_LIMIT:
        return PlaneAuthError(429, "尝试过于频繁，请稍后再试")
    if code == _CODE_INSTANCE_NOT_CONFIGURED:
        return PlaneAuthError(503, "OpenEVA 实例未完成初始化")
    if code == _CODE_PASSWORD_LOGIN_DISABLED:
        return PlaneAuthError(403, "OpenEVA 已禁用密码登录")
    if code == _CODE_ACCOUNT_DEACTIVATED:
        return PlaneAuthError(401, "账号已禁用")
    # USER_DOES_NOT_EXIST / AUTHENTICATION_FAILED_SIGN_IN 等一律不区分，
    # 与本方账号密码登录的口径一致（防枚举）。
    return PlaneAuthError(401, "邮箱或密码错误")


async def authenticate(
    email: str,
    password: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PlaneProfile:
    """用 Plane 浏览器登录流验证邮箱密码，返回 Plane 用户档案。

    Args:
        email: 登录邮箱（小写）。
        password: 明文密码（仅转发给 Plane，不落任何存储/日志）。
        settings: 应用配置（取验证地址与超时）。
        transport: 可选测试注入的 httpx transport。

    Returns:
        PlaneProfile（plane_user_id / email / display_name）。

    Raises:
        PlaneAuthError: 未配置（503）、凭证错误（401/403）、限流（429）、
            Plane 不可达或响应异常（502）。
    """
    base = resolve_auth_base_url(settings)
    if not base:
        raise PlaneAuthError(503, "OpenEVA 账号登录未配置")
    timeout = max(_LOGIN_TIMEOUT_SECONDS, settings.plane_request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        # 1. CSRF token（同时种下 csrftoken cookie）
        try:
            csrf_resp = await client.get(f"{base}/auth/get-csrf-token/")
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务不可用") from exc
        if csrf_resp.status_code >= 400:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")
        try:
            csrf_token = str(csrf_resp.json().get("csrf_token") or "")
        except ValueError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常") from exc
        if not csrf_token:
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")

        # 2. sign-in（不跟随跳转：以 Location 的 error_code 判定成败）
        try:
            resp = await client.post(
                f"{base}/auth/sign-in/",
                data={"email": email, "password": password},
                headers={"X-CSRFToken": csrf_token, "Referer": f"{base}/"},
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 登录服务不可用") from exc
        if resp.status_code not in (301, 302, 303, 307, 308):
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")
        code, _ = _error_from_redirect(resp.headers.get("location", ""))
        if code:
            raise _map_error(code)
        # Plane 定制会话 cookie 名（SESSION_COOKIE_NAME 缺省 "session-id"），
        # 兼容原生 Django 的 "sessionid"；两者都没有视为未真正建立会话
        if not (
            client.cookies.get("session-id")
            or client.cookies.get("sessionid")
        ):
            raise PlaneAuthError(502, "OpenEVA 登录服务异常")

        # 3. 换取用户档案（session cookie 自动携带）
        try:
            me_resp = await client.get(f"{base}/api/users/me/")
        except httpx.HTTPError as exc:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败") from exc
        if me_resp.status_code >= 400:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败")
        try:
            payload = me_resp.json()
        except ValueError as exc:
            raise PlaneAuthError(502, "OpenEVA 用户信息获取失败") from exc
        plane_user_id = str(payload.get("id") or "")
        if not plane_user_id:
            raise PlaneAuthError(502, "OpenEVA 用户信息不完整")
        profile_email = str(payload.get("email") or email).strip().lower() or email
        display_name = str(
            payload.get("display_name") or payload.get("email") or email
        ).strip()
        return PlaneProfile(
            plane_user_id=plane_user_id, email=profile_email, display_name=display_name
        )
