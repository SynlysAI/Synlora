"""受限 Python 代码执行器（python.run 的实现核心）。

执行器抽象（Pi BashOperations 模式）：宿主按部署形态解析一次执行器，
经 ctx.extra.code_executor 注入；缺省回落本机子进程（开发默认）。

sandbox 标记（ToolResult.data["sandbox"]，随事件可观测）：
- local：本机子进程（-I 隔离，事故围栏，非安全边界）
- docker：临时容器强隔离（多用户部署形态）
- local-weak：请求 docker 但不可用时的降级回退（backlog 口径，日志告警）
- unavailable：strict 模式下 docker 不可用，fail-closed 拒绝执行
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Protocol

from ..types import ToolResult

ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_OUTPUT_BYTES = 65_536


def _format_output(
    raw: bytes,
    *,
    timed_out: bool,
    timeout_s: float,
    exit_code: int | None,
    max_output_bytes: int,
    sandbox: str,
    extra_data: dict | None = None,
) -> ToolResult:
    """统一输出装配：utf-8 解码 + 截断 + 超时/正常两态 ToolResult。

    按字符数截断是保守界：UTF-8 下字符数 ≤ 字节数，故字符截断不超过
    字节上限；截断标志按原始字节判定。

    Args:
        raw: 合并后的 stdout+stderr 原始字节。
        timed_out: 是否超时被杀。
        timeout_s: 超时秒数（报错文案用）。
        exit_code: 进程/容器退出码（超时路径可能拿不到，可为 None）。
        max_output_bytes: 输出字节上限。
        sandbox: 执行器标记（写入 data 供事件观测）。
        extra_data: 追加进 data 的执行器私有字段。

    Returns:
        装配好的 ToolResult。
    """
    text = raw.decode("utf-8", errors="replace")
    truncated = len(raw) > max_output_bytes
    if truncated:
        text = text[:max_output_bytes]
    data = {"exit_code": exit_code, "timed_out": timed_out, "sandbox": sandbox, **(extra_data or {})}
    if timed_out:
        return ToolResult(
            ok=False,
            content=f"执行超时（>{timeout_s}s），进程已终止。\n部分输出:\n{text}",
            error="timeout",
            data=data,
        )
    return ToolResult(
        ok=exit_code == 0,
        content=text or "(无输出)",
        truncated=truncated,
        data=data,
    )


class CodeExecutor(Protocol):
    """代码执行器接口：run_python 工具的唯一执行 seam。"""

    sandbox: str

    async def run(
        self,
        code: str,
        cwd: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ToolResult:
        """执行 Python 源码并返回受限结果。

        Args:
            code: 要执行的 Python 源码。
            cwd: 工作目录（调用方保证已限制在用户工作区内）。
            timeout_s: 超时秒数。
            max_output_bytes: 输出字节上限。

        Returns:
            ToolResult：content 为合并输出；data 含 exit_code/timed_out/sandbox。
        """
        ...  # pragma: no cover


class LocalCodeExecutor:
    """本机子进程执行（-I 隔离模式 + 环境白名单 + 超时杀进程）。

    事故围栏而非安全边界：多用户/公网部署应使用 DockerCodeExecutor。
    """

    def __init__(self, label: str = "local") -> None:
        """初始化执行器。

        Args:
            label: sandbox 标记（"local" 或降级时的 "local-weak"）。
        """
        self.sandbox = label

    async def run(
        self,
        code: str,
        cwd: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ToolResult:
        """在受限子进程中执行 Python 代码。

        限制：解释器 -I（隔离模式，忽略用户 site 与环境变量）+ -X utf8
        （stdio 强制 UTF-8，修复 zh-CN Windows 管道 cp936 乱码）；环境变量
        白名单；cwd 锁定；stdout+stderr 合并截断；内部超时与外部取消都会
        杀死子进程。

        Args:
            code: 要执行的 Python 源码。
            cwd: 工作目录（调用方保证已限制在用户工作区内）。
            timeout_s: 超时秒数。
            max_output_bytes: 输出字节上限。

        Returns:
            ToolResult：content 为合并输出；data 含 exit_code/timed_out/sandbox。
        """
        env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-X", "utf8", "-c", code,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except OSError as exc:
            return ToolResult(
                ok=False, content=f"无法启动沙箱进程: {exc}", error="spawn_failed",
                data={"sandbox": self.sandbox},
            )

        timed_out = False
        try:
            try:
                async with asyncio.timeout(timeout_s):
                    raw, _ = await proc.communicate()
            except TimeoutError:
                timed_out = True
                proc.kill()
                raw, _ = await proc.communicate()
        except asyncio.CancelledError:
            # 管线超时/外部取消路径：TimeoutError 分支管不到这里，
            # 不主动 kill 会泄漏一个 100% CPU 的孤儿进程。
            proc.kill()
            try:
                await asyncio.shield(proc.communicate())
            except asyncio.CancelledError:
                pass
            raise

        return _format_output(
            raw, timed_out=timed_out, timeout_s=timeout_s,
            exit_code=proc.returncode, max_output_bytes=max_output_bytes,
            sandbox=self.sandbox,
        )


class DockerCodeExecutor:
    """Docker 容器执行（多用户部署的强隔离）。

    每次执行一个临时容器：workspace 单目录挂载 /workspace（rw，其余
    文件系统为容器私有层）、断网（network_disabled）、内存/CPU/进程数
    限额、镜像默认非 root 用户、跑完即删。代码经挂载的工作区传入，
    不进 argv（无长度与转义问题）、不经 stdin（无管道时序问题）。

    docker-py 为可选依赖，懒加载：local 模式下零开销。
    """

    def __init__(
        self,
        image: str,
        mem_limit: str = "512m",
        cpus: float = 1.0,
        pids_limit: int = 256,
        container_user: str = "",
    ) -> None:
        """初始化容器执行器。

        Args:
            image: 沙箱镜像（需预构建，见仓库 docker/sandbox/）。
            mem_limit: 单容器内存上限（Docker 记法，如 "512m"）。
            cpus: 单容器 CPU 上限（核数）。
            pids_limit: 单容器进程数上限（fork 炸弹围栏）。
            container_user: 容器内运行用户（空 = 镜像默认，镜像内置非 root）。
        """
        self.sandbox = "docker"
        self.image = image
        self._mem_limit = mem_limit
        self._nano_cpus = int(cpus * 1e9)
        self._pids_limit = pids_limit
        self._user = container_user or None
        self._client = None

    def _docker_client(self):
        """懒加载 docker 客户端（long timeout：wait 长阻塞由 asyncio 侧控时）。"""
        if self._client is None:
            import docker  # 可选依赖：仅 docker 模式需要

            self._client = docker.from_env(timeout=300)
        return self._client

    def probe(self) -> tuple[bool, str]:
        """探测执行环境是否可用（daemon 可达 + 镜像已构建）。

        Returns:
            (可用, 不可用原因描述)；可用时原因为空串。
        """
        try:
            client = self._docker_client()
        except Exception as exc:  # ImportError（未装 docker 包）或连接失败
            return False, f"Docker 客户端不可用: {exc}"
        try:
            client.ping()
        except Exception as exc:
            return False, f"Docker daemon 不可达: {exc}"
        try:
            client.images.get(self.image)
        except Exception:
            return False, f"镜像不存在: {self.image}（先构建：docker build -t {self.image} docker/sandbox/）"
        return True, ""

    def _create_container(self, name: str, script_path: str, working_dir: str, host_workspace: Path):
        """同步创建并启动执行容器（经 asyncio.to_thread 调用）。

        Args:
            name: 容器名（synlora-exec-<uuid>，运维可按前缀清理）。
            script_path: 容器内脚本绝对路径（/workspace/... 下）。
            working_dir: 容器内工作目录（与本地 tmp/ 布局对齐）。
            host_workspace: 宿主工作区绝对路径（单目录挂载源）。

        Returns:
            已启动的 container 对象。
        """
        bind_source = str(host_workspace)
        if sys.platform == "win32":
            # Docker Desktop 接受正斜杠形式的盘符路径（C:/a/b），
            # 反斜杠在部分版本会被当作转义
            bind_source = bind_source.replace("\\", "/")
        container = self._docker_client().containers.create(
            image=self.image,
            command=["python", "-I", "-X", "utf8", script_path],
            name=name,
            working_dir=working_dir,
            volumes={bind_source: {"bind": "/workspace", "mode": "rw"}},
            network_disabled=True,
            mem_limit=self._mem_limit,
            nano_cpus=self._nano_cpus,
            pids_limit=self._pids_limit,
            user=self._user,
            labels={"synlora.sandbox": "python-run"},
        )
        container.start()
        return container

    async def _cleanup(self, container, script: Path) -> None:
        """容器与脚本的终态清理（kill 收尸 + 强制删除）。

        Args:
            container: 容器对象（可能已退出）。
            script: 宿主侧临时脚本路径。
        """
        def _sync() -> None:
            try:
                container.remove(force=True)  # force：对运行中容器等效 kill+删
            except Exception:
                pass  # 容器已被守护进程回收（如 daemon 重启）时忽略

        try:
            await asyncio.shield(asyncio.to_thread(_sync))
        except asyncio.CancelledError:
            # 取消路径的清理本身不可再取消，尽最大努力后放行
            pass
        script.unlink(missing_ok=True)

    async def run(
        self,
        code: str,
        cwd: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ToolResult:
        """在临时容器中执行 Python 代码。

        语义与 LocalCodeExecutor 对齐：cwd 通常是 workspace/tmp，工作区根
        是其父目录（容器内同为 /workspace/tmp，../ 相对路径约定不变）。
        timeout_s 只覆盖容器内执行段（创建/启动开销不计入）。

        Args:
            code: 要执行的 Python 源码。
            cwd: 宿主侧工作目录（须位于 workspace 内）。
            timeout_s: 执行超时秒数。
            max_output_bytes: 输出字节上限。

        Returns:
            ToolResult：data 额外携带 container 名便于排查。
        """
        cwd_path = Path(cwd).resolve()
        workspace = cwd_path.parent
        cwd_path.mkdir(parents=True, exist_ok=True)
        rel = cwd_path.relative_to(workspace)
        working_dir = "/workspace" if str(rel) == "." else f"/workspace/{rel.as_posix()}"
        script_rel = f".exec-{uuid.uuid4().hex[:12]}.py"
        script = cwd_path / script_rel
        script.write_text(code, encoding="utf-8")
        name = f"synlora-exec-{uuid.uuid4().hex[:12]}"

        try:
            container = await asyncio.to_thread(
                self._create_container, name, f"{working_dir}/{script_rel}",
                working_dir, workspace,
            )
        except Exception as exc:
            script.unlink(missing_ok=True)
            return ToolResult(
                ok=False, content=f"沙箱容器启动失败: {exc}", error="spawn_failed",
                data={"sandbox": self.sandbox, "container": name},
            )

        timed_out = False
        exit_code: int | None = None
        raw = b""
        try:
            try:
                # wait 返回 {"StatusCode": n}；超时由 asyncio 侧控制
                result = await asyncio.wait_for(asyncio.to_thread(container.wait), timeout_s)
                exit_code = int((result or {}).get("StatusCode", -1))
            except TimeoutError:
                timed_out = True
                await asyncio.to_thread(container.kill)
                try:
                    result = await asyncio.wait_for(asyncio.to_thread(container.wait), timeout=15)
                    exit_code = int((result or {}).get("StatusCode", -1))
                except Exception:  # 收尸失败不掩盖超时事实
                    exit_code = -1
                raw = await self._safe_logs(container)
        except asyncio.CancelledError:
            await self._cleanup(container, script)
            raise

        try:
            if not timed_out:
                raw = await asyncio.to_thread(
                    container.logs, stdout=True, stderr=True)
        finally:
            await self._cleanup(container, script)

        return _format_output(
            raw, timed_out=timed_out, timeout_s=timeout_s, exit_code=exit_code,
            max_output_bytes=max_output_bytes, sandbox=self.sandbox,
            extra_data={"container": name},
        )

    async def _safe_logs(self, container) -> bytes:
        """尽力读取容器日志（超时被杀路径的输出可能不完整，失败返回空）。"""
        try:
            return await asyncio.to_thread(container.logs, stdout=True, stderr=True)
        except Exception:
            return b""


class FailingExecutor:
    """strict 模式下沙箱不可用：fail-closed，每次调用明确拒绝执行。"""

    def __init__(self, reason: str) -> None:
        """初始化。

        Args:
            reason: 沙箱不可用原因（探测得到，透传给调用方）。
        """
        self.sandbox = "unavailable"
        self.reason = reason

    async def run(
        self,
        code: str,
        cwd: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ToolResult:
        """拒绝执行（绝不回落本机）。

        Args:
            code: 要执行的 Python 源码（不执行）。
            cwd: 工作目录（不使用）。
            timeout_s: 未使用。
            max_output_bytes: 未使用。

        Returns:
            ok=False 的 ToolResult（error=sandbox_unavailable）。
        """
        return ToolResult(
            ok=False,
            content=f"沙箱不可用，strict 模式已拒绝执行: {self.reason}",
            error="sandbox_unavailable",
            data={"sandbox": self.sandbox, "reason": self.reason},
        )


DEFAULT_LOCAL_EXECUTOR = LocalCodeExecutor()


async def run_python(
    code: str,
    cwd: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ToolResult:
    """兼容入口：默认本机执行器执行（测试与独立调用）。

    Args:
        code: 要执行的 Python 源码。
        cwd: 工作目录。
        timeout_s: 超时秒数。
        max_output_bytes: 输出字节上限。

    Returns:
        ToolResult。
    """
    return await DEFAULT_LOCAL_EXECUTOR.run(
        code, cwd, timeout_s=timeout_s, max_output_bytes=max_output_bytes,
    )


def resolve_executor(
    mode: str = "local",
    *,
    image: str = "synlora-sandbox:latest",
    strict: bool = False,
    mem_limit: str = "512m",
    cpus: float = 1.0,
    pids_limit: int = 256,
    container_user: str = "",
) -> tuple[CodeExecutor, str]:
    """按部署配置解析执行器（含探测，阻塞调用：宿主启动时经 to_thread 调一次）。

    决策表：
    - mode=local → LocalCodeExecutor（开发默认）
    - mode=docker 且探测通过 → DockerCodeExecutor
    - mode=docker 探测失败 + strict=False → LocalCodeExecutor("local-weak")（降级回退，backlog 口径）
    - mode=docker 探测失败 + strict=True → FailingExecutor（fail-closed，绝不落到本机）

    Args:
        mode: local | docker。
        image: docker 模式镜像名。
        strict: docker 不可用时是否拒绝执行而非回退本机。
        mem_limit: 单容器内存上限。
        cpus: 单容器 CPU 上限。
        pids_limit: 单容器进程数上限。
        container_user: 容器内运行用户（空 = 镜像默认）。

    Returns:
        (executor, note)：note 为人读状态行（含降级原因），宿主用于日志。
    """
    if mode != "docker":
        return LocalCodeExecutor(), "local"
    docker_exec = DockerCodeExecutor(
        image=image, mem_limit=mem_limit, cpus=cpus,
        pids_limit=pids_limit, container_user=container_user,
    )
    ok, reason = docker_exec.probe()
    if ok:
        return docker_exec, "docker"
    if strict:
        return FailingExecutor(reason), f"unavailable（strict，{reason}）"
    return LocalCodeExecutor(label="local-weak"), f"local-weak（docker 模式不可用已回退本机，{reason}）"
