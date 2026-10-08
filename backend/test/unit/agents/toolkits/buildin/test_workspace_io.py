"""工作区 IO 公共模块的单元测试（FakeRuntime，不依赖沙盒服务）。"""

from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.buildin._workspace_io import (
    download_workspace_file,
    resolve_virtual_path,
    upload_workspace_file,
)


REL_WORKDIR = "projects/2026-01-01_00-00-00_demo"
RUNTIME_WORKDIR = "/home/gem/user-data/projects/2026-01-01_00-00-00_demo"


def _fake_runtime():
    """构造带 runtime scope 的假 ToolRuntime（workdir 为数据库真实形态：无斜杠相对路径）。"""
    scope = {
        "runtime_scope_id": "run-123",
        "uid": "u-abc",
        "workdir_relative_path": REL_WORKDIR,
    }
    return SimpleNamespace(config={"configurable": scope}, context=None, state=None)


def test_resolve_rejects_empty_path():
    with pytest.raises(ValueError):
        resolve_virtual_path("", _fake_runtime())


def test_resolve_rejects_parent_escape():
    for bad in ("../etc/passwd", "outputs/../../x.txt", "/a/../b"):
        with pytest.raises(ValueError):
            resolve_virtual_path(bad, _fake_runtime())


def test_resolve_normalizes_relative_and_absolute():
    runtime = _fake_runtime()
    # 相对路径 → runtime 绝对虚拟路径（沙盒 backend 文件操作的合法口径）
    assert resolve_virtual_path("uploads/a.txt", runtime) == f"{RUNTIME_WORKDIR}/uploads/a.txt"
    # 模型从 read_file/ls 看到的 runtime 绝对路径原样接受
    assert resolve_virtual_path(f"{RUNTIME_WORKDIR}/uploads/a.txt", runtime) == f"{RUNTIME_WORKDIR}/uploads/a.txt"
    # 持久化相对口径（projects/... 与 /projects/...）同样接受并转换为 runtime 绝对
    assert resolve_virtual_path(f"{REL_WORKDIR}/uploads/a.txt", runtime) == f"{RUNTIME_WORKDIR}/uploads/a.txt"
    assert resolve_virtual_path(f"/{REL_WORKDIR}/uploads/a.txt", runtime) == f"{RUNTIME_WORKDIR}/uploads/a.txt"


def test_resolve_rejects_paths_outside_workdir():
    # workdir 之外的绝对路径一律拒绝（其他 project、猜错前缀的 /home/gem/...）
    for bad in ("/home/gem/other/x.txt", "/uploads/a.txt", "/home/gem/user-data/projects/other/x.txt"):
        with pytest.raises(ValueError):
            resolve_virtual_path(bad, _fake_runtime())


@pytest.mark.asyncio
async def test_download_missing_scope_raises(monkeypatch):
    runtime = SimpleNamespace(config={"configurable": {}}, context=None, state=None)
    with pytest.raises(ValueError, match="运行时"):
        await download_workspace_file("a.txt", runtime, max_bytes=100)


@pytest.mark.asyncio
async def test_upload_then_download_roundtrip(monkeypatch, tmp_path):
    """用假 backend 验证 upload 写入与路径回传（文件操作走 runtime 绝对口径）。"""
    from yuxi.agents.toolkits.buildin import _workspace_io

    stored = {}

    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def upload_authorized_file_from_path(self, virtual_path, local_path):
            stored[virtual_path] = open(local_path, "rb").read()

        def download_authorized_file_to_path(self, virtual_path, local_path, max_bytes):
            if virtual_path not in stored:
                raise ValueError(f"文件不存在或不是普通文件: {virtual_path}")
            with open(local_path, "wb") as fh:
                fh.write(stored[virtual_path])

    monkeypatch.setattr(_workspace_io, "ProvisionerSandboxBackend", FakeBackend)
    runtime = _fake_runtime()

    display = await upload_workspace_file(b"hello", "outputs/r.txt", runtime)
    assert display == "outputs/r.txt"
    # backend 收到的必须是 /home/gem/user-data 前缀（授权根校验口径）
    assert stored[f"{RUNTIME_WORKDIR}/outputs/r.txt"] == b"hello"

    # 相对路径读取
    data, name = await download_workspace_file("outputs/r.txt", runtime, max_bytes=100)
    assert data == b"hello"
    assert name == "r.txt"

    # 模型直传 runtime 绝对路径（真实运行时模型最自然的形态）
    data, name = await download_workspace_file(
        f"{RUNTIME_WORKDIR}/outputs/r.txt", runtime, max_bytes=100)
    assert data == b"hello"
    assert name == "r.txt"
