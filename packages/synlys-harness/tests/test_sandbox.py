"""python.run 沙箱执行器单测。"""
import asyncio
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

from synlys_harness.tools.sandbox import (
    DockerCodeExecutor,
    FailingExecutor,
    LocalCodeExecutor,
    resolve_executor,
    run_python,
)
from synlys_harness.tools.execution import (
    ExecutionRequest,
    ReadOnlyResource,
    validate_execution_request,
)
from synlys_harness.tools.execution_tools import shell_run
from synlys_harness.types import ToolContext


def test_execution_request_rejects_escaping_cwd(tmp_path):
    """执行工作目录必须留在 workspace 内。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="cwd"):
        validate_execution_request(ExecutionRequest(
            argv=("python", "-V"),
            workspace_root=workspace,
            cwd="../outside",
        ))


def test_execution_request_rejects_conflicting_resource_targets(tmp_path):
    """只读资源挂载点不能相同或互为祖先。"""
    workspace = tmp_path / "workspace"
    resource_a = tmp_path / "a"
    resource_b = tmp_path / "b"
    for path in (workspace, resource_a, resource_b):
        path.mkdir()

    with pytest.raises(ValueError, match="target"):
        validate_execution_request(ExecutionRequest(
            argv=("python", "-V"),
            workspace_root=workspace,
            resources=(
                ReadOnlyResource(resource_a, PurePosixPath("/skills/a")),
                ReadOnlyResource(resource_b, PurePosixPath("/skills/a/sub")),
            ),
        ))


def test_execution_request_rejects_workspace_resource_overlap(tmp_path):
    """资源源目录不得与可写工作区形成包含关系。"""
    workspace = tmp_path / "workspace"
    resource = workspace / "skill"
    resource.mkdir(parents=True)

    with pytest.raises(ValueError, match="工作区"):
        validate_execution_request(ExecutionRequest(
            argv=("python", "-V"),
            workspace_root=workspace,
            resources=(ReadOnlyResource(
                resource,
                PurePosixPath("/skills/a"),
            ),),
        ))


async def test_shell_run_rejects_non_docker_executor(tmp_path):
    """shell.run 不能在本机或弱降级执行器上运行宿主命令。"""
    marker = tmp_path / "should-not-exist.txt"
    ctx = ToolContext(
        user_id="u1",
        run_id="r1",
        workspace_root=tmp_path,
        extra={"code_executor": FailingExecutor("docker unavailable")},
    )

    result = await shell_run(
        ctx,
        {"command": f"echo bad > {marker}", "cwd": "tmp"},
    )

    assert not result.ok and result.error == "shell_unavailable"
    assert not marker.exists()


async def test_simple_execution(tmp_path):
    """正常执行并捕获输出。"""
    r = await run_python("print('hi')", cwd=tmp_path, timeout_s=10)
    assert r.ok and "hi" in r.content


async def test_timeout_kills(tmp_path):
    """死循环超时被杀。"""
    r = await run_python("while True: pass", cwd=tmp_path, timeout_s=0.5)
    assert not r.ok and r.data["timed_out"] is True


async def test_cancel_kills_subprocess(tmp_path):
    """外部取消（CancelledError）也必须杀死子进程，不能泄漏。

    验证手段（Windows 文件锁语义）：子进程以写模式持有 marker.lock 后长眠；
    进程存活期间父进程无法删除该文件，进程被杀后句柄释放、可删除。
    """
    marker = tmp_path / "marker.lock"
    code = (
        "f = open('marker.lock', 'w')\n"
        "f.write('x')\n"
        "f.flush()\n"
        "import time\n"
        "time.sleep(300)\n"
    )
    task = asyncio.create_task(run_python(code, cwd=tmp_path, timeout_s=30))
    # 等待子进程实际启动并打开标记文件
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert marker.exists(), "子进程未能启动或未创建标记文件"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 修复语义：取消后子进程被 kill，句柄释放，文件在短期内可删除
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            marker.unlink()
            break
        except PermissionError:  # noqa: PERF203 进程未死时轮询重试
            await asyncio.sleep(0.1)
    else:
        pytest.fail("取消后 5 秒内子进程仍未退出（marker.lock 无法删除，进程泄漏）")
    assert not marker.exists()


async def test_utf8_output(tmp_path):
    """管道 stdout 下中文不乱码（-X utf8 覆盖 zh-CN Windows 的 cp936 默认编码）。"""
    r = await run_python("print('中文输出测试')", cwd=tmp_path, timeout_s=10)
    assert r.ok and "中文输出测试" in r.content


async def test_output_truncation(tmp_path):
    """输出超过上限被截断（按字符截断，不再对 ASCII 过度 4 倍截断）。"""
    r = await run_python("print('x' * 5000)", cwd=tmp_path, timeout_s=10, max_output_bytes=1000)
    assert r.truncated and r.content.startswith("x" * 1000)
    assert r.data["spill_path"].startswith("tmp/spill/")


async def test_oversized_output_spills_to_workspace(tmp_path):
    """超限输出全文落工作区 tmp/spill/，结果带相对路径与读回提示。"""
    ws = tmp_path / "ws"
    (ws / "tmp").mkdir(parents=True)
    executor = LocalCodeExecutor()

    r = await executor.execute(ExecutionRequest(
        argv=("python", "-I", "-X", "utf8", "-c", "print('x' * 5000)"),
        workspace_root=ws, cwd=".", timeout_s=60, max_output_bytes=1000,
    ))

    assert r.truncated
    spill = r.data.get("spill_path")
    assert spill and spill.startswith("tmp/spill/spill-") and spill.endswith(".txt")
    assert spill in r.content and "file.read" in r.content
    saved = (ws / spill).read_bytes()
    # Windows 本地子进程会把换行翻译为 \r\n，spill 忠实保存原始字节
    assert saved.rstrip(b"\r\n") == b"x" * 5000

    # 未超限：不产生 spill，提示不出现
    r2 = await executor.execute(ExecutionRequest(
        argv=("python", "-I", "-X", "utf8", "-c", "print('hi')"),
        workspace_root=ws, cwd=".", timeout_s=60, max_output_bytes=1000,
    ))
    assert r2.ok and "spill_path" not in r2.data and "tmp/spill" not in r2.content


async def test_isolated_env(tmp_path, monkeypatch):
    """-I 隔离模式：进程看不到注入的环境变量。"""
    monkeypatch.setenv("SYNLYS_SECRET", "leak")
    r = await run_python(
        "import os; print(os.environ.get('SYNLYS_SECRET', 'clean'))",
        cwd=tmp_path, timeout_s=10,
    )
    assert "clean" in r.content and "leak" not in r.content


async def test_writes_artifacts_to_cwd(tmp_path):
    """脚本写入的文件落在受限 cwd 中。"""
    r = await run_python(
        "open('out.txt','w').write('data')", cwd=tmp_path, timeout_s=10,
    )
    assert r.ok and (tmp_path / "out.txt").read_text() == "data"


async def test_uses_same_interpreter_family(tmp_path):
    """沙箱解释器与当前环境一致（可用已装依赖）。"""
    r = await run_python("import sys; print(sys.version_info[0])", cwd=tmp_path, timeout_s=10)
    assert r.ok and r.content.strip().startswith("3")


def test_resolve_local_default():
    """local 模式直返本机执行器，不做任何探测。"""
    executor, note = resolve_executor("local")
    assert executor.sandbox == "local"
    assert note == "local"


def test_resolve_docker_weak_fallback(monkeypatch):
    """docker 模式探测失败：非 strict 时弱回退本机（backlog 口径 sandbox=local-weak）。"""
    monkeypatch.setattr(DockerCodeExecutor, "probe", lambda self: (False, "daemon 不可达"))
    executor, note = resolve_executor("docker")
    assert executor.sandbox == "local-weak"
    assert note.startswith("local-weak") and "daemon 不可达" in note


def test_resolve_docker_strict_fails_closed(monkeypatch):
    """docker 模式探测失败 + strict：fail-closed，绝不落到本机执行。"""
    monkeypatch.setattr(DockerCodeExecutor, "probe", lambda self: (False, "镜像不存在"))
    executor, _ = resolve_executor("docker", strict=True)
    assert isinstance(executor, FailingExecutor)
    assert executor.sandbox == "unavailable"


async def test_failing_executor_refuses(tmp_path):
    """strict 不可用执行器每次调用都明确拒绝。"""
    r = await FailingExecutor("daemon 不可达").run("print('hi')", cwd=tmp_path)
    assert not r.ok and r.error == "sandbox_unavailable"
    assert "daemon 不可达" in r.content


def test_resolve_docker_ok(monkeypatch):
    """docker 模式探测通过：返回容器执行器。"""
    monkeypatch.setattr(DockerCodeExecutor, "probe", lambda self: (True, ""))
    executor, note = resolve_executor("docker")
    assert executor.sandbox == "docker"
    assert note == "docker"


def test_resolve_user_param_prefers_explicit_config():
    """显式配置的容器用户优先于属主对齐。"""
    result = DockerCodeExecutor._resolve_user_param("1000:1000", (1001, 1001))
    assert result == "1000:1000"
    assert DockerCodeExecutor._resolve_user_param("2000", (0, 0)) == "2000"


def test_resolve_user_param_aligns_workspace_owner():
    """未配置时对齐宿主工作区属主（Linux bind mount 写权限对齐）。"""
    assert DockerCodeExecutor._resolve_user_param(None, (1001, 1001)) == "1001:1001"


def test_resolve_user_param_falls_back_when_root_or_unknown():
    """属主为 root 或未知（None）时回退镜像默认非 root 用户。"""
    assert DockerCodeExecutor._resolve_user_param(None, (0, 0)) is None
    assert DockerCodeExecutor._resolve_user_param(None, None) is None


def test_workspace_owner_platform_semantics(tmp_path):
    """Windows 不做属主对齐（Docker Desktop 文件共享宽松）；Linux 返回属主。"""
    owner = DockerCodeExecutor._workspace_owner(tmp_path)
    if sys.platform == "win32":
        assert owner is None
    else:
        stat = tmp_path.stat()
        assert owner == (stat.st_uid, stat.st_gid)


async def test_python_run_uses_injected_executor(tmp_path):
    """python.run 工具优先取 ctx.extra 注入的执行器（宿主部署级注入回路）。"""
    from synlys_harness.tools.builtin import python_run
    from synlys_harness.types import ToolContext

    class _Marker:
        """记名执行器：返回固定标记便于断言被调用。"""

        def __init__(self):
            self.sandbox = "marker"

        async def run(self, code, cwd, timeout_s=60.0, max_output_bytes=65_536):
            from synlys_harness.types import ToolResult
            return ToolResult(ok=True, content="injected", data={"sandbox": self.sandbox})

        async def execute(self, request):
            from synlys_harness.types import ToolResult
            return ToolResult(ok=True, content="injected", data={"sandbox": self.sandbox})

    ctx = ToolContext(
        user_id="u", run_id="r", workspace_root=tmp_path,
        extra={"code_executor": _Marker()},
    )
    r = await python_run(ctx, {"code": "print(1)"})
    assert r.ok and r.content == "injected" and r.data["sandbox"] == "marker"
