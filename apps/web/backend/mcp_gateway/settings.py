"""网关配置：全部来自环境变量，凭证只留在网关侧。"""

from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class GatewaySettings:
    """MCP 网关运行配置。"""

    def __init__(self) -> None:
        self.host = _env("MCP_GATEWAY_HOST", "0.0.0.0")
        self.port = int(_env("MCP_GATEWAY_PORT", "8090"))
        # 学生 SD 调网关用的 Bearer 令牌（注册进 SD 的 headers）。
        self.auth_token = _env("MCP_GATEWAY_AUTH_TOKEN")
        # 三套上游凭证：集中持有、永不下发（§10）。
        self.spec_agent = {
            "base_url": _env("SPEC_AGENT_BASE_URL"),
            "token": _env("SPEC_AGENT_TOKEN"),
        }
        self.poly_agent = {
            "base_url": _env("POLY_AGENT_BASE_URL"),
            "username": _env("POLY_AGENT_USERNAME"),
            "password": _env("POLY_AGENT_PASSWORD"),
        }
        self.sciverse = {
            "base_url": _env("SCIVERSE_BASE_URL"),
            "api_token": _env("SCIVERSE_API_TOKEN"),
        }

    def plugin_configs(self) -> dict:
        """按插件 id 命名空间输出宿主同构配置（ctx.extra["plugins"]）。"""
        return {
            "spec_agent": self.spec_agent,
            "poly_agent": self.poly_agent,
            "sciverse": self.sciverse,
        }
