"""python.run 沙箱执行器单测。"""
from synlys_harness.tools.sandbox import run_python


async def test_simple_execution(tmp_path):
    """正常执行并捕获输出。"""
    r = await run_python("print('hi')", cwd=tmp_path, timeout_s=10)
    assert r.ok and "hi" in r.content


async def test_timeout_kills(tmp_path):
    """死循环超时被杀。"""
    r = await run_python("while True: pass", cwd=tmp_path, timeout_s=0.5)
    assert not r.ok and r.data["timed_out"] is True


async def test_output_truncation(tmp_path):
    """输出超过上限被截断。"""
    r = await run_python("print('x' * 5000)", cwd=tmp_path, timeout_s=10, max_output_bytes=1000)
    assert r.truncated and len(r.content.encode()) <= 1200


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
