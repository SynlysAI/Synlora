"""文件 API：multipart 上传（逐项校验/配额）、列表、下载与删除，按 user_id 隔离。

上传为 207 语义的简化实现：任一文件失败不影响已成功的文件，响应统一为
逐项结果列表 [{ok, file?/error?, code?}] + 顶层 status（ok/partial/failed）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import Repos, get_current_user, get_repos
from app.services import workspace

router = APIRouter(prefix="/api/v1/files", tags=["files"])


async def _own_file(file_id: str, user: dict, repos: Repos) -> dict:
    """取当前用户自己的文件记录（不存在/非本人统一 404，不泄露存在性）。

    Args:
        file_id: 文件记录 id。
        user: 当前用户 payload。
        repos: repo 集中访问对象。

    Returns:
        文件文档。
    """
    doc = await repos.file.get(file_id)
    if doc is None or doc.get("user_id") != user["sub"]:
        raise HTTPException(404, "文件不存在")
    return doc


def _safe_file_path(root: Path, stored_path: str) -> Path | None:
    """把 stored_path 解析到该工作区 files/ 沙箱内（防路径逃逸）。

    不直接信任库里的 stored_path：resolve 后必须仍落在 files/ 目录下，
    否则（../ 逃逸、指向 output/tmp、绝对路径替换等）返回 None。
    目录不存在时也返回 None（resolve 在 Windows 上对不存在路径可抛 OSError）。

    Args:
        root: 用户工作区根。
        stored_path: 相对工作区根的存储路径。

    Returns:
        沙箱内的绝对路径；越界或不可解析时 None。
    """
    files_dir = (root / "files").resolve()
    try:
        candidate = (root / stored_path).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(files_dir):
        return None
    return candidate


@router.post("")
async def upload_files(request: Request, files: list[UploadFile] = File(...),
                       user=Depends(get_current_user),
                       repos=Depends(get_repos)) -> dict:
    """multipart 多文件上传：逐项校验（黑名单 422 / 大小与配额 413）后落盘入库。

    逐个处理、失败即跳过：已成功的文件保留；响应为逐项结果 + 顶层
    status（全部成功 201，否则 200）。

    Returns:
        {status: ok|partial|failed, results: [{ok, file?, error?, code?}]}。
    """
    settings = request.app.state.settings
    root = workspace.workspace_root(settings.data_root, user["sub"])
    files_dir = root / "files"
    results: list[dict] = []
    for f in files:
        # 客户端文件名只取 basename，杜绝上传名自身携带路径成分
        fname = Path(f.filename or "").name or "unnamed"
        content = await f.read()
        size = len(content)
        try:
            workspace.validate_upload(fname, size)
        except ValueError as exc:
            # 经模块属性现取上限值（测试可 monkeypatch），与 validate_upload 判定保持一致
            code = 413 if size > workspace.MAX_FILE_BYTES else 422
            results.append({"ok": False, "code": code, "error": str(exc)})
            continue
        try:
            workspace.check_quota(root, size, settings.user_quota_bytes)
        except ValueError as exc:
            results.append({"ok": False, "code": 413, "error": str(exc)})
            continue
        target = workspace.unique_target(files_dir, fname)
        target.write_bytes(content)
        doc = await repos.file.create({
            "user_id": user["sub"],
            "filename": fname,
            "stored_path": target.relative_to(root).as_posix(),
            "size": size,
            "mime": f.content_type or "application/octet-stream",
        })
        results.append({"ok": True, "file": {
            "_id": doc["_id"], "filename": fname, "size": size}})
    ok_count = sum(1 for r in results if r["ok"])
    status = "ok" if ok_count == len(results) else ("partial" if ok_count else "failed")
    # 全部成功按创建语义回 201；存在失败项回 200（207 简化为顶层 status 字段）
    return JSONResponse({"status": status, "results": results},
                        status_code=201 if status == "ok" else 200)


@router.get("")
async def list_files(user=Depends(get_current_user),
                     repos=Depends(get_repos)) -> list[dict]:
    """当前用户文件列表（created_at 倒序；created_at 非索引列，Python 端排序）。"""
    docs = await repos.file.list(filters={"user_id": user["sub"]})
    # 同批上传时间戳可能同值，次序按文件名兜底保证稳定
    return sorted(docs, key=lambda d: (-float(d.get("created_at") or 0),
                                       str(d.get("filename", ""))))


@router.get("/{file_id}/download")
async def download_file(file_id: str, request: Request,
                        user=Depends(get_current_user),
                        repos=Depends(get_repos)) -> FileResponse:
    """下载文件（归属校验 404；stored_path 经 files/ 沙箱校验，越界 404）。

    Raises:
        HTTPException: 不存在/非本人/路径越界/磁盘文件缺失（统一 404）。
    """
    doc = await _own_file(file_id, user, repos)
    root = workspace.workspace_root(request.app.state.settings.data_root, user["sub"])
    candidate = _safe_file_path(root, str(doc.get("stored_path", "")))
    if candidate is None or not candidate.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(candidate, filename=str(doc.get("filename") or candidate.name))


@router.delete("/{file_id}")
async def delete_file(file_id: str, request: Request,
                      user=Depends(get_current_user),
                      repos=Depends(get_repos)) -> dict:
    """删除文件：磁盘文件（忽略缺失；越界路径不动磁盘）+ 文档记录。"""
    doc = await _own_file(file_id, user, repos)
    root = workspace.workspace_root(request.app.state.settings.data_root, user["sub"])
    candidate = _safe_file_path(root, str(doc.get("stored_path", "")))
    if candidate is not None:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass  # 磁盘清理失败不阻断记录删除（记录在而文件缺失可被下载 404 兜底）
    await repos.file.delete(file_id)
    return {"ok": True}
