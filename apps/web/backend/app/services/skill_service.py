"""技能：磁盘 SKILL.md 的扫描/解析/写入（不入库）。

字段与正文骨架遵循 jiuwen `skill-spec.md`：frontmatter 必填
`name`（= 目录名，kebab-case）/ `description`（做什么 + 何时用），可选
`version` / `author` / `tags` / `allowed_tools`；正文骨架
`# 标题 → ## 目标 → ## 工作流 → ## 决策规则 → ## 输出要求`。

frontmatter 刻意不声明 `tools`：权限由系统分配（jiuwen 明确禁止）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

NAME_OK = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
BUILTIN_SKILL_NAMES = frozenset({"data-analysis", "pdf-extraction", "office-doc"})
# 内置技能源目录（随仓库内容：apps/web/backend/catalog/skills）
CATALOG_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "catalog" / "skills"


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


class SkillService:
    """{data_root}/skills 下的技能读写与扫描。"""

    def __init__(self, data_root: Path, extra_roots: list[Path] | None = None) -> None:
        """保存数据根与额外技能根。

        Args:
            data_root: 应用数据根目录。
            extra_roots: 额外技能根（插件包的 skills/ 目录；同名时用户目录优先）。
        """
        self._data_root = data_root
        # 规范化额外技能根，避免同一目录的不同写法被重复扫描（口径与 add_root 一致）
        self._extra_roots: list[Path] = [Path(r).resolve() for r in (extra_roots or [])]

    @property
    def skills_dir(self) -> Path:
        """技能根目录（自动创建）。

        Returns:
            {data_root}/skills 路径，不存在时已创建。
        """
        d = self._data_root / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_root(self, root: Path) -> None:
        """追加一个额外技能根（插件安装时调用；重复追加幂等）。

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
            builtin: 该根的技能是否标记为内置（插件技能为 True，不可删）。

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
            skill["builtin"] = builtin or skill["name"] in BUILTIN_SKILL_NAMES
            out.append(skill)
        return out

    def list_skills(self) -> list[dict]:
        """扫描全部技能（用户目录 + 额外根；同名用户目录优先）。

        每个根内按目录名排序；单个技能解析失败（缺 frontmatter / 名不合法 /
        YAML 语法错误）只跳过该目录，不影响其余技能。

        Returns:
            技能字典列表，每项含 `builtin` 标记。
        """
        out = self._scan_root(self.skills_dir, builtin=False)
        seen = {s["name"] for s in out}
        for root in self._extra_roots:
            for skill in self._scan_root(root, builtin=True):
                if skill["name"] not in seen:
                    seen.add(skill["name"])
                    out.append(skill)
        return out

    def read_body(self, name: str) -> str | None:
        """读技能正文（不含 frontmatter；用户目录优先于额外根）。

        Args:
            name: 技能名（即目录名）。

        Returns:
            正文文本；名字非法、技能不存在或不可解析时返回 None。
        """
        if not NAME_OK.match(name):
            return None
        for root in [self.skills_dir, *self._extra_roots]:
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
            写入后的技能字典（含 `builtin` 标记）。

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
        return {**skill, "builtin": name in BUILTIN_SKILL_NAMES}

    def delete_skill(self, name: str) -> bool:
        """删除技能目录。

        Args:
            name: 技能名（即目录名）。

        Returns:
            目录存在并已删除返回 True；名字非法或目录不存在返回 False。

        Raises:
            ValueError: 技能名属于内置技能（不可删除）。
        """
        if not NAME_OK.match(name):
            return False
        target = self.skills_dir / name
        if not target.is_dir():
            return False
        if name in BUILTIN_SKILL_NAMES:
            raise ValueError("内置技能不可删除")
        shutil.rmtree(target)
        return True

    def seed_builtins(self) -> None:
        """把随仓库内容的内置技能拷到用户技能目录（幂等，不覆盖已有目录）。"""
        if not CATALOG_SKILLS_ROOT.is_dir():
            return
        for src in CATALOG_SKILLS_ROOT.iterdir():
            dst = self.skills_dir / src.name
            if not dst.exists():
                shutil.copytree(src, dst)
