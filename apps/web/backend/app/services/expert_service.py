"""用户自建专家：文件为事实源，读时幂等实例化进 assistants 集合。

文件布局：{data_root}/users/{user_id}/experts/{dir}/expert.json
文档形态与 catalog/experts 的内置专家一致，但多一个由平台维护的 `_id`
（形如 `{user_id}:{dir}`）与 `owner` 字段。

写入纪律：**先动文件再刷记录**（照 ProjectService.rename_project），失败即整体失败，
避免"文件与记录不一致"的半成品状态。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.db.repos import AssistantRepo

logger = logging.getLogger(__name__)

EXPERT_MANIFEST = "expert.json"
_SAFE_DIR = re.compile(r"[^A-Za-z0-9_一-鿿-]+")


class UserExpertService:
    """用户自建专家的文件读写与助手实例化。"""

    def __init__(self, store: Any, data_root: Path) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
            data_root: 数据根目录。
        """
        self._store = store
        self._repo = AssistantRepo(store)
        self._data_root = data_root
        # 实例化是 check-then-act（get → insert），并发下两次首访会双双 get 到 None，
        # 后者 insert 撞 _id 抛 ValueError（前端 StrictMode 双 effect / 双标签页即可触发）。
        # 用 per-user 锁把整段串行化，照 ProjectService._lock_for 范式。
        self._user_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        """取该用户的实例化锁（懒创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            该用户专属的 asyncio.Lock。
        """
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    @staticmethod
    def expert_id(user_id: str, dir_name: str) -> str:
        """专家文档 id。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            f"{user_id}:{dir_name}"。
        """
        return f"{user_id}:{dir_name}"

    @staticmethod
    def sanitize_dir_name(name: str) -> str:
        """把显示名安全化为目录名。

        Args:
            name: 专家显示名。

        Returns:
            只含字母/数字/下划线/连字符/中文的名字；全被过滤时返回空串。
        """
        return _SAFE_DIR.sub("_", name.strip()).strip("_")

    @staticmethod
    def _is_safe_dir_name(dir_name: str) -> bool:
        """目录名是否可直接拼进文件系统路径（非空、非 . / ..、不含路径分隔符）。

        `write()` 的目录名经 `sanitize_dir_name` 过滤，天然安全；但 `get_own` 的目录名
        切片自调用方给的 expert_id（T14 的 `/me/experts/{id}` 路径参数），可能含越界
        片段（如 `u1:../../u2/experts/chem` 会指到别人的专家文件）。此处是文件系统
        操作的最后一道防线，照 workspace.project_root 的口径拒绝。

        Args:
            dir_name: 待校验的目录名。

        Returns:
            True 表示可安全拼接。
        """
        return bool(dir_name) and dir_name not in {".", ".."} \
            and "/" not in dir_name and "\\" not in dir_name

    def experts_dir(self, user_id: str) -> Path:
        """某用户的专家目录（不创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            {data_root}/users/{user_id}/experts 路径。
        """
        return self._data_root / "users" / user_id / "experts"

    async def list_own(self, user_id: str) -> list[dict]:
        """列出某用户的自建专家。

        Args:
            user_id: 用户 sub。

        Returns:
            专家字典列表（按目录名排序）。
        """
        root = self.experts_dir(user_id)
        if not root.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            expert = self._load(user_id, entry.name)
            if expert is not None:
                out.append(expert)
        return out

    async def get_own(self, user_id: str, expert_id: str) -> dict | None:
        """取某用户的自建专家（校验归属）。

        Args:
            user_id: 用户 sub。
            expert_id: 专家 id（形如 {user_id}:{dir}）。

        Returns:
            专家字典；不存在、不属于该用户、或目录名越界返回 None。
        """
        prefix = f"{user_id}:"
        if not expert_id.startswith(prefix):
            return None
        dir_name = expert_id[len(prefix):]
        if not self._is_safe_dir_name(dir_name):
            return None
        return self._load(user_id, dir_name)

    async def write(self, user_id: str, *, dir_name: str, name: str, avatar: str,
                    description: str, system_prompt: str,
                    tool_whitelist: list[str] | None = None) -> dict:
        """写入（新建或覆盖）一个自建专家：先写文件，再刷 assistants 记录。

        记录走 `_upsert_record`（**覆盖刷新**）：用户刚显式改了内容，记录必须跟着变；
        这与 `ensure_instantiated` 的「已存在跳过」刻意区分开。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名（sanitize 后的名字）。
            name: 显示名。
            avatar: 头像 emoji。
            description: 描述。
            system_prompt: 人设提示词。
            tool_whitelist: 可用工具名列表。

        Returns:
            专家字典（含 _id）。

        Raises:
            ValueError: dir_name 为空或不合法。
        """
        clean = self.sanitize_dir_name(dir_name)
        if not clean:
            raise ValueError("专家名不合法")
        payload = {
            "name": name.strip() or clean,
            "avatar": avatar,
            "description": description,
            "system_prompt": system_prompt,
            "tool_whitelist": [str(t) for t in (tool_whitelist or [])],
        }
        target = self.experts_dir(user_id) / clean
        target.mkdir(parents=True, exist_ok=True)
        (target / EXPERT_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        expert = {**payload, "_id": self.expert_id(user_id, clean), "dir_name": clean}
        await self._upsert_record(user_id, expert)
        return expert

    async def delete(self, user_id: str, expert_id: str) -> bool:
        """删除自建专家（先删文件目录，再删记录）。

        Args:
            user_id: 用户 sub。
            expert_id: 专家 id。

        Returns:
            True 表示已删除；专家不存在返回 False。
        """
        expert = await self.get_own(user_id, expert_id)
        if expert is None:
            return False
        shutil.rmtree(self.experts_dir(user_id) / expert["dir_name"], ignore_errors=True)
        # 走仓储删除（而非直连 store）：把 builtin 保护收在同一处口径，不靠 id 格式兜底。
        await self._repo.delete(expert_id)
        return True

    async def ensure_instantiated(self, user_id: str) -> list[dict]:
        """把该用户的自建专家幂等实例化进 assistants（列「我的专家」时调用）。

        对每个自建专家走 `_ensure_record`（**已存在跳过、不覆盖**，照
        catalog/seed.py::seed_experts 的幂等范式）：管理员或用户在助手管理页对记录的
        改动（改名、换模型等）不该被一次列表刷新顶掉。

        整段在该用户的锁内执行，避免并发首访各自 insert 撞 _id。

        Args:
            user_id: 用户 sub。

        Returns:
            该用户的自建专家列表（含 _id）。
        """
        async with self._lock_for(user_id):
            experts = await self.list_own(user_id)
            for expert in experts:
                await self._ensure_record(user_id, expert)
            return experts

    def _manifest_path(self, user_id: str, dir_name: str) -> Path:
        """expert.json 路径。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            {data_root}/users/{uid}/experts/{dir}/expert.json。
        """
        return self.experts_dir(user_id) / dir_name / EXPERT_MANIFEST

    def _load(self, user_id: str, dir_name: str) -> dict | None:
        """读单个专家文件。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            含 _id/name/avatar/description/system_prompt/tool_whitelist/dir_name 的字典；
            文件缺失或损坏返回 None。
        """
        path = self._manifest_path(user_id, dir_name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("用户专家 manifest 解析失败，已跳过 %s", path)
            return None
        if not isinstance(data, dict) or not str(data.get("system_prompt") or "").strip():
            logger.warning("用户专家 manifest 非法（缺 system_prompt），已跳过 %s", path)
            return None
        return {
            "_id": self.expert_id(user_id, dir_name),
            "dir_name": dir_name,
            "name": str(data.get("name") or dir_name),
            "avatar": str(data.get("avatar") or ""),
            "description": str(data.get("description") or ""),
            "system_prompt": str(data["system_prompt"]),
            "tool_whitelist": [str(t) for t in (data.get("tool_whitelist") or [])],
        }

    @staticmethod
    def _record_body(user_id: str, expert: dict) -> dict:
        """自建专家 → assistants 记录体（不含 _id）。

        Args:
            user_id: 用户 sub。
            expert: 专家字典。

        Returns:
            助手文档字段字典（builtin=False，owner/source_dir 标记来源）。
        """
        return {
            "name": expert["name"], "avatar": expert["avatar"],
            "description": expert["description"],
            "system_prompt": expert["system_prompt"],
            "tool_whitelist": list(expert["tool_whitelist"]),
            "model_provider_id": None, "knowledge_base_ids": [],
            "builtin": False, "owner": user_id, "source_dir": expert["dir_name"],
        }

    async def _upsert_record(self, user_id: str, expert: dict) -> None:
        """按 _id 覆盖刷新 assistants 记录（用户显式写入时调用）。

        Args:
            user_id: 用户 sub。
            expert: 专家字典。
        """
        body = self._record_body(user_id, expert)
        if await self._store.get("assistants", expert["_id"]) is None:
            await self._store.insert("assistants", {"_id": expert["_id"], **body})
        else:
            await self._store.update("assistants", expert["_id"], body)

    async def _ensure_record(self, user_id: str, expert: dict) -> None:
        """按 _id 幂等补种 assistants 记录（已存在跳过，不覆盖）。

        与 `_upsert_record` 的区别：这里只在缺失时插入，保住管理员在助手页对记录的
        改动（照 catalog/seed.py::seed_experts 的幂等范式）。

        Args:
            user_id: 用户 sub。
            expert: 专家字典。
        """
        if await self._store.get("assistants", expert["_id"]) is not None:
            return
        await self._store.insert(
            "assistants", {"_id": expert["_id"], **self._record_body(user_id, expert)})
