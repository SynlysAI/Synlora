"""技能：磁盘 SKILL.md 的扫描/解析/写入（不入库）。

内置技能由 catalog 只读根（`apps/web/backend/catalog/skills`）**直接提供**，
与插件技能根同一模式：不落 `{data_root}/public/skills`、不可删（标记 `builtin=True`）。
`{data_root}/users/{uid}/skills` 是**用户自建根**：仅该用户可见，可写可删（传 `user_id` 时并入）。
`{data_root}/public/skills` 是**公共层**：管理员自建/导入的技能，可写可删、始终可见。
同名时按「用户根 → 公共层 → 只读根」的顺序优先（`list_skills` 依次扫描并由 `seen` 去重）。

字段与正文骨架遵循 jiuwen `skill-spec.md`：frontmatter 必填
`name`（= 目录名，kebab-case）/ `description`（做什么 + 何时用），可选
`version` / `author` / `tags` / `allowed_tools`；正文骨架
`# 标题 → ## 目标 → ## 工作流 → ## 决策规则 → ## 输出要求`。

frontmatter 刻意不声明 `tools`：权限由系统分配（jiuwen 明确禁止）。
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import yaml

from app.services.workspace import public_skills_root
from app.services.workspace import user_skills_root as workspace_skills_root

logger = logging.getLogger(__name__)

NAME_OK = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_skill_md(text: str) -> dict:
    """解析 SKILL.md 全文。

    Args:
        text: SKILL.md 内容。

    Returns:
        含 name/description/version/author/tags/allowed_tools/content 的字典。

    Raises:
        ValueError: frontmatter 缺失，或缺 name/description，或 name 非 kebab-case。
    """
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("缺少 frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not NAME_OK.match(name):
        raise ValueError(f"技能名必须是 kebab-case: {name!r}")
    if not description:
        raise ValueError("description 必填")
    return {
        "name": name,
        "description": description,
        "version": str(meta.get("version") or "1.0"),
        "author": str(meta.get("author") or ""),
        "tags": [str(t) for t in (meta.get("tags") or [])],
        "allowed_tools": [str(t) for t in (meta.get("allowed_tools") or [])],
        "content": match.group(2).strip(),
    }


def render_skill_md(skill: dict) -> str:
    """把技能字段渲染回 SKILL.md 文本（落盘/导出共用）。

    Args:
        skill: 含 name/description/content 等的字典。

    Returns:
        SKILL.md 全文（frontmatter + 正文）。
    """
    front = yaml.safe_dump(
        {
            "name": skill["name"],
            "description": skill["description"],
            "version": skill.get("version") or "1.0",
            "author": skill.get("author") or "",
            "tags": skill.get("tags") or [],
            "allowed_tools": skill.get("allowed_tools") or [],
        },
        allow_unicode=True, sort_keys=False,
    ).strip()
    return f"---\n{front}\n---\n\n{skill['content'].strip()}\n"


class SkillNameTaken(ValueError):
    """技能名已被占用（用户自建 / 公共层 / 内置任一来源）。

    继承 `ValueError`，调用方既有的 `except ValueError` 仍可兜住；API 层优先映射 409。
    """


class SkillService:
    """技能扫描与读写：用户自建根 + 可写公共层（{data_root}/public/skills）+ 只读根（catalog / 插件）。"""

    def __init__(self, data_root: Path, extra_roots: list[Path] | None = None) -> None:
        """保存数据根与只读技能根。

        Args:
            data_root: 应用数据根目录（其下 public/skills 为可写公共层）。
            extra_roots: 只读技能根（catalog/skills 与插件包的 skills/ 目录；
                同名时公共层优先，只读根技能不可删）。
        """
        self._data_root = data_root
        # 规范化额外技能根，避免同一目录的不同写法被重复扫描（口径与 add_root 一致）
        self._extra_roots: list[Path] = [Path(r).resolve() for r in (extra_roots or [])]

    @property
    def skills_dir(self) -> Path:
        """公共可写技能层根目录（自动创建）。

        Returns:
            {data_root}/public/skills 路径，不存在时已创建。
        """
        d = public_skills_root(self._data_root)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def user_skills_dir(self, user_id: str) -> Path:
        """某用户的自建技能根（不存在时不创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            {data_root}/users/{user_id}/skills 路径。
        """
        return workspace_skills_root(self._data_root, user_id)

    def list_own_skills(self, user_id: str) -> list[dict]:
        """扫描某用户自建技能。

        Args:
            user_id: 用户 sub。

        Returns:
            技能字典列表（builtin 恒为 False）。
        """
        return self._scan_root(self.user_skills_dir(user_id), builtin=False)

    def name_taken(self, name: str) -> str | None:
        """技能名是否已被任一来源占用。

        Args:
            name: 技能名。

        Returns:
            占用来源（"public" / "builtin"），未占用返回 None。
        """
        if (self.skills_dir / name / "SKILL.md").is_file():
            return "public"
        for root in self._extra_roots:
            if (root / name / "SKILL.md").is_file():
                return "builtin"
        return None

    def write_user_skill(self, user_id: str, *, name: str, description: str,
                         content: str, version: str = "1.0", author: str = "",
                         tags: list[str] | None = None,
                         allowed_tools: list[str] | None = None) -> dict:
        """写入某用户的自建技能（同名占用则拒绝）。

        Args:
            user_id: 用户 sub。
            name: 技能名（kebab-case，同时作为目录名）。
            description: 技能描述。
            content: 正文。
            version: 版本号。
            author: 作者。
            tags: 标签列表。
            allowed_tools: 允许的工具名列表。

        Returns:
            写入后的技能字典（builtin 恒为 False）。

        Raises:
            ValueError: 技能名不是 kebab-case。
            SkillNameTaken: 名字已被占用（自建 / 公共 / 内置）。
        """
        if not NAME_OK.match(name):
            raise ValueError(f"技能名必须是 kebab-case: {name!r}")
        if (self.user_skills_dir(user_id) / name / "SKILL.md").is_file():
            raise SkillNameTaken(f"你已有同名技能「{name}」")
        origin = self.name_taken(name)
        if origin is not None:
            label = "公共技能" if origin == "public" else "内置技能"
            raise SkillNameTaken(f"「{name}」与{label}同名，请换一个名字")
        skill = {"name": name, "description": description, "content": content,
                 "version": version, "author": author,
                 "tags": tags or [], "allowed_tools": allowed_tools or []}
        target = self.user_skills_dir(user_id) / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_skill_md(skill), encoding="utf-8")
        return {**skill, "builtin": False}

    def delete_user_skill(self, user_id: str, name: str) -> bool:
        """删除某用户的自建技能目录。

        Args:
            user_id: 用户 sub。
            name: 技能名。

        Returns:
            存在并已删除返回 True；名字非法或不存在返回 False。
        """
        if not NAME_OK.match(name):
            return False
        target = self.user_skills_dir(user_id) / name
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True

    def add_root(self, root: Path) -> None:
        """追加一个只读技能根（插件安装时调用；重复追加幂等）。

        Args:
            root: 技能根目录（其下每个子目录是一个技能）。
        """
        normalized = Path(root).resolve()
        if normalized not in self._extra_roots:
            self._extra_roots.append(normalized)

    def _scan_root(self, root: Path, *, builtin: bool) -> list[dict]:
        """扫描单个技能根目录。

        Args:
            root: 技能根目录（不存在时返回空列表）。
            builtin: 该根是否为只读根（catalog / 插件传 True，可写公共层传 False）；
                只读根技能不在可写目录里，故删不掉。

        Returns:
            技能字典列表。
        """
        out: list[dict] = []
        if not root.is_dir():
            return out
        for entry in sorted(root.iterdir()):
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            try:
                skill = parse_skill_md(md.read_text(encoding="utf-8"))
            except (ValueError, yaml.YAMLError):
                continue
            skill["builtin"] = builtin
            out.append(skill)
        return out

    def list_skills(self, user_id: str | None = None) -> list[dict]:
        """扫描全部技能（用户根 → 公共层 → 只读根，前者同名优先）。

        每个根内按目录名排序；单个技能解析失败（缺 frontmatter / 名不合法 /
        YAML 语法错误）只跳过该目录，不影响其余技能。

        Args:
            user_id: 用户 sub；None 表示不含任何用户根（管理员全局视图）。

        Returns:
            技能字典列表，每项含 `builtin` 标记。
        """
        out = self._scan_root(self.user_skills_dir(user_id), builtin=False) if user_id else []
        seen = {s["name"] for s in out}
        for skill in self._scan_root(self.skills_dir, builtin=False):
            if skill["name"] not in seen:
                seen.add(skill["name"])
                out.append(skill)
        for root in self._extra_roots:
            for skill in self._scan_root(root, builtin=True):
                if skill["name"] not in seen:
                    seen.add(skill["name"])
                    out.append(skill)
        return out

    def read_body(self, name: str, user_id: str | None = None) -> str | None:
        """读技能正文（用户根优先于公共层与只读根；不含 frontmatter）。

        Args:
            name: 技能名（即目录名）。
            user_id: 用户 sub；None 表示不含用户根。

        Returns:
            正文文本；名字非法、技能不存在或不可解析时返回 None。
        """
        if not NAME_OK.match(name):
            return None
        roots = ([self.user_skills_dir(user_id)] if user_id else []) + \
            [self.skills_dir, *self._extra_roots]
        for root in roots:
            md = root / name / "SKILL.md"
            if not md.is_file():
                continue
            try:
                return parse_skill_md(md.read_text(encoding="utf-8"))["content"]
            except (ValueError, yaml.YAMLError):
                continue  # 该根的技能损坏：跳过，继续找后续根（与 _scan_root 的"跳过"语义一致）
        return None

    def write_skill(self, *, name: str, description: str, content: str,
                    version: str = "1.0", author: str = "",
                    tags: list[str] | None = None,
                    allowed_tools: list[str] | None = None) -> dict:
        """写入（新建或覆盖）一个技能目录。

        Args:
            name: 技能名（kebab-case，同时作为目录名）。
            description: 技能描述（做什么 + 何时用）。
            content: 正文（不含 frontmatter）。
            version: 版本号。
            author: 作者。
            tags: 标签列表。
            allowed_tools: 允许的工具名列表。

        Returns:
            写入后的技能字典（`builtin` 恒为 False：写入的是可写公共层，
            非只读根来源）。

        Raises:
            ValueError: 技能名不是 kebab-case。
        """
        if not NAME_OK.match(name):
            raise ValueError(f"技能名必须是 kebab-case: {name!r}")
        skill = {"name": name, "description": description, "content": content,
                 "version": version, "author": author,
                 "tags": tags or [], "allowed_tools": allowed_tools or []}
        target = self.skills_dir / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_skill_md(skill), encoding="utf-8")
        # 写入的是可写公共层，不是只读根来源 ⇒ builtin 恒为 False
        return {**skill, "builtin": False}

    def delete_skill(self, name: str) -> bool:
        """删除技能目录。

        Args:
            name: 技能名（即目录名）。

        Returns:
            公共层目录存在并已删除返回 True；名字非法或目录不存在返回 False。
            只读根（catalog / 插件）的技能不在公共层目录里，故恒返回 False。
        """
        if not NAME_OK.match(name):
            return False
        target = self.skills_dir / name
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True

    def migrate_legacy_builtin_copies(self, catalog_skills_root: Path) -> list[str]:
        """清理公共技能目录里与 catalog 一致的旧内置副本（播种遗留）。

        只删「同名且 SKILL.md 字节完全一致」的目录——内容不同保留（可能是管理员改过，
        也可能是更早的播种残留），保留时记一条 warning：它同时因「公共层优先」
        一直遮蔽内置版本，需人工核对后决定去留。

        Args:
            catalog_skills_root: catalog 技能根（其下每个子目录是一个内置技能）。

        Returns:
            被清理的技能名列表（内容不同而保留的不在其中）。

        注：旧播种路径 `{data_root}/skills` 已废弃（公共层迁至 `public/skills`），
        本函数现在只可能命中"有人手工在 public/skills 下重建的同名同字节目录"。
        旧部署遗留的 `{data_root}/skills` 目录不再被扫描，需人工清理。
        """
        removed: list[str] = []
        if not catalog_skills_root.is_dir():
            return removed
        for src in sorted(catalog_skills_root.iterdir()):
            src_md = src / "SKILL.md"
            legacy_md = self.skills_dir / src.name / "SKILL.md"
            if not src_md.is_file() or not legacy_md.is_file():
                continue
            try:
                if src_md.read_bytes() != legacy_md.read_bytes():
                    logger.warning(
                        "公共层技能 %s 与内置版本同名但内容不同，会遮蔽内置版（若为旧播种残留，"
                        "请人工删除 %s 以改用内置版）", src.name, self.skills_dir / src.name)
                    continue  # 内容不同：可能是管理员改过，保留
            except OSError:
                continue
            shutil.rmtree(self.skills_dir / src.name, ignore_errors=True)
            removed.append(src.name)
        return removed
