"""受限 Python 子进程执行器（python.run 的实现核心）。"""
from __future__ import annotations

import asyncio
import os
import sys

from ..types import ToolResult

ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")


async def run_python(
    code: str,
    cwd,
    timeout_s: float = 60.0,
    max_output_bytes: int = 65_536,
) -> ToolResult:
    """在受限子进程中执行 Python 代码。

    限制：解释器 -I（隔离模式，忽略用户 site 与环境变量）+ -X utf8
    （stdio 强制 UTF-8，修复 zh-CN Windows 管道 cp936 乱码）；环境变量白名单；
    cwd 锁定；stdout+stderr 合并截断；内部超时与外部取消都会杀死子进程。

    Args:
        code: 要执行的 Python 源码。
        cwd: 工作目录（调用方保证已限制在用户沙箱内）。
        timeout_s: 超时秒数。
        max_output_bytes: 输出字节上限。

    Returns:
        ToolResult：content 为合并输出；data 含 exit_code/timed_out。
    """
    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-X", "utf8", "-c", code,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as exc:
        return ToolResult(ok=False, content=f"无法启动沙箱进程: {exc}", error="spawn_failed")

    timed_out = False
    try:
        try:
            async with asyncio.timeout(timeout_s):
                raw, _ = await proc.communicate()
        except TimeoutError:
            timed_out = True
            proc.kill()
            raw, _ = await proc.communicate()
    except asyncio.CancelledError:
        # 管线超时/外部取消路径：TimeoutError 分支管不到这里，
        # 不主动 kill 会泄漏一个 100% CPU 的孤儿进程。
        proc.kill()
        try:
            await asyncio.shield(proc.communicate())
        except asyncio.CancelledError:
            pass
        raise

    text = raw.decode("utf-8", errors="replace")
    truncated = len(raw) > max_output_bytes
    if truncated:
        text = text[:max_output_bytes]  # 按字符截断（字符数 ≥ 字节数，保守上界）
    if timed_out:
        return ToolResult(
            ok=False, content=f"执行超时（>{timeout_s}s），进程已终止。\n部分输出:\n{text}",
            error="timeout", data={"timed_out": True},
        )
    return ToolResult(
        ok=proc.returncode == 0,
        content=text or "(无输出)",
        truncated=truncated,
        data={"exit_code": proc.returncode, "timed_out": False},
    )
