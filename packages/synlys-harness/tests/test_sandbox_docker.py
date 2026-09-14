"""Docker 沙箱执行器集成测试（backlog 沙箱升级的逃逸用例）。

前置：docker daemon 可达且已构建 synlora-sandbox:latest
（docker build -t synlora-sandbox:latest docker/sandbox/）；
任一不满足则整文件自动 skip（开发机无 docker 不阻断 CI/本地单测）。

用例对应 backlog：
- 写工作区外路径被拦（容器文件系统私有 + 非 root 用户）
- 读不到宿主环境变量（容器 env 不继承宿主）
- 外联网络被拦（network_disabled，DNS 一并失效）
- 超时 kill、外部取消后容器与临时脚本无残留
- 正常执行：产物落 workspace（bind mount 生效）、输出截断
"""
import asyncio
import time
from pathlib import Path

import pytest

from synlys_harness.tools.sandbox import DockerCodeExecutor

EXECUTOR = DockerCodeExecutor(image="synlora-sandbox:latest")
DOCKER_OK, REASON = EXECUTOR.probe()

pytestmark = pytest.mark.skipif(not DOCKER_OK, reason=f"docker 沙箱不可用: {REASON}")


@pytest.fixture
def workspace(tmp_path) -> Path:
    """构造 workspace/tmp 布局（与 python.run 工具的目录约定一致）。"""
    ws = tmp_path / "ws"
    (ws / "tmp").mkdir(parents=True)
    return ws


@pytest.fixture(autouse=True)
def assert_no_leftovers():
    """每个用例结束后断言无 synlora-exec-* 残留容器（清理回路回归）。"""
    yield
    client = EXECUTOR._docker_client()
    leftover = client.containers.list(all=True, filters={"name": "synlora-exec-"})
    assert not leftover, f"残留容器: {[c.name for c in leftover]}"


async def test_normal_execution_and_artifacts(workspace):
    """正常执行：输出捕获 + ../ 相对路径产物落 workspace（挂载生效）。"""
    r = await EXECUTOR.run(
        "print('done')\nopen('../out.txt','w').write('data')",
        cwd=workspace / "tmp", timeout_s=60,
    )
    assert r.ok and "done" in r.content
    assert r.data["sandbox"] == "docker"
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "data"


async def test_cannot_write_outside_workspace(workspace):
    """逃逸用例 1：容器内写 / 根路径被拒（文件系统私有 + 非 root）。"""
    r = await EXECUTOR.run("open('/escape.txt','w')", cwd=workspace / "tmp", timeout_s=60)
    assert not r.ok and "PermissionError" in r.content


async def test_no_host_env_leak(workspace, monkeypatch):
    """逃逸用例 2：宿主进程环境变量不进容器。"""
    monkeypatch.setenv("SYNLYS_SECRET", "leak")
    r = await EXECUTOR.run(
        "import os; print(os.environ.get('SYNLYS_SECRET', 'clean'))",
        cwd=workspace / "tmp", timeout_s=60,
    )
    assert r.ok and "clean" in r.content and "leak" not in r.content


async def test_network_blocked(workspace):
    """逃逸用例 3：断网容器内 DNS/连接均失败。"""
    r = await EXECUTOR.run(
        "import socket\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 80)\n"
        "    print('DNS_OK')\n"
        "except OSError as e:\n"
        "    print('BLOCKED', e)\n",
        cwd=workspace / "tmp", timeout_s=60,
    )
    assert r.ok and "BLOCKED" in r.content and "DNS_OK" not in r.content


async def test_timeout_kills_container(workspace):
    """超时被杀：timed_out 标记 + 容器被删。"""
    r = await EXECUTOR.run("while True: pass", cwd=workspace / "tmp", timeout_s=5)
    assert not r.ok and r.data["timed_out"] is True
    client = EXECUTOR._docker_client()
    assert not client.containers.list(all=True, filters={"name": r.data["container"]})
    assert not list((workspace / "tmp").glob(".exec-*.py"))


async def test_cancel_cleans_container(workspace):
    """外部取消（CancelledError）：容器与临时脚本都清理，无泄漏。"""
    task = asyncio.create_task(EXECUTOR.run(
        "import time; time.sleep(300)", cwd=workspace / "tmp", timeout_s=120,
    ))
    await asyncio.sleep(3)  # 等容器创建并进入长睡
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client = EXECUTOR._docker_client()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not client.containers.list(all=True, filters={"name": "synlora-exec-"}):
            break
        await asyncio.sleep(0.5)
    else:
        pytest.fail("取消后 15 秒内容器仍未清理")
    assert not list((workspace / "tmp").glob(".exec-*.py"))


async def test_output_truncation(workspace):
    """输出超限截断（与本地执行器同语义）。"""
    r = await EXECUTOR.run(
        "print('x' * 5000)", cwd=workspace / "tmp", timeout_s=60,
        max_output_bytes=1000,
    )
    assert r.truncated and len(r.content) == 1000
