"""文件 API：multipart 上传（逐项校验/配额）、列表、下载与删除，按项目作用域隔离。

文件一律落在**所属项目**的 files/（{data_root}/workspaces/{uid}/{项目目录}/files），
记录里的 stored_path 始终是「相对项目根」的路径（如 files/a.txt）；下载/删除先按记录
的 project_id 取项目根，C6 之前没有 project_id 的历史记录回落到**当前活跃项目**——
旧布局迁移正是把 {uid}/files 搬进活跃项目 default/，故老文件仍能命中。

路由两类：
- 项目作用域 /api/v1/projects/{pid}/files（上传/列表），上传前校验项目归属（404）；
- legacy /api/v1/files（上传/列表/下载/删除），上传与列表都解析到当前活跃项目，
  供老前端继续可用；下载/删除路径与行为不变。

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
# 项目作用域路由单独挂 /api/v1/projects（与 projects_api 同前缀，路径不冲突）
project_router = APIRouter(prefix="/api/v1/projects", tags=["files"])


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


async def _project_or_404(request: Request, user: dict, pid: str) -> dict:
    """取当前用户的项目（不存在/不属于本人统一 404）。

    Args:
        request: FastAPI 请求（取 project_service）。
        user: 当前用户 payload。
        pid: 项目 id。

    Returns:
        项目文档。

    Raises:
        HTTPException: 项目不存在或不属于该用户（404）。
    """
    project = await request.app.state.project_service.get(user["sub"], pid)
    if project is None:
        raise HTTPException(404, "项目不存在")
    return project


async def _file_root(request: Request, user: dict, doc: dict) -> Path:
    """解析文件记录所属项目的磁盘根。

    记录带 project_id 时按它取项目；历史记录没有 project_id（或项目已被删除）时回落
    当前活跃项目（resolve_active_project 内部会在需要时执行旧布局迁移 + 补种默认项目）。

    Args:
        request: FastAPI 请求（取 project_service）。
        user: 当前用户 payload。
        doc: 文件文档。

    Returns:
        项目根目录（files/output/tmp 已确保存在）。
    """
    service = request.app.state.project_service
    project = None
    pid = doc.get("project_id")
    if pid:
        project = await service.get(user["sub"], str(pid))
    if project is None:
        project = await service.resolve_active_project(user["sub"], None)
    return service.root_for(project)


def _safe_file_path(root: Path, stored_path: str) -> Path | None:
    """把 stored_path 解析到该项目 files/ 沙箱内（防路径逃逸）。

    不直接信任库里的 stored_path：resolve 后必须仍落在 files/ 目录下，
    否则（../ 逃逸、指向 output/tmp、绝对路径替换等）返回 None。
    目录不存在时也返回 None（resolve 在 Windows 上对不存在路径可抛 OSError）。

    Args:
        root: 项目根目录。
        stored_path: 相对项目根的存储路径。

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


def _file_sort_key(doc: dict) -> tuple:
    """文件列表排序键（created_at 倒序，同值按文件名兜底保证稳定）。

    Args:
        doc: 文件文档。

    Returns:
        (-created_at, filename) 元组。
    """
    return (-float(doc.get("created_at") or 0), str(doc.get("filename", "")))


def _belongs_to(doc: dict, project_id: str) -> bool:
    """文档是否属于该项目（无 project_id 的历史记录视同活跃项目，见 list 端点说明）。

    Args:
        doc: 文件文档。
        project_id: 活跃项目 id。

    Returns:
        True 表示应出现在该项目/活跃项目的列表里。
    """
    return doc.get("project_id") == project_id or not doc.get("project_id")


async def _active_id_if_history_exists(request: Request, user: dict, docs: list[dict]) -> str:
    """存在无 project_id 的历史记录时，返回当前活跃项目 id（否则返回空串）。

    仅在确有历史记录时才解析活跃项目：resolve_active_project 可能要迁移旧布局或
    补种默认项目，纯只读的列表请求不该在无历史记录时产生这些副作用。

    Args:
        request: FastAPI 请求（取 project_service）。
        user: 当前用户 payload。
        docs: 该用户的全部文件文档。

    Returns:
        活跃项目 id；无历史记录时为空串。
    """
    if not any(not d.get("project_id") for d in docs):
        return ""
    active = await request.app.state.project_service.resolve_active_project(user["sub"], None)
    return active["_id"]


async def _store_uploads(request: Request, files: list[UploadFile], user: dict,
                         repos: Repos, project: dict) -> JSONResponse:
    """把 multipart 文件逐个写入指定项目的 files/ 并落库。

    逐项校验（黑名单 422 / 大小与配额 413）、失败即跳过：已成功的文件保留。配额按
    用户整棵工作区树计（含各项目目录），不按单个项目计。

    Args:
        request: FastAPI 请求（取 settings）。
        files: multipart 文件列表。
        user: 当前用户 payload。
        repos: repo 集中访问对象。
        project: 目标项目文档（调用方已校验归属）。

    Returns:
        {status: ok|partial|failed, results: [{ok, file?, error?, code?}]}，
        全部成功 201，否则 200。
    """
    settings = request.app.state.settings
    root = request.app.state.project_service.root_for(project)
    files_dir = root / "files"
    quota_root = workspace.user_root(settings.data_root, user["sub"])
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
            workspace.check_quota(quota_root, size, settings.user_quota_bytes)
        except ValueError as exc:
            results.append({"ok": False, "code": 413, "error": str(exc)})
            continue
        target = workspace.unique_target(files_dir, fname)
        target.write_bytes(content)
        doc = await repos.file.create({
            "user_id": user["sub"],
            "project_id": project["_id"],
            "filename": fname,
            # 相对项目根（files/a.txt）：下载按项目根拼回，项目改名/迁移都不失效
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


@project_router.post("/{pid}/files")
async def upload_project_files(request: Request, pid: str,
                               files: list[UploadFile] = File(...),
                               user=Depends(get_current_user),
                               repos=Depends(get_repos)) -> JSONResponse:
    """把文件上传到指定项目（multipart 字段名 files，逐项结果见 _store_uploads）。

    Args:
        request: FastAPI 请求。
        pid: 目标项目 id。
        files: multipart 文件列表。
        user: 当前登录用户。
        repos: repo 集中访问对象。

    Returns:
        逐项结果 + 顶层 status（全部成功 201，否则 200）。

    Raises:
        HTTPException: 404 表示项目不存在或不属于当前用户。
    """
    project = await _project_or_404(request, user, pid)
    return await _store_uploads(request, files, user, repos, project)


@project_router.get("/{pid}/files")
async def list_project_files(request: Request, pid: str,
                             user=Depends(get_current_user),
                             repos=Depends(get_repos)) -> list[dict]:
    """列出指定项目的文件（created_at 倒序）。

    在 Python 端按 project_id 过滤：files 集合在 sqlite 端只有 user_id 被提为真实
    索引列（COLLECTION_INDEXES），project_id 不是可过滤列，而给存量库新增索引列
    需要 ALTER（CREATE TABLE IF NOT EXISTS 不会补列），会破坏已有部署。

    无 project_id 的历史记录（C6 之前上传）在**该项目恰为活跃项目**时一并列出：
    旧布局迁移把它们的文件搬进了活跃项目，下载也回落活跃项目，否则会出现「文件在
    项目磁盘里、项目列表里却看不到」。

    Args:
        request: FastAPI 请求。
        pid: 项目 id。
        user: 当前登录用户。
        repos: repo 集中访问对象。

    Returns:
        该项目的文件文档列表。

    Raises:
        HTTPException: 404 表示项目不存在或不属于当前用户。
    """
    project = await _project_or_404(request, user, pid)
    docs = await repos.file.list(filters={"user_id": user["sub"]})
    mine = [d for d in docs if d.get("project_id") == project["_id"]]
    if await _active_id_if_history_exists(request, user, docs) == project["_id"]:
        mine += [d for d in docs if not d.get("project_id")]
    return sorted(mine, key=_file_sort_key)


@router.post("")
async def upload_files(request: Request, files: list[UploadFile] = File(...),
                       user=Depends(get_current_user),
                       repos=Depends(get_repos)) -> JSONResponse:
    """legacy 上传：未指定项目，落**当前活跃项目**的 files/（老前端兼容）。

    活跃项目由 ProjectService.resolve_active_project 解析：按绑定项目 → 首个项目 →
    按需新建默认项目；该回落在 per-user 锁内完成，并发首传不会各建一个默认项目。

    Args:
        request: FastAPI 请求。
        files: multipart 文件列表。
        user: 当前登录用户。
        repos: repo 集中访问对象。

    Returns:
        逐项结果 + 顶层 status（全部成功 201，否则 200）。
    """
    project = await request.app.state.project_service.resolve_active_project(
        user["sub"], None)
    return await _store_uploads(request, files, user, repos, project)


@router.get("")
async def list_files(request: Request, user=Depends(get_current_user),
                     repos=Depends(get_repos)) -> list[dict]:
    """legacy 列表：列出**当前活跃项目**的文件（created_at 倒序）。

    语义变更说明：本端点原先列该用户的全部文件，现随「每用户一个工作区」改造为
    「每项目一个工作区」而改为只列当前活跃项目——前端应改用
    GET /api/v1/projects/{pid}/files 按项目取数。

    无 project_id 的历史记录（C6 之前上传）仍一并列出：旧布局迁移把 {uid}/files
    搬进了活跃项目，且它们的下载/删除也回落到活跃项目，不列出等于老数据在前端
    列表里凭空消失（文件却还在磁盘上）。

    Args:
        request: FastAPI 请求。
        user: 当前登录用户。
        repos: repo 集中访问对象。

    Returns:
        当前活跃项目的文件文档列表。
    """
    project = await request.app.state.project_service.resolve_active_project(
        user["sub"], None)
    docs = await repos.file.list(filters={"user_id": user["sub"]})
    return sorted((d for d in docs if _belongs_to(d, project["_id"])),
                  key=_file_sort_key)


@router.get("/{file_id}/download")
async def download_file(file_id: str, request: Request,
                        user=Depends(get_current_user),
                        repos=Depends(get_repos)) -> FileResponse:
    """下载文件（归属校验 404；stored_path 经项目 files/ 沙箱校验，越界 404）。

    Raises:
        HTTPException: 不存在/非本人/路径越界/磁盘文件缺失（统一 404）。
    """
    doc = await _own_file(file_id, user, repos)
    root = await _file_root(request, user, doc)
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
    root = await _file_root(request, user, doc)
    candidate = _safe_file_path(root, str(doc.get("stored_path", "")))
    if candidate is not None:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass  # 磁盘清理失败不阻断记录删除（记录在而文件缺失可被下载 404 兜底）
    await repos.file.delete(file_id)
    return {"ok": True}
