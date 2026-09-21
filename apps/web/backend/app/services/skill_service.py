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

import io
import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import yaml

from app.services.workspace import public_skills_root
from app.services.workspace import user_skills_root as workspace_skills_root

if TYPE_CHECKING:
    from app.catalog.loader import SkillPackage

logger = logging.getLogger(__name__)

NAME_OK = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
MAX_SKILL_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_SKILL_EXTRACTED_BYTES = 80 * 1024 * 1024
MAX_SKILL_ARCHIVE_FILES = 500
MAX_SKILL_PREVIEW_BYTES = 2 * 1024 * 1024
PREVIEWABLE_SUFFIXES = {
    ".css", ".csv", ".html", ".ini", ".js", ".json", ".jsx", ".md",
    ".py", ".rst", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt",
    ".xml", ".yaml", ".yml",
}
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


@dataclass(frozen=True)
class ResolvedSkill:
    """正文、元数据和资源目录绑定到同一来源的技能。"""

    name: str
    description: str
    body: str
    directory: Path
    source: str
    plugin_id: str | None = None


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


def _persist_skill(target: Path, *, name: str, description: str, content: str,
                   version: str, author: str, tags: list[str] | None,
                   allowed_tools: list[str] | None) -> dict:
    """组装技能字段并落盘 SKILL.md（自建技能的写入与更新共用，保证字段集单一来源）。

    Args:
        target: 技能目录（调用方负责校验，须为已存在或待创建的目录）。
        name: 技能名。
        description: 技能描述。
        content: 正文。
        version: 版本号。
        author: 作者。
        tags: 标签列表。
        allowed_tools: 允许的工具名列表。

    Returns:
        技能字典（builtin 恒为 False）。
    """
    skill = {"name": name, "description": description, "content": content,
             "version": version, "author": author,
             "tags": tags or [], "allowed_tools": allowed_tools or []}
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(render_skill_md(skill), encoding="utf-8")
    return {**skill, "builtin": False}


class SkillNameTaken(ValueError):
    """技能名已被占用（用户自建 / 公共层 / 内置任一来源）。

    继承 `ValueError`，调用方既有的 `except ValueError` 仍可兜住；API 层优先映射 409。
    """


class SkillNotFound(ValueError):
    """目标自建技能不存在（更新/删除时调用方没有该技能）。

    与 `SkillNameTaken` 一样继承 `ValueError`，但语义不同：这个表示"你没有这个技能"，
    调用方通常映射成 403（不许改别人的），而不是 409（名字冲突）。
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
        # 插件贡献的技能根（extra_roots 的子集，root → 插件 id）：扫描时技能标
        # source='plugin' 且带 plugin=<id>——管理页据此过滤到插件页统一查看，
        # 会话级插件开关据此过滤技能选择列表
        self._plugin_roots: dict[Path, str] = {}
        self._root_names: dict[Path, frozenset[str]] = {}
        self._catalog_skills: dict[str, Path] = {}

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
            技能字典列表（builtin 恒为 False、source 恒为 'user'）。
        """
        return self._scan_root(self.user_skills_dir(user_id), builtin=False, source="user")

    def name_taken(self, name: str) -> str | None:
        """技能名是否已被任一来源占用。

        Args:
            name: 技能名。

        Returns:
            占用来源（"public" / "builtin"），未占用返回 None。
        """
        # 用纯路径函数探测（skills_dir 属性会 mkdir，查询动作不该有建目录副作用）
        if (public_skills_root(self._data_root) / name / "SKILL.md").is_file():
            return "public"
        if name in self._catalog_skills:
            return "builtin"
        for root in self._extra_roots:
            if (root / name / "SKILL.md").is_file():
                return "builtin"
        return None

    def write_user_skill(self, user_id: str, *, name: str, description: str,
                         content: str, version: str = "1.0", author: str = "",
                         tags: list[str] | None = None,
                         allowed_tools: list[str] | None = None) -> dict:
        """写入某用户的自建技能（同名占用则拒绝）。

        写前必查同名：与用户已有自建、公共层、只读根（catalog / 插件）任一来源同名
        都拒绝。覆盖写已有自建技能请改用 `update_user_skill`。

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
        return _persist_skill(
            self.user_skills_dir(user_id) / name, name=name, description=description,
            content=content, version=version, author=author,
            tags=tags, allowed_tools=allowed_tools)

    def import_user_skill_zip(self, user_id: str, archive: bytes) -> dict:
        """从 ZIP 压缩包导入一个用户技能目录。

        Args:
            user_id: 用户 sub。
            archive: ZIP 文件字节。

        Returns:
            导入后的技能字典。

        Raises:
            ValueError: 压缩包格式、结构或内容不合法。
            SkillNameTaken: 技能名已被任一来源占用。
        """
        if not archive or len(archive) > MAX_SKILL_ARCHIVE_BYTES:
            raise ValueError("技能压缩包为空或超过 20 MB")
        try:
            package = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile as exc:
            raise ValueError("不是有效的 ZIP 压缩包") from exc

        with package, tempfile.TemporaryDirectory(prefix="synlora-skill-") as temp:
            temp_root = Path(temp)
            members = package.infolist()
            if not members or len(members) > MAX_SKILL_ARCHIVE_FILES:
                raise ValueError("技能压缩包文件数量不合法")
            extracted_bytes = 0
            for member in members:
                relative = PurePosixPath(member.filename)
                if (relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or "\\" in member.filename):
                    raise ValueError(f"压缩包包含非法路径: {member.filename}")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"压缩包不允许符号链接: {member.filename}")
                extracted_bytes += member.file_size
                if extracted_bytes > MAX_SKILL_EXTRACTED_BYTES:
                    raise ValueError("技能压缩包解压后超过 80 MB")
                target = (temp_root / Path(*relative.parts)).resolve()
                try:
                    target.relative_to(temp_root.resolve())
                except ValueError as exc:
                    raise ValueError(f"压缩包包含非法路径: {member.filename}") from exc
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

            package_root = self._find_import_root(temp_root)
            if not self._directory_is_safe(package_root):
                raise ValueError("技能包包含链接或不可读取文件")
            try:
                parsed = parse_skill_md(
                    (package_root / "SKILL.md").read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
                raise ValueError(f"SKILL.md 不合法: {exc}") from exc
            name = parsed["name"]
            if package_root != temp_root and package_root.name != name:
                raise ValueError("技能目录名必须与 SKILL.md 的 name 一致")
            if (self.user_skills_dir(user_id) / name / "SKILL.md").is_file():
                raise SkillNameTaken(f"你已有同名技能「{name}」")
            origin = self.name_taken(name)
            if origin is not None:
                label = "公共技能" if origin == "public" else "内置技能"
                raise SkillNameTaken(f"「{name}」与{label}同名，请换一个名字")

            target = self.user_skills_dir(user_id) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package_root, target)
            return {**parsed, "builtin": False, "source": "user"}

    def import_public_skill_zip(self, archive: bytes) -> dict:
        """从 ZIP 压缩包导入一个公共技能目录。

        Args:
            archive: ZIP 文件字节。

        Returns:
            导入后的技能字典。

        Raises:
            ValueError: 压缩包格式、结构或内容不合法。
            SkillNameTaken: 技能名已被任一来源占用。
        """
        if not archive or len(archive) > MAX_SKILL_ARCHIVE_BYTES:
            raise ValueError("技能压缩包为空或超过 20 MB")
        try:
            package = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile as exc:
            raise ValueError("不是有效的 ZIP 压缩包") from exc

        with package, tempfile.TemporaryDirectory(prefix="synlora-public-skill-") as temp:
            temp_root = Path(temp)
            members = package.infolist()
            if not members or len(members) > MAX_SKILL_ARCHIVE_FILES:
                raise ValueError("技能压缩包文件数量不合法")
            extracted_bytes = 0
            for member in members:
                relative = PurePosixPath(member.filename)
                if (relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or "\\" in member.filename):
                    raise ValueError(f"压缩包包含非法路径: {member.filename}")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"压缩包不允许符号链接: {member.filename}")
                extracted_bytes += member.file_size
                if extracted_bytes > MAX_SKILL_EXTRACTED_BYTES:
                    raise ValueError("技能压缩包解压后超过 80 MB")
                target = (temp_root / Path(*relative.parts)).resolve()
                try:
                    target.relative_to(temp_root.resolve())
                except ValueError as exc:
                    raise ValueError(f"压缩包包含非法路径: {member.filename}") from exc
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

            package_root = self._find_import_root(temp_root)
            if not self._directory_is_safe(package_root):
                raise ValueError("技能包包含链接或不可读取文件")
            try:
                parsed = parse_skill_md(
                    (package_root / "SKILL.md").read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
                raise ValueError(f"SKILL.md 不合法: {exc}") from exc
            name = parsed["name"]
            if package_root != temp_root and package_root.name != name:
                raise ValueError("技能目录名必须与 SKILL.md 的 name 一致")
            if self.name_taken(name) is not None:
                raise SkillNameTaken(f"「{name}」已存在，请换一个名字")

            target = self.skills_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package_root, target)
            return {**parsed, "builtin": False, "source": "public"}

    def update_user_skill(self, user_id: str, name: str, *, description: str,
                          content: str, version: str = "1.0", author: str = "",
                          tags: list[str] | None = None,
                          allowed_tools: list[str] | None = None) -> dict:
        """覆盖写**已存在**的用户自建技能。

        与 `write_user_skill` 的区别：不参与同名检查（目标名已属于该用户），
        但**要求该用户目录下确有这个技能**——这条前置检查留在服务层，调用方
        无法用它创建新技能去遮蔽内置/公共技能。

        Args:
            user_id: 用户 sub。
            name: 技能名（须已是该用户的自建技能）。
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
            SkillNotFound: 该用户没有这个自建技能。
        """
        if not NAME_OK.match(name):
            raise ValueError(f"技能名必须是 kebab-case: {name!r}")
        target = self.user_skills_dir(user_id) / name
        if not (target / "SKILL.md").is_file():
            raise SkillNotFound(f"技能不存在或不属于你: {name}")
        return _persist_skill(
            target, name=name, description=description, content=content,
            version=version, author=author,
            tags=tags, allowed_tools=allowed_tools)

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

    def set_catalog_skills(self, packages: dict[str, "SkillPackage"]) -> None:
        """设置 catalog 合并后胜出的具体技能目录。

        Args:
            packages: scan_catalog 产出的技能包映射。
        """
        self._catalog_skills = {
            name: Path(os.path.abspath(package.directory))
            for name, package in packages.items()
        }

    def add_root(
        self,
        root: Path,
        *,
        plugin: str | None = None,
        names: frozenset[str] | None = None,
    ) -> None:
        """追加一个只读技能根（插件安装时调用；重复追加幂等）。

        Args:
            root: 技能根目录（其下每个子目录是一个技能）。
            plugin: 贡献该根的插件 id（None = 内置 catalog 根）；插件根扫描出的
                技能带 source='plugin' 与 plugin=<id>，供管理页/会话开关过滤。
            names: 该插件 manifest 明确声明的技能名；None 表示扫描全部。
        """
        normalized = Path(os.path.abspath(root))
        if normalized not in self._extra_roots:
            self._extra_roots.append(normalized)
        if plugin:
            self._plugin_roots[normalized] = plugin
        if names is not None:
            self._root_names[normalized] = names

    def skills_under(self, root: Path) -> list[dict]:
        """扫描指定只读根下的技能（插件页展示附属技能清单用）。

        Args:
            root: 技能根目录（通常是 PluginPackage.skills_root）。

        Returns:
            技能字典列表（builtin=True、source='plugin'）。
        """
        return self._scan_root(Path(root).resolve(), builtin=True, source="plugin")

    def _scan_root(self, root: Path, *, builtin: bool, source: str,
                   plugin_id: str | None = None) -> list[dict]:
        """扫描单个技能根目录。

        Args:
            root: 技能根目录（不存在时返回空列表）。
            builtin: 该根是否为只读根（catalog / 插件传 True，可写公共层传 False）；
                只读根技能不在可写目录里，故删不掉。
            source: 技能来源标记（catalog/public/plugin/user）。
            plugin_id: 贡献根的插件 id（source='plugin' 时写入技能字典的
                plugin 字段，其余来源不写）。

        Returns:
            技能字典列表。
        """
        out: list[dict] = []
        if not root.is_dir():
            return out
        allowed_names = self._root_names.get(root.resolve())
        for entry in sorted(root.iterdir()):
            if allowed_names is not None and entry.name not in allowed_names:
                continue
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            try:
                skill = parse_skill_md(md.read_text(encoding="utf-8"))
            except (ValueError, yaml.YAMLError):
                continue
            skill["builtin"] = builtin
            skill["source"] = source
            if source == "plugin" and plugin_id:
                skill["plugin"] = plugin_id
            out.append(skill)
        return out

    def list_skills(self, user_id: str | None = None) -> list[dict]:
        """扫描全部技能（用户根 → 公共层 → 只读根，前者同名优先）。

        用户根优先是为了处理"管理员在用户之后新增了同名公共技能"这一可达场景：
        用户写入时有同名拒绝把关，但管理员后加的同名技能不该把用户自己那份顶掉。

        每个根内按目录名排序；单个技能解析失败（缺 frontmatter / 名不合法 /
        YAML 语法错误）只跳过该目录，不影响其余技能。

        Args:
            user_id: 用户 sub；None 表示不含任何用户根（管理员全局视图）。

        Returns:
            技能字典列表，每项含 `builtin` 与 `source`（catalog/public/plugin/user）标记。
        """
        out = (self._scan_root(self.user_skills_dir(user_id), builtin=False, source="user")
               if user_id else [])
        seen = {s["name"] for s in out}
        for skill in self._scan_root(self.skills_dir, builtin=False, source="public"):
            if skill["name"] not in seen:
                seen.add(skill["name"])
                out.append(skill)
        for root in self._extra_roots:
            pid = self._plugin_roots.get(root)
            for skill in self._scan_root(root, builtin=True,
                                         source="plugin" if pid else "catalog",
                                         plugin_id=pid):
                if skill["name"] not in seen:
                    seen.add(skill["name"])
                    out.append(skill)
        for name, directory in sorted(self._catalog_skills.items()):
            if name in seen:
                continue
            rows = self._scan_root(
                directory.parent,
                builtin=True,
                source="catalog",
            )
            for skill in rows:
                if skill["name"] == name and name not in seen:
                    seen.add(name)
                    out.append(skill)
        return out

    def read_body(self, name: str, user_id: str | None = None) -> str | None:
        """读技能正文（用户根优先于公共层与只读根；不含 frontmatter）。

        用户根优先是为了处理"管理员在用户之后新增了同名公共技能"这一可达场景：
        用户写入时有同名拒绝把关，但管理员后加的同名技能不该把用户自己那份顶掉。

        Args:
            name: 技能名（即目录名）。
            user_id: 用户 sub；None 表示不含用户根。

        Returns:
            正文文本；名字非法、技能不存在或不可解析时返回 None。
        """
        if not NAME_OK.match(name):
            return None
        directory = self.skill_directory(name, user_id=user_id)
        if directory is not None:
            try:
                return parse_skill_md(
                    (directory / "SKILL.md").read_text(encoding="utf-8"))["content"]
            except (ValueError, yaml.YAMLError):
                return None
        return None

    def skill_directory(self, name: str, user_id: str | None = None) -> Path | None:
        """解析当前优先级下实际生效的技能目录。

        Args:
            name: 技能名。
            user_id: 用户 sub；None 表示不包含用户层。

        Returns:
            安全且可解析的技能目录；不存在返回 None。
        """
        if not NAME_OK.match(name):
            return None
        candidates = ([self.user_skills_dir(user_id) / name] if user_id else [])
        candidates.append(public_skills_root(self._data_root) / name)
        catalog_directory = self._catalog_skills.get(name)
        if catalog_directory is not None:
            candidates.append(catalog_directory)
        candidates.extend(root / name for root in self._extra_roots)
        for directory in candidates:
            if not (directory / "SKILL.md").is_file():
                continue
            if not self._directory_is_safe(directory):
                continue
            try:
                parsed = parse_skill_md(
                    (directory / "SKILL.md").read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            if parsed["name"] == name:
                return directory.resolve()
        return None

    def list_skill_files(self, name: str, user_id: str | None = None) -> list[dict]:
        """列出技能包文件树。

        Args:
            name: 技能名。
            user_id: 用户 sub；用于应用用户层优先级。

        Returns:
            文件树节点列表；技能不存在时返回空列表。
        """
        directory = self.skill_directory(name, user_id=user_id)
        if directory is None:
            return []

        def walk(current: Path) -> list[dict]:
            nodes: list[dict] = []
            for child in sorted(
                    current.iterdir(),
                    key=lambda item: (item.name != "SKILL.md", item.name.lower())):
                relative = child.relative_to(directory).as_posix()
                if child.is_dir():
                    nodes.append({
                        "path": relative,
                        "kind": "directory",
                        "previewable": False,
                        "children": walk(child),
                    })
                elif child.is_file():
                    size = child.stat().st_size
                    nodes.append({
                        "path": relative,
                        "kind": "file",
                        "size": size,
                        "previewable": (
                            size <= MAX_SKILL_PREVIEW_BYTES
                            and child.suffix.lower() in PREVIEWABLE_SUFFIXES | IMAGE_SUFFIXES
                        ),
                    })
            return nodes

        return walk(directory)

    def read_skill_file(self, name: str, relative_path: str,
                        user_id: str | None = None) -> dict | None:
        """读取技能包内可预览文本文件。

        Args:
            name: 技能名。
            relative_path: 技能目录内 POSIX 相对路径。
            user_id: 用户 sub；用于应用用户层优先级。

        Returns:
            文件内容字典；不存在或不可预览返回 None。
        """
        directory = self.skill_directory(name, user_id=user_id)
        if directory is None or not isinstance(relative_path, str) or "\\" in relative_path:
            return None
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        target = (directory / Path(*relative.parts)).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            return None
        if (not target.is_file() or target.stat().st_size > MAX_SKILL_PREVIEW_BYTES
                or target.suffix.lower() not in PREVIEWABLE_SUFFIXES):
            return None
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        return {"path": relative.as_posix(), "content": content, "encoding": "utf-8"}

    def skill_file_path(self, name: str, relative_path: str,
                        user_id: str | None = None) -> Path | None:
        """解析技能包内文件的安全绝对路径。

        Args:
            name: 技能名。
            relative_path: 技能目录内 POSIX 相对路径。
            user_id: 用户 sub；用于应用用户层优先级。

        Returns:
            安全文件路径；不存在、越界或超过预览大小返回 None。
        """
        directory = self.skill_directory(name, user_id=user_id)
        if directory is None or not isinstance(relative_path, str) or "\\" in relative_path:
            return None
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        target = (directory / Path(*relative.parts)).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            return None
        if not target.is_file() or target.stat().st_size > MAX_SKILL_PREVIEW_BYTES:
            return None
        return target

    def export_skill_zip(self, name: str,
                         user_id: str | None = None) -> bytes | None:
        """把技能目录打包为 ZIP，供管理端或用户侧完整导出。

        Args:
            name: 技能名。
            user_id: 用户 sub；用于按用户根优先解析技能。

        Returns:
            ZIP 文件字节；技能不存在或目录不安全时返回 None。
        """
        directory = self.skill_directory(name, user_id=user_id)
        if directory is None or not self._directory_is_safe(directory):
            return None
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(directory).as_posix())
        return buffer.getvalue()

    @staticmethod
    def _find_import_root(temp_root: Path) -> Path:
        """识别 ZIP 中唯一技能包根目录。"""
        if (temp_root / "SKILL.md").is_file():
            return temp_root
        candidates = [
            child for child in temp_root.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        ]
        if len(candidates) != 1:
            raise ValueError("压缩包根目录或唯一一级子目录中必须包含 SKILL.md")
        return candidates[0]

    @staticmethod
    def _directory_is_safe(directory: Path) -> bool:
        """拒绝技能包内符号链接及 Windows reparse 资源。"""
        try:
            for path in (directory, *directory.rglob("*")):
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    return False
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if getattr(info, "st_file_attributes", 0) & reparse:
                    return False
        except OSError:
            return False
        return True

    def _resolve_directory(
        self,
        directory: Path,
        *,
        source: str,
        plugin_id: str | None = None,
    ) -> ResolvedSkill | None:
        """从确定目录解析一个技能，不跨根回退。"""
        if not self._directory_is_safe(directory):
            logger.warning("技能资源包含链接或不可读取，已排除: %s (%s)",
                           directory.name, source)
            return None
        resolved = directory.resolve()
        try:
            skill = parse_skill_md((resolved / "SKILL.md").read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            logger.warning("技能解析失败，已排除且不回退同名来源: %s (%s): %s",
                           directory.name, source, exc)
            return None
        return ResolvedSkill(
            name=skill["name"],
            description=skill["description"],
            body=skill["content"],
            directory=resolved,
            source=source,
            plugin_id=plugin_id,
        )

    def resolve_skills(self, user_id: str | None = None) -> list[ResolvedSkill]:
        """按覆盖优先级一次解析并绑定全部有效技能来源。

        Args:
            user_id: 当前用户；None 时不包含用户自建层。

        Returns:
            确定来源的技能列表。
        """
        candidates: dict[str, tuple[Path, str, str | None]] = {}

        def add_root(
            root: Path,
            source: str,
            plugin_id: str | None = None,
            names: frozenset[str] | None = None,
        ) -> None:
            if not root.is_dir():
                return
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or (names is not None and entry.name not in names):
                    continue
                candidates.setdefault(entry.name, (entry, source, plugin_id))

        if user_id:
            add_root(self.user_skills_dir(user_id), "user")
        add_root(self.skills_dir, "public")
        for name, directory in sorted(self._catalog_skills.items()):
            candidates.setdefault(name, (directory, "catalog", None))
        for root in self._extra_roots:
            plugin_id = self._plugin_roots.get(root)
            add_root(
                root,
                "plugin" if plugin_id else "catalog",
                plugin_id,
                self._root_names.get(root),
            )

        resolved: list[ResolvedSkill] = []
        for name, (directory, source, plugin_id) in candidates.items():
            item = self._resolve_directory(
                directory,
                source=source,
                plugin_id=plugin_id,
            )
            if item is not None and item.name == name:
                resolved.append(item)
            elif item is not None:
                logger.warning("技能目录名与 frontmatter 名称不一致，已排除: %s != %s",
                               name, item.name)
        return resolved

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
