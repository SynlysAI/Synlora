"""网关启动入口（开发/PM2 共用）。"""

import uvicorn

from .settings import GatewaySettings


def main() -> None:
    """按配置启动 MCP 网关。"""
    settings = GatewaySettings()
    uvicorn.run("mcp_gateway.server:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
