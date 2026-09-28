"""stdio MCP 子进程客户端（按行 JSON-RPC，不依赖 mcp SDK）。

协议：spawn 子进程，stdin/stdout 按行读写 JSON-RPC；启动即 initialize 握手 +
notifications/initialized。进程生命周期由调用方（McpService）管理。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 握手版本回退序：新版本失败（服务端拒绝）时换旧版本重建进程重试一次
PROTOCOL_VERSIONS = ("2025-11-25", "2024-11-05")
CLIENT_INFO = {"name": "Synlora", "version": "0.14"}


class StdioMcpClient:
    """一个 stdio MCP 子进程连接。"""

    def __init__(self, command: str, args: list[str], cwd: str = "",
                 env: dict[str, str] | None = None, timeout_s: float = 60.0) -> None:
        """保存连接参数（未启动）。

        Args:
            command: 启动命令。
            args: 命令参数。
            cwd: 工作目录（空 = 继承当前进程）。
            env: 附加环境变量（叠加在 os.environ 之上）。
            timeout_s: 单次请求超时秒数。
        """
        self._command = command
        self._args = list(args)
        self._cwd = cwd or None
        self._env = {**env} if env else {}
        self._timeout_s = timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        """进程是否存活。"""
        return self._process is not None and self._process.returncode is None

    async def start(self, protocol_version: str) -> None:
        """spawn 子进程并完成 initialize 握手。

        Args:
            protocol_version: MCP 协议版本（握手失败由调用方换版本重试）。

        Raises:
            RuntimeError: spawn 失败、握手超时或服务端返回错误。
        """
        self._process = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            cwd=self._cwd,
            env={**os.environ, **self._env},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # stderr 只记录不阻断：子进程往 stderr 打日志不该撑爆管道
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        init_id = uuid.uuid4().hex
        self._send({
            "jsonrpc": "2.0", "id": init_id, "method": "initialize",
            "params": {"protocolVersion": protocol_version, "capabilities": {},
                       "clientInfo": CLIENT_INFO},
        })
        await asyncio.wait_for(self._read_until(init_id), timeout=self._timeout_s)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def request(self, method: str, params: dict) -> dict:
        """发一个 JSON-RPC 请求并等待对应 id 的响应。

        Args:
            method: JSON-RPC 方法名。
            params: 参数。

        Raises:
            RuntimeError: 进程已死、超时或服务端返回 error。
        """
        if not self.alive:
            raise RuntimeError("MCP 子进程已退出")
        request_id = uuid.uuid4().hex
        self._send({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params})
        return await asyncio.wait_for(
            self._read_until(request_id), timeout=self._timeout_s)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用远程工具（tools/call）。"""
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        """终止子进程并停止 stderr 泵。"""
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None

    def _send(self, payload: dict) -> None:
        """写一行 JSON 到 stdin。"""
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    async def _read_until(self, request_id: str) -> dict:
        """逐行读 stdout 直到匹配 id 的响应（忽略通知行）。"""
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                raise RuntimeError("MCP 子进程 stdout 已关闭")
            try:
                payload = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or str(payload.get("id")) != request_id:
                continue
            if payload.get("error"):
                error = payload["error"]
                raise RuntimeError(str(
                    error.get("message") if isinstance(error, dict) else error))
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("MCP 响应缺少 result")
            return result

    async def _drain_stderr(self) -> None:
        """持续读 stderr 并按行记日志（防止子进程因管道写满而阻塞）。"""
        assert self._process is not None and self._process.stderr is not None
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            logger.info("MCP stderr: %s", line.decode("utf-8", "replace").rstrip())


async def connect_stdio(pkg: Any) -> StdioMcpClient:
    """按版本回退序连接 stdio MCP 服务。

    Args:
        pkg: McpPackage（command/args/cwd/env/timeout_s）。

    Returns:
        已完成握手的 StdioMcpClient。

    Raises:
        RuntimeError: 全部版本握手失败。
    """
    last_error: Exception | None = None
    for version in PROTOCOL_VERSIONS:
        client = StdioMcpClient(
            pkg.command, list(pkg.args or []), pkg.cwd,
            dict(pkg.env or {}), float(pkg.timeout_s or 60.0))
        try:
            await client.start(version)
            return client
        except Exception as exc:  # 该版本握手失败：销毁进程换下一版本
            await client.close()
            last_error = exc
    raise RuntimeError(f"stdio MCP 握手失败（{pkg.id}）: {last_error}")
