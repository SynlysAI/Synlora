"""文件 API 测试：上传/列表/下载/删除全链路、重名序号、类型与大小校验、
配额、用户隔离、项目作用域归属与 stored_path 路径逃逸防护。

文件按项目作用域落盘（{data_root}/workspaces/{uid}/{项目目录}/files），legacy
/api/v1/files 路由解析到当前活跃项目，故本文件的磁盘断言统一取活跃项目目录。
"""
from pathlib import Path


async def _files_dir(app) -> Path:
    """取普通用户（u-user）当前活跃项目的 files 目录。

    Args:
        app: 已初始化的 FastAPI 实例。

    Returns:
        {data_root}/workspaces/u-user/{活跃项目目录}/files 路径。
    """
    service = app.state.project_service
    project = await service.resolve_active_project("u-user", None)
    return service.root_for(project) / "files"


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
    """构造 legacy multipart 上传请求（POST /api/v1/files）。

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


def _upload_to(client, headers, pid: str, *items):
    """构造项目作用域 multipart 上传请求（POST /api/v1/projects/{pid}/files）。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        pid: 项目 id。
        items: (filename, content) 元组列表。

    Returns:
        POST /api/v1/projects/{pid}/files 的响应。
    """
    return client.post(f"/api/v1/projects/{pid}/files", headers=headers,
                       files=[("files", (name, content, "text/plain"))
                              for name, content in items])


async def _new_project(client, headers, name: str = "p") -> str:
    """新建项目并返回其 id。

    Args:
        client: httpx 异步客户端。
        headers: 请求头。
        name: 项目名。

    Returns:
        新建项目的 _id。
    """
    r = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["_id"]


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
    assert sorted(p.name for p in (await _files_dir(app)).iterdir()) == ["beta.txt"]
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
    assert sorted(p.name for p in (await _files_dir(app)).iterdir()) == [
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
    assert not any((await _files_dir(app)).iterdir())


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
    assert sorted(p.name for p in (await _files_dir(app)).iterdir()) == ["good.txt"]


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
    assert not any((await _files_dir(app)).iterdir())


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


# ---------- 项目作用域上传/列表 ----------


async def test_upload_scoped_to_project(client, user_headers):
    """上传落到项目目录，且能在该项目的目录树里看到。"""
    pid = (await client.post("/api/v1/projects", json={"name": "p"},
                             headers=user_headers)).json()["_id"]
    files = {"files": ("a.txt", b"hello", "text/plain")}
    r = await client.post(f"/api/v1/projects/{pid}/files", files=files, headers=user_headers)
    assert r.status_code == 201
    listing = (await client.get(f"/api/v1/projects/{pid}/tree",
                                headers=user_headers)).json()
    assert "files" in {e["name"] for e in listing}
    inner = (await client.get(f"/api/v1/projects/{pid}/tree?path=files",
                              headers=user_headers)).json()
    assert [e["name"] for e in inner] == ["a.txt"]


async def test_upload_rejects_executable(app, client, user_headers):
    """黑名单扩展名：逐项 code 422 拒收、不落盘、不入库。

    注意（与任务给出草稿的唯一偏差）：顶层 HTTP 状态仍为 200，422 是**逐项** code。
    这是「校验与响应结构保持现状」的要求，也是既有 test_quota_exceeded（单项 413
    同样回 200）确立的契约——本端点若对单项失败回 422、配额失败回 200 将自相矛盾。
    """
    pid = await _new_project(client, user_headers)
    files = {"files": ("x.exe", b"MZ", "application/octet-stream")}
    r = await client.post(f"/api/v1/projects/{pid}/files", files=files, headers=user_headers)
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["ok"] is False and item["code"] == 422
    assert "不允许" in item["error"]

    # 被拒文件不落盘、不入库，项目 files/ 保持空
    service = app.state.project_service
    project = await service.get("u-user", pid)
    assert not any((service.root_for(project) / "files").iterdir())
    assert (await client.get(f"/api/v1/projects/{pid}/files",
                             headers=user_headers)).json() == []


async def test_upload_missing_project_404(client, user_headers):
    files = {"files": ("a.txt", b"hi", "text/plain")}
    r = await client.post("/api/v1/projects/nope/files", files=files, headers=user_headers)
    assert r.status_code == 404


async def test_project_files_listing_scoped(client, user_headers):
    """项目作用域列表只列本项目文件（另一项目的文件不出现），重名仍加序号。"""
    p1 = await _new_project(client, user_headers, "p1")
    p2 = await _new_project(client, user_headers, "p2")
    assert (await _upload_to(client, user_headers, p1, ("same.txt", b"one"))).status_code == 201
    assert (await _upload_to(client, user_headers, p1, ("same.txt", b"two"))).status_code == 201
    assert (await _upload_to(client, user_headers, p2, ("other.txt", b"three"))).status_code == 201

    first = (await client.get(f"/api/v1/projects/{p1}/files", headers=user_headers)).json()
    assert sorted(f["filename"] for f in first) == ["same.txt", "same.txt"]
    assert sorted(f["stored_path"] for f in first) == ["files/same.txt", "files/same_1.txt"]
    second = (await client.get(f"/api/v1/projects/{p2}/files", headers=user_headers)).json()
    assert [f["filename"] for f in second] == ["other.txt"]

    # 项目作用域列表对不存在的项目 404
    assert (await client.get("/api/v1/projects/nope/files",
                             headers=user_headers)).status_code == 404


async def test_project_scoped_files_not_visible_to_others(client, user_headers, admin_headers):
    """他人项目作用域上传/列表/下载均 404（项目归属校验优先于用户归属校验）。"""
    pid = await _new_project(client, user_headers)
    assert (await _upload_to(client, user_headers, pid, ("mine.txt", b"x"))).status_code == 201
    fid = (await client.get(f"/api/v1/projects/{pid}/files",
                            headers=user_headers)).json()[0]["_id"]

    assert (await client.get(f"/api/v1/projects/{pid}/files",
                             headers=admin_headers)).status_code == 404
    assert (await client.post(f"/api/v1/projects/{pid}/files", headers=admin_headers,
                              files={"files": ("y.txt", b"y", "text/plain")})).status_code == 404
    assert (await client.get(f"/api/v1/files/{fid}/download",
                             headers=admin_headers)).status_code == 404


# ---------- 回归：上传 → 下载不再 404（C5 遗留） ----------


async def test_upload_then_legacy_migration_download_not_404(app, client, user_headers):
    """C5 回归：上传后触发旧布局迁移（{uid}/files → {uid}/default/files），下载不再 404。

    修复前上传落 {uid}/files、下载按用户根解析，迁移搬走后磁盘文件已不在原处，
    下载必然 404（列表却仍有记录）。
    """
    r = await _upload(client, user_headers, ("mig.txt", b"payload"))
    assert r.status_code == 201, r.text
    fid = r.json()["results"][0]["file"]["_id"]

    # 触发旧布局迁移（list_projects → migrate_legacy_layout + 按需补种默认项目）
    await app.state.project_service.list_projects("u-user")

    resp = await client.get(f"/api/v1/files/{fid}/download", headers=user_headers)
    assert resp.status_code == 200, resp.text
    assert resp.content == b"payload"


async def test_legacy_route_upload_list_download_still_work(app, client, user_headers):
    """legacy /api/v1/files 老前端兼容：上传落到活跃项目、列表可见、下载 200。"""
    r = await _upload(client, user_headers, ("legacy.txt", b"old-payload"))
    assert r.status_code == 201 and r.json()["status"] == "ok"
    fid = r.json()["results"][0]["file"]["_id"]

    listed = (await client.get("/api/v1/files", headers=user_headers)).json()
    assert [f["_id"] for f in listed] == [fid]

    active = await app.state.project_service.resolve_active_project("u-user", None)
    assert listed[0]["project_id"] == active["_id"]
    assert listed[0]["stored_path"] == "files/legacy.txt"
    # 项目作用域列表能看到同一文件
    scoped = (await client.get(f"/api/v1/projects/{active['_id']}/files",
                               headers=user_headers)).json()
    assert [f["_id"] for f in scoped] == [fid]
    assert await _download(client, user_headers, listed[0]) == b"old-payload"


async def test_legacy_record_without_project_id_falls_back_to_active_project(app, client,
                                                                            user_headers):
    """历史记录（无 project_id、文件在旧布局 {uid}/files/）迁移后仍可下载并在列表可见。

    这正是线上真实数据形态（C6 之前上传的记录都没有 project_id）：迁移把
    {uid}/files 搬进活跃项目 default/ 后，按记录里的 project_id 解析必然落空，
    必须回落到活跃项目。
    """
    # 手工铺旧布局数据：磁盘文件在 {uid}/files/，记录缺少 project_id
    user_dir = app.state.settings.data_root / "workspaces" / "u-user"
    (user_dir / "files").mkdir(parents=True, exist_ok=True)
    (user_dir / "files" / "old.txt").write_bytes(b"old-data")
    await app.state.store.insert("files", {
        "_id": "fid-old", "user_id": "u-user", "filename": "old.txt",
        "stored_path": "files/old.txt", "size": len(b"old-data"),
        "mime": "text/plain", "created_at": 1.0, "updated_at": 1.0,
    })

    # 触发旧布局迁移并列表：历史记录视同活跃项目，不凭空消失
    listed = (await client.get("/api/v1/files", headers=user_headers)).json()
    assert [f["_id"] for f in listed] == ["fid-old"]

    resp = await client.get("/api/v1/files/fid-old/download", headers=user_headers)
    assert resp.status_code == 200, resp.text
    assert resp.content == b"old-data"

    # 磁盘确实已被迁移进活跃项目，且活跃项目的项目作用域列表与 legacy 列表口径一致
    # （历史记录跟随活跃项目：download 与 list 用的是同一个回落口径）
    service = app.state.project_service
    active = await service.resolve_active_project("u-user", None)
    assert (service.root_for(active) / "files" / "old.txt").read_bytes() == b"old-data"
    scoped = (await client.get(f"/api/v1/projects/{active['_id']}/files",
                               headers=user_headers)).json()
    assert [f["_id"] for f in scoped] == ["fid-old"]


async def test_uploaded_file_visible_in_agent_workspace(app, client, user_headers):
    """上传的文件出现在 agent 工作区根（活跃项目根）的 files/ 下。"""
    r = await _upload(client, user_headers, ("seen.txt", b"visible"))
    assert r.status_code == 201, r.text

    service = app.state.project_service
    project = await service.resolve_active_project("u-user", None)
    assert (service.root_for(project) / "files" / "seen.txt").read_bytes() == b"visible"

    # 上传返回的文档记下了项目归属
    fid = r.json()["results"][0]["file"]["_id"]
    doc = await app.state.store.get("files", fid)
    assert doc["project_id"] == project["_id"]
