"""单轮技能资源与执行环境装配。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable

from synlys_harness import ReadOnlyResource, ToolResult

from app.services.skill_service import ResolvedSkill

MAX_RESOURCE_CHARS = 48_000


@dataclass(frozen=True)
class PreparedSkills:
    """本轮确定来源的技能及其执行路径。"""

    items: tuple[ResolvedSkill, ...]
    resources: tuple[ReadOnlyResource, ...]
    resource_roots: dict[str, str]


def select_runtime_skills(
    skills: list[ResolvedSkill],
    *,
    hidden_names: set[str],
    active_plugin_ids: set[str],
    requested_names: list[str] | None,
) -> list[ResolvedSkill]:
    """按能力策略、会话插件和请求选择筛选技能。

    Args:
        skills: 当前用户所有已解析技能。
        hidden_names: 能力策略排除的技能名。
        active_plugin_ids: 用户可见集与会话开关的插件交集。
        requested_names: 请求指定技能；None/空表示全部当前可用技能。

    Returns:
        保持原顺序的本轮技能列表。
    """
    requested = set(requested_names or ())
    return [
        skill for skill in skills
        if skill.name not in hidden_names
        and (skill.plugin_id is None or skill.plugin_id in active_plugin_ids)
        and (not requested or skill.name in requested)
    ]


def select_runtime_tools(
    registry_names: list[str],
    *,
    whitelist: list[str],
    sandbox: str,
    all_plugin_tools: set[str],
    visible_plugin_tools: set[str],
) -> list[str]:
    """按专家白名单、执行器能力和插件授权筛选工具。"""
    selected = list(dict.fromkeys(whitelist)) if whitelist else list(registry_names)
    if sandbox != "docker":
        selected = [name for name in selected if name != "shell.run"]
    return [
        name for name in selected
        if name not in all_plugin_tools or name in visible_plugin_tools
    ]


def prepare_skills(
    skills: list[ResolvedSkill],
    *,
    sandbox: str,
) -> PreparedSkills:
    """将技能目录映射为当前执行器可访问的资源。

    Args:
        skills: 已完成权限筛选且来源固定的技能。
        sandbox: 当前执行器标记。

    Returns:
        本轮技能、只读资源及模型可用路径。
    """
    items = tuple(skills)
    if sandbox == "docker":
        resources = tuple(
            ReadOnlyResource(
                source=item.directory.resolve(),
                target=PurePosixPath(f"/skills/{item.name}"),
            )
            for item in items
        )
        roots = {item.name: f"/skills/{item.name}" for item in items}
    else:
        resources = ()
        roots = {item.name: str(item.directory.resolve()) for item in items}
    return PreparedSkills(items=items, resources=resources, resource_roots=roots)


def _safe_resource_path(directory: Path, path: str) -> Path | None:
    """将包内相对路径解析为不能逃逸的真实路径。"""
    if not isinstance(path, str) or not path or "\\" in path:
        return None
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = directory.resolve()
    target = (root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def make_skill_resource_reader(
    skills: tuple[ResolvedSkill, ...],
) -> Callable[[str, str], Awaitable[ToolResult]]:
    """创建只持有本轮技能目录映射的文本读取回调。

    Args:
        skills: 本轮允许的确定来源技能。

    Returns:
        异步文本资源读取函数。
    """
    directories = {item.name: item.directory.resolve() for item in skills}

    async def read(name: str, path: str) -> ToolResult:
        directory = directories.get(name)
        if directory is None:
            return ToolResult(
                ok=False,
                content=f"技能不存在或本轮不可用: {name}",
                error="skill_not_available",
            )
        target = _safe_resource_path(directory, path)
        if target is None:
            return ToolResult(
                ok=False,
                content="技能资源路径非法",
                error="skill_path_invalid",
            )
        if not target.is_file() or target.is_symlink():
            return ToolResult(
                ok=False,
                content=f"技能资源不存在: {path}",
                error="skill_resource_not_found",
            )
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return ToolResult(
                ok=False,
                content=f"读取技能资源失败: {exc}",
                error="skill_resource_read_failed",
            )
        if b"\x00" in raw:
            return ToolResult(
                ok=False,
                content="技能资源是二进制文件，不能直接读取",
                error="skill_resource_binary",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                content="技能资源不是 UTF-8 文本",
                error="skill_resource_binary",
            )
        truncated = len(text) > MAX_RESOURCE_CHARS
        return ToolResult(
            ok=True,
            content=text[:MAX_RESOURCE_CHARS],
            truncated=truncated,
            data={"path": path, "truncated": truncated},
        )

    return read
