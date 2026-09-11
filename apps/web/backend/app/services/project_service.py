"""项目服务：项目 CRUD 编排、目录树懒加载、旧数据迁移。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.repos import ProjectRepo
from app.services import workspace


class ProjectService:
    """项目与其磁盘目录的编排层。"""

    def __init__(self, store, data_root: Path) -> None:
        """保存仓储与数据根。

        Args:
            store: DocumentStore 实例。
            data_root: 数据根目录。
        """
        self._repo = ProjectRepo(store)
        self._data_root = data_root
        # used_dir_names → free_dir_name → create 是 check-then-act，两步之间
        # 无唯一约束可依赖（store 的索引列不支持 UNIQUE(user_id, dir_name)）。
        # 用 per-user 锁把「算名 → 建目录 → 落库」整段串行化，照 SessionRepo._count_lock 范式。
        self._user_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        """取该用户的建项目锁（懒创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            该用户专属的 asyncio.Lock。
        """
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    async def list_projects(self, user_id: str) -> list[dict]:
        """列出项目（首次访问时执行旧布局迁移并补种默认项目）。

        Args:
            user_id: 用户 sub。

        Returns:
            未归档项目文档列表（updated_at 倒序）。
        """
        # 迁移 + 补种也在锁内：并发首次加载（React 双 effect / 两个标签页）
        # 否则会补种出两条同名 default 项目
        async with self._lock_for(user_id):
            migrated = workspace.migrate_legacy_layout(self._data_root, user_id)
            if migrated:
                await self._repo.create(
                    user_id=user_id, name="默认项目",
                    dir_name=workspace.DEFAULT_PROJECT_DIR,
                )
        return await self._repo.list_for_user(user_id)

    async def create_project(self, user_id: str, name: str) -> dict:
        """新建项目并创建其目录（目录名重复时自动加后缀，不会因重名失败）。

        Args:
            user_id: 用户 sub。
            name: 项目显示名。

        Returns:
            新建的项目文档。

        Raises:
            ValueError: 项目名全被过滤为空。
        """
        base = workspace.sanitize_dir_name(name)
        if not base:
            raise ValueError("项目名不合法")
        user_dir = self._data_root / "workspaces" / user_id
        # 锁内完成「算名 → 建目录 → 落库」：并发双击建同名项目时不会算出同一个 dir_name
        async with self._lock_for(user_id):
            taken = await self._repo.used_dir_names(user_id)
            dir_name = workspace.free_dir_name(user_dir, base, taken)
            project = await self._repo.create(
                user_id=user_id, name=name.strip(), dir_name=dir_name)
            workspace.project_root(self._data_root, user_id, dir_name)
            return project

    async def delete_project(self, user_id: str, project_id: str) -> bool:
        """删除项目记录与磁盘目录（目录删不掉时改名 trash 释放名字）。

        Args:
            user_id: 用户 sub。
            project_id: 项目 id。

        Returns:
            True 表示记录与目录都已彻底删除；项目不存在返回 False。
        """
        project = await self.get(user_id, project_id)
        if project is None:
            return False
        removed = workspace.remove_project_dir(self.root_for(project))
        await self._repo.delete(project_id)
        return removed

    async def get(self, user_id: str, project_id: str) -> dict | None:
        """取项目（校验归属）。

        Args:
            user_id: 用户 sub。
            project_id: 项目 id。

        Returns:
            项目文档；不存在或不属于该用户返回 None。
        """
        doc = await self._repo.get(project_id)
        return doc if doc and doc["user_id"] == user_id else None

    def root_for(self, project: dict) -> Path:
        """项目对应的磁盘根目录。

        Args:
            project: 项目文档。

        Returns:
            {data_root}/workspaces/{user_id}/{dir_name} 路径（确保 files/output/tmp 存在）。
        """
        return workspace.project_root(
            self._data_root, project["user_id"], project["dir_name"])

    async def list_dir(self, user_id: str, project_id: str, rel: str = "") -> list[dict]:
        """列出项目内某目录的一级条目（懒加载用）。

        Args:
            user_id: 用户 sub。
            project_id: 项目 id。
            rel: 相对项目根的目录路径（空串表示根）。

        Returns:
            条目列表，每项含 name/path/is_dir/size/mtime；目录在前、同级按名排序。

        Raises:
            ValueError: 项目不存在、路径越界、或目标不是目录。
        """
        project = await self.get(user_id, project_id)
        if project is None:
            raise ValueError("项目不存在")
        root = self.root_for(project)
        target = workspace.resolve_in_project(root, rel)
        if not target.is_dir():
            raise ValueError("不是目录")
        out = []
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            stat = entry.stat()
            out.append({
                "name": entry.name,
                "path": str(entry.relative_to(root)).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return out
