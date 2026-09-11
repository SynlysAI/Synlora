"""项目服务：项目 CRUD 编排、目录树懒加载、旧数据迁移。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.db.repos import ProjectRepo
from app.services import workspace


class ProjectNameTaken(ValueError):
    """项目显示名已被同用户的其它项目占用。

    继承 `ValueError`，这样既有的 `except ValueError` 调用方仍能兜住；
    API 层把本异常优先映射成 409（与「名字不合法」的 422 区分开）。
    """

logger = logging.getLogger(__name__)


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

    async def _list_locked(self, user_id: str) -> list[dict]:
        """锁内列出项目（迁移旧布局 + 按需补种默认项目）。

        调用方必须已持有该用户的锁（asyncio.Lock 不可重入，故不能直接调
        list_projects）。

        Args:
            user_id: 用户 sub。

        Returns:
            未归档项目文档列表（updated_at 倒序）。
        """
        migrated = workspace.migrate_legacy_layout(self._data_root, user_id)
        if migrated:
            await self._repo.create(
                user_id=user_id, name="默认项目",
                dir_name=workspace.DEFAULT_PROJECT_DIR,
            )
        return await self._repo.list_for_user(user_id)

    async def _create_locked(self, user_id: str, name: str, base: str) -> dict:
        """锁内新建项目（调用方必须已持有该用户的锁）。

        Args:
            user_id: 用户 sub。
            name: 项目显示名。
            base: sanitize 后的基础目录名。

        Returns:
            新建的项目文档。
        """
        user_dir = self._data_root / "workspaces" / user_id
        taken = await self._repo.used_dir_names(user_id)
        dir_name = workspace.free_dir_name(user_dir, base, taken)
        project = await self._repo.create(
            user_id=user_id, name=name.strip(), dir_name=dir_name)
        workspace.project_root(self._data_root, user_id, dir_name)
        return project

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
            return await self._list_locked(user_id)

    async def list_projects_readonly(self, user_id: str) -> list[dict]:
        """列出项目（**只读**：不迁移旧布局、不补种默认项目、不加锁）。

        与 list_projects 的区别只在副作用：files API 解析历史文件（无 project_id）
        的归属时要遍历各项目根，这发生在下载/列表这类只读请求里，不该顺手迁移旧布局
        （os.replace 搬目录）或补种默认项目（写 projects 集合）。

        Args:
            user_id: 用户 sub。

        Returns:
            未归档项目文档列表（updated_at 倒序）。
        """
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
        async with self._lock_for(user_id):
            await self._ensure_name_free(user_id, name, exclude_id=None)
            return await self._create_locked(user_id, name, base)

    async def _ensure_name_free(
        self, user_id: str, name: str, *, exclude_id: str | None
    ) -> None:
        """校验显示名未被同用户其它项目占用（须在 per-user 锁内调用）。

        只比显示名（`name`，strip 后精确比较，区分大小写）；目录名仍由
        `free_dir_name` 兜底加后缀，二者是两回事。

        Args:
            user_id: 用户 sub。
            name: 待校验的显示名。
            exclude_id: 要排除的项目 id（改名时排除自己，否则改回原名会被判重）。

        Raises:
            ProjectNameTaken: 已被其它项目占用。
        """
        target = name.strip()
        for doc in await self._repo.list_for_user(user_id):
            if doc["_id"] != exclude_id and doc.get("name") == target:
                raise ProjectNameTaken(f"已存在同名工作区「{target}」")

    async def resolve_active_project(self, user_id: str, project_id: str | None) -> dict:
        """解析会话当前应用的项目（并发安全）。

        命中绑定项目则直接用它；否则用该用户的第一个项目；一个都没有则建默认项目。
        「查列表 → 视情况新建」整体在 per-user 锁内，避免并发首条消息（双击发送 /
        双标签页 / 两条会话同时首条）各自查空后各建一个默认项目（默认项目、
        默认项目-2 两条记录 + 两个磁盘目录）。

        Args:
            user_id: 用户 sub。
            project_id: 会话绑定的项目 id（可为 None 或已失效）。

        Returns:
            项目文档。
        """
        if project_id:
            project = await self.get(user_id, project_id)
            if project is not None:
                return project
        # 回落也走 _list_locked：未消费项目列表时旧布局（{uid}/files 等）仍可能未迁移，
        # 聊天路径得自己补上，否则 python.run 会在新项目目录里找不到已上传的文件
        async with self._lock_for(user_id):
            projects = await self._list_locked(user_id)
            if projects:
                return projects[0]
            return await self._create_locked(
                user_id, name="默认项目", base=workspace.DEFAULT_PROJECT_DIR)

    async def delete_project(self, user_id: str, project_id: str) -> bool:
        """删除项目记录与磁盘目录。

        目录删除失败时 `remove_project_dir` 会把它改名成 `{name}.trash-{ts}-{uuid}`
        以释放目录名并保住数据，这属于正常兜底而非失败——只要记录删掉了就返回 True。

        Args:
            user_id: 用户 sub。
            project_id: 项目 id。

        Returns:
            True 表示记录已删除；项目不存在（或不属于该用户）返回 False。

        Raises:
            OSError: 删除目录与兜底改名均失败（此时记录会残留）。
        """
        project = await self.get(user_id, project_id)
        if project is None:
            return False
        trash_fallback = not workspace.remove_project_dir(self.root_for(project))
        await self._repo.delete(project_id)
        if trash_fallback:
            logger.warning("项目目录未能删除，已改名为 trash：%s", project["dir_name"])
        return True

    async def rename_project(self, user_id: str, project_id: str, name: str) -> dict:
        """重命名项目：显示名与磁盘目录名同步改。

        目录名按新名字重新 sanitize；与其它项目冲突时自动加后缀（重命名不会因重名失败）。
        **物理目录改名刻意放在记录更新之前**：改名失败（目录被占用等）就整体抛错、
        记录保持原样，避免出现「显示名已改、磁盘还是旧目录名」的不一致状态。

        Args:
            user_id: 用户 sub。
            project_id: 项目 id。
            name: 新显示名。

        Returns:
            更新后的项目文档。

        Raises:
            ValueError: 新名字全被过滤为空，或项目不存在。
            OSError: 磁盘目录改名失败（目录被占用等）。
        """
        base = workspace.sanitize_dir_name(name)
        if not base:
            raise ValueError("项目名不合法")
        project = await self.get(user_id, project_id)
        if project is None:
            raise ValueError("项目不存在")
        user_dir = self._data_root / "workspaces" / user_id
        old_dir = project["dir_name"]
        async with self._lock_for(user_id):
            await self._ensure_name_free(user_id, name, exclude_id=project_id)
            if base == old_dir:
                new_dir = old_dir
            else:
                taken = await self._repo.used_dir_names(user_id)
                taken.discard(old_dir)  # 自己不算占用，否则会被判成冲突而加后缀
                new_dir = workspace.free_dir_name(user_dir, base, taken)
            if new_dir != old_dir:
                src = user_dir / old_dir
                if src.is_dir():
                    src.rename(user_dir / new_dir)
            return await self._repo.rename(
                project_id, name=name.strip(), dir_name=new_dir)

    async def get(self, user_id: str, project_id: str) -> dict | None:
        """取项目（校验归属）。
        Args:
            user_id: 用户 sub。
            project_id: 项目 id。

        Returns:
            项目文档；不存在或不属于该用户返回 None。
        """
        doc = await self._repo.get(project_id)
        return doc if doc and doc.get("user_id") == user_id else None

    def root_for(self, project: dict) -> Path:
        """项目对应的磁盘根目录。

        Args:
            project: 项目文档。

        Returns:
            {data_root}/workspaces/{user_id}/{dir_name} 路径（确保 files/output/tmp 存在）。

        Raises:
            ValueError: dir_name 为空、为 . / .. 或含路径分隔符（workspace.project_root 抛出）。
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
        # 一处 resolve、两侧同源：resolve_in_project 内部也用 root.resolve() 判边界，
        # 再拿它做 entry.relative_to(root) 的相对基准。data_root 默认是相对路径
        # （settings.data_dir = "../data"），未解析时 relative_to 必抛 ValueError。
        root = self.root_for(project).resolve()
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
