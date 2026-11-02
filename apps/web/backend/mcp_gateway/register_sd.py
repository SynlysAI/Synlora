"""把网关注册进学生 ScienceDiscovery 的 CLI。

用法：
    python -m mcp_gateway.register_sd --sd-base-url http://192.168.1.10:5300 \
        --sd-token <SCIENCE_AGENT_AUTH_TOKEN> [--gateway-url http://campus-host:8090/mcp]

网关令牌取 MCP_GATEWAY_AUTH_TOKEN；慢工具（谱图异步 350s+）以 timeoutSeconds=600 注册。
"""

from __future__ import annotations

import argparse
import json
import os

import httpx

from .settings import GatewaySettings


def main() -> None:
    """注册网关为 SD custom MCP server。"""
    parser = argparse.ArgumentParser(description="Register the AI4MS MCP gateway into ScienceDiscovery")
    parser.add_argument("--sd-base-url", required=True, help="学生 SD 的 adapter 基址（如 http://192.168.1.10:5300）")
    parser.add_argument("--sd-token", required=True, help="SD 的 SCIENCE_AGENT_AUTH_TOKEN")
    parser.add_argument("--gateway-url", default="", help="网关对 SD 可达的 /mcp 地址；缺省用本机 8090")
    args = parser.parse_args()

    settings = GatewaySettings()
    gateway_token = settings.auth_token or os.environ.get("MCP_GATEWAY_AUTH_TOKEN", "")
    if not gateway_token:
        raise SystemExit("缺少 MCP_GATEWAY_AUTH_TOKEN")
    gateway_url = args.gateway_url or f"http://127.0.0.1:{settings.port}/mcp"
    payload = {
        "name": "AI4MS 专业工具网关",
        "description": "poly_agent / spec_agent 核磁与谱图解析 / sciverse 的专业工具（凭证由网关集中持有）",
        "transport": "http",
        "enabled": True,
        "command": "",
        "args": [],
        "cwd": "",
        "url": gateway_url,
        "env": {},
        "headers": {"Authorization": f"Bearer {gateway_token}"},
        "timeoutSeconds": 600,
    }
    response = httpx.post(
        f"{args.sd_base_url.rstrip('/')}/api/mcp/servers",
        headers={"Authorization": f"Bearer {args.sd_token}"},
        json=payload,
        timeout=15,
    )
    print(json.dumps({"status": response.status_code, "body": response.text[:500]}, ensure_ascii=False, indent=2))
    response.raise_for_status()


if __name__ == "__main__":
    main()
