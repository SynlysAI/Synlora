"""用户自建专家：文件为唯一事实源，不进全局 assistants 集合。

文件布局：{data_root}/users/{user_id}/experts/{dir}/expert.json
文档形态与 catalog/experts 的内置专家一致，读取时补 `_id`（形如
`{user_id}:{dir}`）与运行期需要的缺省字段，与助手文档同构——会话绑定
与运行期解析按 id 前缀路由到文件，隐私由「内容只存在作者目录里」构造保证。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from typing import Any

logger = logging.getLogger(__name__)

EXPERT_MANIFEST = "expert.json"
_SAFE_DIR = re.compile(r"[^A-Za-z0-9_一-鿿-]+")


class UserExpertService:
    """用户自建专家的文件读写。"""

    def __init__(self, data_root: Path) -> None:
        """保存依赖。

        Args:
            data_root: 数据根目录。
        """
        self._data_root = data_root

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
                    tool_whitelist: list[str] | None = None,
                    skill_refs: list[str] | None = None,
                    mcp_refs: list[str] | None = None,
                    suggested_prompts: list[str] | None = None) -> dict:
        """写入（新建或覆盖）一个自建专家（只写文件，无任何数据库记录）。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名（sanitize 后的名字）。
            name: 显示名。
            avatar: 头像 emoji。
            description: 描述。
            system_prompt: 人设提示词。
            tool_whitelist: 可用工具名列表。
            skill_refs: 默认绑定的技能名列表。
            mcp_refs: 默认绑定的 MCP ID 列表。
            suggested_prompts: 推荐问题列表。

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
            "skill_refs": list(dict.fromkeys(
                str(item).strip() for item in (skill_refs or []) if str(item).strip())),
            "mcp_refs": list(dict.fromkeys(
                str(item).strip() for item in (mcp_refs or []) if str(item).strip())),
            "suggested_prompts": [
                str(item).strip() for item in (suggested_prompts or []) if str(item).strip()
            ],
        }
        target = self.experts_dir(user_id) / clean
        target.mkdir(parents=True, exist_ok=True)
        (target / EXPERT_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**payload, "_id": self.expert_id(user_id, clean), "dir_name": clean}

    async def delete(self, user_id: str, expert_id: str) -> bool:
        """删除自建专家（删文件目录即完事）。

        Args:
            user_id: 用户 sub。
            expert_id: 专家 id（形如 {user_id}:{dir}）。

        Returns:
            True 表示已删除；专家不存在返回 False。
        """
        expert = await self.get_own(user_id, expert_id)
        if expert is None:
            return False
        shutil.rmtree(self.experts_dir(user_id) / expert["dir_name"], ignore_errors=True)
        return True

    @staticmethod
    async def purge_legacy_records(store: Any) -> int:
        """一次性清理历史版本实例化进 assistants 的用户专家记录（启动时调用）。

        旧版把用户专家双写进全局集合（带 owner 字段）；专家改为纯文件事实源后
        这些记录不再是事实源，启动时整批移除。

        Args:
            store: DocumentStore 实例。

        Returns:
            清理的记录数。
        """
        removed = 0
        for doc in await store.list("assistants"):
            if doc.get("owner"):
                await store.delete("assistants", str(doc["_id"]))
                removed += 1
        return removed

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
            与助手文档同构的字典（含 _id/name/avatar/.../model_provider_id/
            knowledge_base_ids 缺省字段）；文件缺失或损坏返回 None。
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
            "skill_refs": [str(t) for t in (data.get("skill_refs") or [])],
            "mcp_refs": [str(t) for t in (data.get("mcp_refs") or [])],
            "suggested_prompts": [
                str(t) for t in (data.get("suggested_prompts") or [])
            ],
            # 与助手文档同构的缺省字段（用户专家不关联模型与知识库），
            # 让运行期解析无需按来源分支取字段
            "model_provider_id": None,
            "knowledge_base_ids": [],
        }
