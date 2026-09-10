"""python.run 沙箱执行器单测。"""
import asyncio
import time

import pytest

from synlys_harness.tools.sandbox import run_python


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
    assert r.truncated and len(r.content) == 1000


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
