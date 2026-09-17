"""后台任务提交权限快照、沙箱参数编译与工作区门禁。"""
from __future__ import annotations

import math
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synlys_harness import ExecutionRequest, ReadOnlyResource

from app.services.skill_service import ResolvedSkill


@dataclass(frozen=True)
class JobSubmissionScope:
    """从当前运行装配冻结的后台任务授权范围。"""

    allowed_tools: frozenset[str]
    allowed_plugins: frozenset[str]
    skills: tuple[ResolvedSkill, ...]
    resources: tuple[ReadOnlyResource, ...]
    workspace_root: Path
    ownership: dict[str, str]


class WorkspaceJobGuard:
    """序列化平台任务交接与工作区删除检查。"""

    def __init__(self, repo) -> None:
        """保存任务仓库并初始化工作区锁表。"""
        self._repo = repo
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _keys(user_id: str, workspace_root: Path, ownership: dict[str, str]) -> tuple[str, ...]:
        """生成稳定排序的工作区、会话和项目门禁键。"""
        keys = {f"workspace:{user_id}:{workspace_root.resolve()}"}
        for kind in ("session_id", "project_id"):
            value = ownership.get(kind)
            if value:
                keys.add(f"{kind}:{user_id}:{value}")
        return tuple(sorted(keys))

    @asynccontextmanager
    async def hold(
        self,
        user_id: str,
        workspace_root: Path,
        ownership: dict[str, str],
    ):
        """按稳定顺序持有某任务涉及的全部门禁锁。"""
        locks = [self._locks.setdefault(key, asyncio.Lock())
                 for key in self._keys(user_id, workspace_root, ownership)]
        for lock in locks:
            await lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    async def has_active(
        self,
        *,
        user_id: str,
        workspace_root: Path,
        ownership: dict[str, str],
    ) -> bool:
        """检查同一工作区、会话或项目是否有活跃平台任务。"""
        active = {"pending", "running"}
        target_root = str(workspace_root.resolve())
        for doc in await self._repo.list(filters={"user_id": user_id}):
            if doc.get("backend") != "sandbox" or doc.get("status") not in active:
                continue
            doc_owner = doc.get("workspace_owner") or {}
            if str(doc.get("workspace_root") or "") == target_root:
                return True
            if any(ownership.get(key) and ownership.get(key) == doc_owner.get(key)
                   for key in ("session_id", "project_id")):
                return True
        return False


class _BaseParams(BaseModel):
    """拒绝模型传入未声明的执行权限字段。"""

    model_config = ConfigDict(extra="forbid")
    cwd: str = "tmp"
    timeout_s: float | None = None


class _PythonParams(_BaseParams):
    """Python 后台任务参数。"""

    code: str = Field(min_length=1)


class _ShellParams(_BaseParams):
    """Shell 后台任务参数。"""

    command: str = Field(min_length=1)


class _SkillParams(_BaseParams):
    """技能脚本后台任务参数。"""

    skill: str = Field(min_length=1)
    script: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


def _timeout(params: _BaseParams, settings) -> float:
    """解析并校验后台任务独立超时。"""
    value = (
        float(params.timeout_s)
        if params.timeout_s is not None
        else float(settings.sandbox_job_default_timeout_s)
    )
    maximum = float(settings.sandbox_job_max_timeout_s)
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ValueError(f"timeout_s 必须是有限正数且不超过 {maximum:g} 秒")
    return value


def _validate_cwd(cwd: str) -> str:
    """校验工作区内相对 cwd。"""
    value = PurePosixPath(str(cwd).replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("cwd 必须是工作区内相对路径")
    return value.as_posix() or "."


def _skill_script(scope: JobSubmissionScope, name: str, script: str) -> tuple[str, str]:
    """将获准技能脚本编译为容器路径和解释器。"""
    relative = PurePosixPath(script)
    if (relative.is_absolute() or ".." in relative.parts
            or not relative.parts or relative.parts[0] != "scripts"):
        raise ValueError("script 必须是技能包 scripts/ 下的相对路径")
    skill = next((item for item in scope.skills if item.name == name), None)
    if skill is None:
        raise ValueError(f"技能本轮不可用: {name}")
    source = (skill.directory / Path(*relative.parts)).resolve()
    try:
        source.relative_to(skill.directory.resolve())
    except ValueError as exc:
        raise ValueError("技能脚本路径越界") from exc
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"技能脚本不存在或不可执行: {script}")
    suffix = source.suffix.lower()
    if suffix == ".py":
        if "python.run" not in scope.allowed_tools:
            raise ValueError("当前专家未授权 python.run，不能提交 Python 技能脚本")
        return "python", f"/skills/{name}/{relative.as_posix()}"
    if suffix == ".sh":
        if "shell.run" not in scope.allowed_tools:
            raise ValueError("当前专家未授权 shell.run，不能提交 Shell 技能脚本")
        return "bash", f"/skills/{name}/{relative.as_posix()}"
    raise ValueError("技能脚本仅支持 .py 或 .sh")


def prepare_sandbox_job(
    kind: str,
    params: dict,
    scope: JobSubmissionScope,
    settings,
) -> ExecutionRequest:
    """将平台后台任务参数编译为可信执行请求。

    Args:
        kind: sandbox.python、sandbox.shell 或 sandbox.skill。
        params: 模型提交的严格参数对象。
        scope: 当前运行冻结的权限与资源来源。
        settings: 后台任务默认和最大超时配置。

    Returns:
        尚未写 execution_id 的执行请求。

    Raises:
        ValueError: 参数、权限、路径或超时不合法。
    """
    try:
        if kind == "sandbox.python":
            parsed = _PythonParams.model_validate(params)
            if "python.run" not in scope.allowed_tools:
                raise ValueError("当前专家未授权 python.run")
            argv = ("python", "-I", "-X", "utf8", "-c", parsed.code)
        elif kind == "sandbox.shell":
            parsed = _ShellParams.model_validate(params)
            if "shell.run" not in scope.allowed_tools:
                raise ValueError("当前专家未授权 shell.run")
            argv = ("/bin/bash", "--noprofile", "--norc", "-c", parsed.command)
        elif kind == "sandbox.skill":
            parsed = _SkillParams.model_validate(params)
            interpreter, script_path = _skill_script(
                scope, parsed.skill, parsed.script
            )
            if interpreter == "python":
                argv = (
                    "python", "-I", "-X", "utf8", script_path, *parsed.args,
                )
            else:
                argv = (
                    "/bin/bash", "--noprofile", "--norc", script_path, *parsed.args,
                )
        else:
            raise ValueError(f"未知的平台任务类型: {kind}")
    except ValidationError as exc:
        raise ValueError(f"后台任务参数非法: {exc}") from exc

    return ExecutionRequest(
        argv=tuple(argv),
        workspace_root=scope.workspace_root.resolve(),
        cwd=_validate_cwd(parsed.cwd),
        resources=scope.resources,
        timeout_s=_timeout(parsed, settings),
        max_output_bytes=65_536,
    )
