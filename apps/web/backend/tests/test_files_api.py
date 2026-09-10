"""文件 API 测试：上传/列表/下载/删除全链路、重名序号、类型与大小校验、
配额、用户隔离与 stored_path 路径逃逸防护。"""
from pathlib import Path


def _files_dir(app) -> Path:
    """取普通用户（u-user）工作区的 files 目录。

    Args:
        app: 已初始化的 FastAPI 实例。

    Returns:
        {data_root}/workspaces/u-user/files 路径。
    """
    return app.state.settings.data_root / "workspaces" / "u-user" / "files"


async def _download(client, headers, doc) -> bytes:
    """下载指定文件文档，返回响应字节。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        doc: 文件文档。

    Returns:
        响应体字节。
    """
    r = await client.get(f"/api/v1/files/{doc['_id']}/download", headers=headers)
    assert r.status_code == 200, r.text
    return r.content


def _upload(client, headers, *items):
    """构造 multipart 上传请求。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        items: (filename, content) 元组列表。

    Returns:
        POST /api/v1/files 的响应。
    """
    return client.post("/api/v1/files", headers=headers,
                       files=[("files", (name, content, "text/plain"))
                              for name, content in items])


# ---------- 上传/列表/下载/删除全链路 ----------


async def test_upload_list_download_delete(app, client, user_headers):
    """上传两文件 → 列表 2 项 → 下载字节一致 → 删除后列表 1 项且磁盘文件清理。"""
    r = await _upload(client, user_headers,
                      ("alpha.txt", b"hello alpha"), ("beta.txt", b"hello beta"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ok"
    created = [item["file"] for item in body["results"]]
    assert [c["filename"] for c in created] == ["alpha.txt", "beta.txt"]
    assert created[0]["size"] == len(b"hello alpha")
    assert all(c["_id"] for c in created)
    ids = {c["_id"] for c in created}

    r = await client.get("/api/v1/files", headers=user_headers)
    assert r.status_code == 200
    listed = r.json()
    assert {f["_id"] for f in listed} == ids
    assert len(listed) == 2

    r = await client.get(f"/api/v1/files/{created[0]['_id']}/download",
                         headers=user_headers)
    assert r.status_code == 200
    assert r.content == b"hello alpha"

    r = await client.delete(f"/api/v1/files/{created[0]['_id']}",
                            headers=user_headers)
    assert r.status_code == 200 and r.json()["ok"] is True

    # 删除后：列表只剩 1 项、磁盘只剩 beta.txt、再下载 404
    r = await client.get("/api/v1/files", headers=user_headers)
    assert [f["filename"] for f in r.json()] == ["beta.txt"]
    assert sorted(p.name for p in _files_dir(app).iterdir()) == ["beta.txt"]
    assert (await client.get(f"/api/v1/files/{created[0]['_id']}/download",
                             headers=user_headers)).status_code == 404


async def test_download_missing_404(client, user_headers):
    """下载不存在的文件 id 应 404。"""
    r = await client.get("/api/v1/files/nope/download", headers=user_headers)
    assert r.status_code == 404


# ---------- 重名自动加序号 ----------


async def test_duplicate_name_numbering(app, client, user_headers):
    """同文件名传两次：files/ 下两个物理文件（name.ext / name_1.ext），各自可下载。"""
    r1 = await _upload(client, user_headers, ("dup.txt", b"first"))
    r2 = await _upload(client, user_headers, ("dup.txt", b"second"))
    assert r1.status_code == 201 and r2.status_code == 201

    r = await client.get("/api/v1/files", headers=user_headers)
    docs = r.json()
    assert len(docs) == 2
    assert all(d["filename"] == "dup.txt" for d in docs)
    assert sorted(d["stored_path"] for d in docs) == [
        "files/dup.txt", "files/dup_1.txt"]
    assert sorted(p.name for p in _files_dir(app).iterdir()) == [
        "dup.txt", "dup_1.txt"]

    # 两个物理文件内容各自独立、与各自上传内容一致
    assert {await _download(client, user_headers, docs[0]),
            await _download(client, user_headers, docs[1])} == {b"first", b"second"}


# ---------- 类型黑名单 / 大小上限 / 部分成功 ----------


async def test_blocked_extension_and_oversize(app, client, user_headers, monkeypatch):
    """.exe 黑名单 422、超大小上限 413：逐项失败、零落盘、零入库。"""
    monkeypatch.setattr("app.services.workspace.MAX_FILE_BYTES", 100)
    r = await _upload(client, user_headers,
                      ("evil.exe", b"MZ"), ("big.txt", b"x" * 200))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    exe_item, big_item = body["results"]
    assert exe_item["ok"] is False and exe_item["code"] == 422
    assert "不允许" in exe_item["error"]
    assert big_item["ok"] is False and big_item["code"] == 413

    assert (await client.get("/api/v1/files", headers=user_headers)).json() == []
    assert not any(_files_dir(app).iterdir())


async def test_partial_upload_keeps_successes(app, client, user_headers):
    """混合上传（一好一坏）：成功的保留，失败逐项报错，顶层 partial。"""
    r = await _upload(client, user_headers,
                      ("good.txt", b"good"), ("evil.bat", b"bad"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "partial"
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["code"] == 422

    listed = (await client.get("/api/v1/files", headers=user_headers)).json()
    assert [f["filename"] for f in listed] == ["good.txt"]
    assert sorted(p.name for p in _files_dir(app).iterdir()) == ["good.txt"]


# ---------- 配额 ----------


async def test_quota_exceeded(app, client, user_headers, monkeypatch):
    """配额用尽：上传被拒（413 逐项），不入库不落盘。"""
    monkeypatch.setattr(app.state.settings, "user_quota_bytes", 10)
    r = await _upload(client, user_headers, ("q.txt", b"x" * 20))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    item = body["results"][0]
    assert item["ok"] is False and item["code"] == 413
    assert "配额" in item["error"]
    assert (await client.get("/api/v1/files", headers=user_headers)).json() == []
    assert not any(_files_dir(app).iterdir())


# ---------- 用户隔离 ----------


async def test_user_isolation(client, user_headers, admin_headers):
    """user 的文件对其他用户不可见、不可下载/删除（统一 404）。"""
    r = await _upload(client, user_headers, ("mine.txt", b"secret-mine"))
    fid = r.json()["results"][0]["file"]["_id"]

    assert (await client.get("/api/v1/files", headers=admin_headers)).json() == []
    assert (await client.get(f"/api/v1/files/{fid}/download",
                             headers=admin_headers)).status_code == 404
    assert (await client.delete(f"/api/v1/files/{fid}",
                                headers=admin_headers)).status_code == 404

    # 本人文件不受他人操作影响
    listed = (await client.get("/api/v1/files", headers=user_headers)).json()
    assert [f["_id"] for f in listed] == [fid]


# ---------- stored_path 路径逃逸防护（Critical） ----------


async def test_stored_path_escape_blocked(app, client, user_headers):
    """DB stored_path 被篡改为 ../../evil.txt 后下载：404，工作区外文件不被读出。"""
    r = await _upload(client, user_headers, ("inner.txt", b"inner"))
    fid = r.json()["results"][0]["file"]["_id"]

    # 在工作区外（data_root 上级）放置诱饵文件，并把 stored_path 改为指向它
    evil = app.state.settings.data_root.parent / "evil.txt"
    evil.write_text("pwned", encoding="utf-8")
    await app.state.store.update("files", fid, {"stored_path": "../../evil.txt"})

    resp = await client.get(f"/api/v1/files/{fid}/download", headers=user_headers)
    assert resp.status_code in (400, 404)
    assert b"pwned" not in resp.content

    # 指向工作区内 files/ 之外（output/）同样拒绝
    await app.state.store.update("files", fid, {"stored_path": "../output/x.txt"})
    resp = await client.get(f"/api/v1/files/{fid}/download", headers=user_headers)
    assert resp.status_code in (400, 404)
