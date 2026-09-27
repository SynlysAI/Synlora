"""stdio MCP 子进程客户端测试（用临时假 server 脚本）。"""
import json
import sys
import textwrap

import pytest

from app.catalog.loader import McpPackage
from app.services.mcp_service import McpService
from app.services.mcp_stdio import connect_stdio

FAKE_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        method, mid = msg["method"], msg["id"]
        if method == "initialize":
            result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {},
                      "serverInfo": {"name": "fake", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "回声",
                                 "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text",
                                   "text": json.dumps(msg["params"]["arguments"])}],
                      "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
        sys.stdout.flush()
""")


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "fake_mcp_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def _pkg(server_script):
    return McpPackage(
        id="fake", name="假服务", description="d", transport="stdio",
        command=sys.executable, args=[str(server_script)], timeout_s=10.0,
        directory=server_script.parent,
    )


@pytest.mark.asyncio
async def test_stdio_handshake_and_tools(server_script):
    client = await connect_stdio(_pkg(server_script))
    try:
        tools = await client.request("tools/list", {})
        assert tools["tools"][0]["name"] == "echo"
        result = await client.call_tool("echo", {"a": 1})
        assert json.loads(result["content"][0]["text"]) == {"a": 1}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_dead_process_lazy_restart(server_script):
    """进程在调用间隙死亡：下次调用静默懒重建（错误只在调用进行中崩溃时上抛）。"""

    class _Store:
        async def get(self, *_a, **_k):
            return None

    service = McpService(_Store(), "",
                         catalog_mcps=lambda: {"fake": _pkg(server_script)})
    tools = await service.discover_public_tools("fake")
    assert tools[0]["name"] == "echo"
    # 杀掉子进程模拟调用间隙死亡
    old = service._stdio_clients["fake"]
    old._process.kill()
    await old._process.wait()
    # 懒重建：下次调用直接换新进程成功，旧连接被清理
    tools = await service.discover_public_tools("fake")
    assert tools[0]["name"] == "echo"
    assert service._stdio_clients["fake"] is not old
    assert old._process is None
    await service.aclose()


@pytest.mark.asyncio
async def test_stdio_inflight_death_destroys_and_raises(server_script):
    """调用进行中失败：销毁缓存并上抛；下次调用重建。"""

    class _Store:
        async def get(self, *_a, **_k):
            return None

    service = McpService(_Store(), "",
                         catalog_mcps=lambda: {"fake": _pkg(server_script)})

    class _DyingClient:
        """alive 为真但 request 必炸的桩：确定性触发 except 路径。"""

        alive = True
        closed = False

        async def request(self, method, params):
            raise RuntimeError("调用中死亡")

        async def close(self):
            self.closed = True

    stub = _DyingClient()
    service._stdio_clients["fake"] = stub
    with pytest.raises(RuntimeError):
        await service._stdio_request("fake", "tools/list", {})
    assert stub.closed is True
    assert "fake" not in service._stdio_clients
    # 缓存已清：下次调用重建真实连接
    tools = await service.discover_public_tools("fake")
    assert tools[0]["name"] == "echo"
    await service.aclose()
