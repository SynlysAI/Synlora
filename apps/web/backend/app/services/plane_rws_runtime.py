"""Plane Research Workspace 短期运行绑定适配层。"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from synlys_harness import ExecutionRequest, ReadOnlyResource


class PlaneRWSRuntimeError(RuntimeError):
    """Plane/Runner 绑定失效或执行失败；调用方不得回落个人工作区。"""


class PlaneRWSRuntime:
    """用短期 grant 驱动一次与 Plane 共享的 Synlora 沙箱执行。"""

    def __init__(
        self,
        settings: Any,
        store: Any,
        executor_resolver: Callable[[], Awaitable[Any]],
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        """保存依赖；不在构造期发起网络请求或创建执行器。

        Args:
            settings: Synlora 全局配置。
            store: 文档存储，用于保存不含 token 的运行回执投影。
            executor_resolver: 返回当前受控代码执行器的异步函数。
            http_client_factory: 测试注入的 HTTP 客户端工厂。
        """
        self._settings = settings
        self._store = store
        self._executor_resolver = executor_resolver
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    async def execute(
        self,
        *,
        run_id: str,
        token: str,
        argv: list[str],
        cwd: str = ".",
        timeout_s: float = 1800.0,
        changed_paths: list[str] | None = None,
        outputs: dict | None = None,
        request_id: str = "",
    ) -> dict:
        """执行一个已由 Plane 签发的 agent run。

        Args:
            run_id: Plane WorkspaceAgentRun 标识。
            token: 短期 bearer token，仅在本方法内存中传递。
            argv: 受信服务方提供的固定 argv，不来自模型自由文本。
            cwd: WorkingCopy 内的相对工作目录。
            timeout_s: 执行超时。
            changed_paths: 需要进入候选提交的显式路径。
            outputs: 附加到 run receipt 的输出索引。
            request_id: Plane finalize 幂等键。

        Returns:
            不含 token 的执行与回执投影。
        """
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise PlaneRWSRuntimeError("argv 必须是非空字符串数组")
        changed_paths = list(changed_paths or [])
        outputs = dict(outputs or {})
        request_id = request_id or f"synlora-{run_id}-{int(time.time())}"
        async with self._http_client_factory(timeout=self._settings.plane_request_timeout_seconds) as client:
            binding = await self._verify_plane_binding(client, run_id, token)
            runner_binding = await self._resolve_runner_binding(client, binding, token)
            assets_path = await self._materialize_assets(
                client, runner_binding, binding.get("asset_items") or [], token
            )
            executor = await self._executor_resolver()
            if str(getattr(executor, "sandbox", "")) != "docker":
                raise PlaneRWSRuntimeError("Plane RWS 运行必须使用 Docker 沙箱")
            request = ExecutionRequest(
                argv=tuple(argv),
                workspace_root=Path(str(runner_binding["workspace_path"])),
                cwd=cwd,
                resources=(ReadOnlyResource(Path(assets_path), PurePosixPath("/assets")),),
                timeout_s=float(timeout_s),
                execution_id=f"rws-{run_id}",
            )
            result = await executor.execute(request)
            finalize = await self._finalize_plane_run(
                client, run_id, token, request_id, 0 if result.ok else 1, changed_paths, outputs
            )
        projection = {
            "run_id": run_id,
            "study_id": binding.get("study_id", ""),
            "working_copy_id": binding.get("working_copy_id", ""),
            "grant_id": binding.get("grant_id", ""),
            "branch": binding.get("branch", ""),
            "ok": bool(result.ok),
            "content": result.content,
            "changed_paths": changed_paths,
            "outputs": outputs,
            "plane_receipt": finalize,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        try:
            await self._store.insert("plane_rws_runs", {"_id": run_id, **projection})
        except ValueError:
            await self._store.update("plane_rws_runs", run_id, projection)
        return projection

    async def _verify_plane_binding(self, client: httpx.AsyncClient, run_id: str, token: str) -> dict:
        """向 Plane 复验 binding，绝不信任请求方传入的绑定对象。"""
        base = str(self._settings.plane_rws_api_url or "").rstrip("/")
        if not base:
            raise PlaneRWSRuntimeError("Plane RWS API 未配置")
        response = await client.post(
            f"{base}/api/research/rws/agent-runs/{run_id}/verify/",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            raise PlaneRWSRuntimeError("Plane run binding 失效")
        binding = response.json().get("data") or {}
        if binding.get("run_id") != run_id or "run.execute" not in binding.get("capabilities", []):
            raise PlaneRWSRuntimeError("Plane run capability 不匹配")
        return binding

    async def _runner_request(self, client: httpx.AsyncClient, action: str, payload: dict) -> dict:
        """调用固定 Runner action，并要求服务级 bearer 认证。"""
        base = str(self._settings.plane_rws_runner_url or "").rstrip("/")
        service_token = str(self._settings.plane_rws_runner_token or "")
        if not base or not service_token:
            raise PlaneRWSRuntimeError("Workspace Runner 未配置")
        response = await client.post(
            f"{base}/{action}",
            json=payload,
            headers={"Authorization": f"Bearer {service_token}"},
        )
        body = response.json()
        if response.status_code != 200 or not body.get("ok"):
            raise PlaneRWSRuntimeError(body.get("message") or "Workspace Runner 拒绝执行")
        return body.get("data") or {}

    async def _resolve_runner_binding(self, client: httpx.AsyncClient, binding: dict, token: str) -> dict:
        """解析当前授权的物理 WorkingCopy 挂载路径。"""
        result = await self._runner_request(
            client, "binding.resolve", {"grant_id": binding["grant_id"], "token": token}
        )
        if not result.get("workspace_path"):
            raise PlaneRWSRuntimeError("Runner 未返回授权工作区")
        return result

    async def _materialize_assets(
        self, client: httpx.AsyncClient, runner_binding: dict, items: list[dict], token: str
    ) -> str:
        """按需下载并二次校验只读资产，失败即中止运行。"""
        result = await self._runner_request(
            client,
            "assets.materialize",
            {"grant_id": runner_binding["grant_id"], "token": token, "items": items},
        )
        if not result.get("assets_path"):
            raise PlaneRWSRuntimeError("Runner 资产物化失败")
        return str(result["assets_path"])

    async def _finalize_plane_run(
        self,
        client: httpx.AsyncClient,
        run_id: str,
        token: str,
        request_id: str,
        exit_status: int,
        changed_paths: list[str],
        outputs: dict,
    ) -> dict:
        """把委托人、输出与候选改动回传 Plane。"""
        base = str(self._settings.plane_rws_api_url or "").rstrip("/")
        response = await client.post(
            f"{base}/api/research/rws/agent-runs/{run_id}/finalize/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": request_id,
                "exit_status": exit_status,
                "changed_paths": changed_paths or ["outputs/synlora-result.txt"],
                "outputs": outputs,
            },
        )
        if response.status_code != 200:
            raise PlaneRWSRuntimeError("Plane run receipt 保存失败")
        return response.json().get("data") or {}
