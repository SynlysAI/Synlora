"""工作区 IO 公共模块的单元测试（FakeRuntime，不依赖沙盒服务）。"""

from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.buildin._workspace_io import (
    download_workspace_file,
    resolve_virtual_path,
    upload_workspace_file,
)


def _fake_runtime():
    """构造带 runtime scope 的假 ToolRuntime。"""
    scope = {
        "runtime_scope_id": "run-123",
        "uid": "u-abc",
        "workdir_relative_path": "/projects/2026-01-01_00-00-00_demo",
    }
    return SimpleNamespace(config={"configurable": scope}, context=None, state=None)


WORKDIR = "/projects/2026-01-01_00-00-00_demo"


def test_resolve_rejects_empty_path():
    with pytest.raises(ValueError):
        resolve_virtual_path("", _fake_runtime())


def test_resolve_rejects_parent_escape():
    for bad in ("../etc/passwd", "outputs/../../x.txt", "/a/../b"):
        with pytest.raises(ValueError):
            resolve_virtual_path(bad, _fake_runtime())


def test_resolve_normalizes_relative_and_absolute():
    runtime = _fake_runtime()
    assert resolve_virtual_path("uploads/a.txt", runtime) == f"{WORKDIR}/uploads/a.txt"
    # 已带 workdir 前缀的绝对虚拟路径原样接受
    assert resolve_virtual_path(f"{WORKDIR}/uploads/a.txt", runtime) == f"{WORKDIR}/uploads/a.txt"


def test_resolve_rejects_paths_outside_workdir():
    # workdir 之外的绝对路径一律拒绝（含省略 workdir 前缀的 /uploads 写法）
    for bad in ("/home/gem/other/x.txt", "/uploads/a.txt"):
        with pytest.raises(ValueError):
            resolve_virtual_path(bad, _fake_runtime())


@pytest.mark.asyncio
async def test_download_missing_scope_raises(monkeypatch):
    runtime = SimpleNamespace(config={"configurable": {}}, context=None, state=None)
    with pytest.raises(ValueError, match="运行时"):
        await download_workspace_file("a.txt", runtime, max_bytes=100)


@pytest.mark.asyncio
async def test_upload_then_download_roundtrip(monkeypatch, tmp_path):
    """用假 backend 验证 upload 写入与路径回传。"""
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
    assert stored[f"{WORKDIR}/outputs/r.txt"] == b"hello"

    data, name = await download_workspace_file("outputs/r.txt", runtime, max_bytes=100)
    assert data == b"hello"
    assert name == "r.txt"
