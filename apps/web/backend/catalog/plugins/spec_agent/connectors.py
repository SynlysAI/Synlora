"""Spec_Agent 谱图解析异步任务的连接器（插件包自带，宿主动态加载）。

覆盖上游 5 种任务：NMR / IR / GPC / Raman / LC-MS。上游约束（2026-09-16 实测）：
- 提交**只接受 file_id**（`input_type="file_id"`），故必须先上传谱图文件；
- 状态枚举 `PENDING/QUEUED/RUNNING/SUCCESS/FAILED/CANCELED`（单 L），
  由本模块的 `SPEC_STATUS_MAP` 翻译成 harness 的统一状态；
- 结果走独立端点 `/tasks/{id}/result`，`result` 是自由 JSON。

配置与凭证沿用宿主通用通道：`ctx["config"]`（插件配置）+ `ctx["ai4ms_token"]`
（按登录用户代签）；`ctx["workspace_root"]` 是用户工作区根，谱图文件从这里读。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from synlys_harness import JobStatus

# 宿主定义的连接器异常：插件必须用**同一个类**（宿主按类型捕获：JobPollFailed
# 保持原状态下轮重试、JobSubmitFailed 判失败），另定义同名类就捕获不到了。
from app.services.job_connectors import JobPollFailed, JobSubmitFailed

PLUGIN_ID = "spec_agent"

CONNECT_TIMEOUT_S = 10.0   # 连接超时（宿主不可达时快速失败）
REQUEST_TIMEOUT_S = 60.0   # 读超时（上传/提交/查状态都是秒级操作）
UPLOAD_TIMEOUT_S = 120.0   # 上传单独放宽（谱图文件可能较大）

# 上游状态原文（小写）→ 统一状态；上游用的是 SUCCESS/CANCELED，与 harness 不同名
SPEC_STATUS_MAP: dict[str, JobStatus] = {
    "pending": JobStatus.PENDING,
    "queued": JobStatus.PENDING,
    "running": JobStatus.RUNNING,
    "success": JobStatus.COMPLETED,
    "failed": JobStatus.FAILED,
    "canceled": JobStatus.CANCELLED,
    "cancelled": JobStatus.CANCELLED,   # 兼容双 L 拼写（上游实测为单 L）
}

# 测试可经 monkeypatch 注入 MockTransport（None = 走真实网络）
_transport: httpx.BaseTransport | None = None


class SpectraTaskConnector:
    """一种谱图解析任务的连接器（5 种任务各一个实例）。

    Attributes:
        kind: 本平台的任务类型（如 spec.task.nmr）。
        endpoint: 上游 URL 路径段（nmr/ir/gpc/raman/lcms）——决定实际行为。
        status_map: 上游状态原文 → 统一状态。
    """

    plugin_id = PLUGIN_ID

    def __init__(self, kind: str, endpoint: str) -> None:
        """初始化。

        Args:
            kind: 本平台任务类型。
            endpoint: 上游 URL 路径段。
        """
        self.kind = kind
        self.endpoint = endpoint
        self.status_map = dict(SPEC_STATUS_MAP)

    # ---------- 连接器协议 ----------

    async def submit(self, params: dict, ctx: dict) -> str:
        """读工作区文件 → 上传换 file_id → 提交任务，返回上游 task_id。

        Args:
            params: 工具传入的参数，需含 `path`（工作区内的谱图文件相对路径）；
                可选 `params`（透传给上游的任务参数对象）。
            ctx: 宿主填充的上下文（config/ai4ms_token/workspace_root）。

        Returns:
            上游 task_id。

        Raises:
            JobSubmitFailed: 未配置服务地址、缺 path、路径非法、文件不存在，
                或上游拒绝（含响应体摘要）。
        """
        base_url, headers = self._conn(ctx)
        target = self._locate(ctx, params)
        async with self._client(UPLOAD_TIMEOUT_S) as client:
            file_id = await self._upload(client, base_url, headers, target)
            body = {
                "input": {"input_type": "file_id", "file_id": file_id},
                "params": params.get("params") or {},
            }
            try:
                resp = await client.post(
                    f"{base_url}/api/v1/tasks/{self.endpoint}", headers=headers,
                    json=body)
            except httpx.TimeoutException as exc:
                raise JobSubmitFailed(f"提交任务超时: {exc}") from exc
            except httpx.HTTPError as exc:
                raise JobSubmitFailed(f"提交任务失败: {exc}") from exc
        data = self._unwrap(resp, "提交任务")
        task_id = str((data or {}).get("task_id") or "")
        if not task_id:
            raise JobSubmitFailed(f"上游未返回 task_id: {str(data)[:200]}")
        return task_id

    async def poll(self, external_id: str, ctx: dict) -> str:
        """查询任务状态原文。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            上游状态原文（如 "RUNNING"）。

        Raises:
            JobPollFailed: 查询失败（网络异常、上游 4xx/5xx、非 JSON 或
                code != 0、未配置服务地址，以及 200 但响应缺 status）。一律以
                上述异常抛出——若静默返回空串，宿主会当成"未映射状态"而保持
                原状态且不记失败计数，任务会永久挂起、用户与日志都无感知。
        """
        try:
            base_url, headers = self._conn(ctx)
            async with self._client(REQUEST_TIMEOUT_S) as client:
                resp = await client.get(
                    f"{base_url}/api/v1/tasks/{external_id}", headers=headers)
            data = self._unwrap(resp, "查询任务状态")
            raw = str((data or {}).get("status") or "")
            if not raw:
                raise JobPollFailed(
                    f"上游未返回任务状态: {str(data)[:200]}")
            return raw
        except JobPollFailed:
            raise
        except JobSubmitFailed as exc:
            # _conn 的配置缺失与 _unwrap 的通用失败都抛 JobSubmitFailed，
            # 在查询路径一律转成 JobPollFailed（宿主对两者的处理不同）
            raise JobPollFailed(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise JobPollFailed(f"查询任务状态失败: {exc}") from exc

    async def fetch_result(self, external_id: str, ctx: dict) -> str:
        """取任务结果并序列化为文本（仅成功终态由宿主调用）。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            结果 JSON 文本；上游无结果时返回空串。
        """
        base_url, headers = self._conn(ctx)
        async with self._client(REQUEST_TIMEOUT_S) as client:
            try:
                resp = await client.get(
                    f"{base_url}/api/v1/tasks/{external_id}/result", headers=headers)
            except httpx.HTTPError:
                return ""  # 取结果失败不阻断状态流转（宿主只记日志）
        data = self._unwrap(resp, "取任务结果", raise_on_error=False) or {}
        result = data.get("result")
        if result is None:
            return ""
        return json.dumps(result, ensure_ascii=False)

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """取消任务。

        Args:
            external_id: 上游 task_id。
            ctx: 连接器上下文。

        Returns:
            恒为 False：上游 v1 未提供取消接口，本平台的"取消"是本地收敛
            （任务标记为 cancelled、停止轮询）；上游任务会自行跑完。
        """
        return False

    # ---------- 内部 ----------

    def _conn(self, ctx: dict) -> tuple[str, dict[str, str]]:
        """取上游地址与请求头。

        Args:
            ctx: 连接器上下文。

        Returns:
            (base_url 去尾斜杠, headers)。

        Raises:
            JobSubmitFailed: 未配置 base_url。
        """
        config = ctx.get("config") or {}
        base_url = str(config.get("base_url") or "").rstrip("/")
        if not base_url:
            # 两种成因都可能：管理员没填服务地址（提交/查询/取结果路径的 config
            # 都来自宿主注入），或本会话未启用该插件（会话级插件开关默认全关，
            # 此时宿主注入的 plugins 里根本没有本插件的配置）。旧文案只提前者，
            # 会把"只是没勾选插件"的用户引向管理后台白排查。
            raise JobSubmitFailed(
                "谱图解析插件不可用：请确认管理员已在管理后台「插件」页填写服务地址，"
                "且本会话已在输入框的「+ → 插件」面板中启用 spec_agent")
        headers: dict[str, str] = {}
        # 凭证顺序：宿主按登录用户代签的动态 token 优先，回落插件配置的服务 token
        token = str(ctx.get("ai4ms_token") or config.get("token") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return base_url, headers

    def _locate(self, ctx: dict, params: dict) -> Path:
        """把 path 参数解析为工作区内的真实文件。

        Args:
            ctx: 连接器上下文（取 workspace_root）。
            params: 工具参数。

        Returns:
            谱图文件绝对路径。

        Raises:
            JobSubmitFailed: 缺 path、无工作区、路径越界（或指向目录）、
                文件不存在。
        """
        rel = str(params.get("path") or "").strip()
        if not rel:
            raise JobSubmitFailed("缺少参数 path（工作区内的谱图文件路径）")
        root_str = str(ctx.get("workspace_root") or "")
        if not root_str:
            raise JobSubmitFailed("当前会话没有可用的工作区，无法读取谱图文件")
        root = Path(root_str).resolve()
        try:
            target = (root / rel).resolve()
        except OSError as exc:
            raise JobSubmitFailed(f"路径非法: {rel}") from exc
        if root not in target.parents:
            # 覆盖两种情形：真越界（../../etc/passwd）、或指向工作区根自身（"."）
            raise JobSubmitFailed(f"路径越界或指向目录: {rel}")
        if not target.is_file():
            raise JobSubmitFailed(f"文件不存在: {rel}")
        return target

    async def _upload(self, client: httpx.AsyncClient, base_url: str,
                      headers: dict[str, str], target: Path) -> str:
        """上传谱图文件，返回上游 file_id。

        Args:
            client: HTTP 客户端。
            base_url: 上游地址。
            headers: 请求头。
            target: 本地文件路径。

        Returns:
            上游 file_id。

        Raises:
            JobSubmitFailed: 读取/上传失败或未返回 file_id。
        """
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise JobSubmitFailed(f"读取文件失败: {exc}") from exc
        files = {"file": (target.name, content, "application/octet-stream")}
        try:
            resp = await client.post(f"{base_url}/api/v1/files/upload",
                                     headers=headers, files=files)
        except httpx.TimeoutException as exc:
            raise JobSubmitFailed(f"上传谱图文件超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JobSubmitFailed(f"上传谱图文件失败: {exc}") from exc
        data = self._unwrap(resp, "上传谱图文件")
        file_id = str((data or {}).get("file_id") or "")
        if not file_id:
            raise JobSubmitFailed(f"上游未返回 file_id: {str(data)[:200]}")
        return file_id

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        """构造 HTTP 客户端（测试经模块级 `_transport` 注入 MockTransport）。

        Args:
            timeout_s: 读超时。

        Returns:
            异步客户端。
        """
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S),
            transport=_transport)

    @staticmethod
    def _unwrap(resp: httpx.Response, action: str,
                raise_on_error: bool = True) -> dict | None:
        """解包上游统一响应（`{code, message, data}`）。

        Args:
            resp: 上游响应。
            action: 动作名（拼错误文案用）。
            raise_on_error: 失败时是否抛 JobSubmitFailed（False 时返回 None）。

        Returns:
            `data` 字段；失败且不抛时返回 None。

        Raises:
            JobSubmitFailed: HTTP 非 200、非 JSON、或 code != 0。
        """
        def _fail(msg: str) -> None:
            if raise_on_error:
                raise JobSubmitFailed(msg)

        if resp.status_code in (401, 403):
            _fail("访问凭证无效或已过期（管理员可在管理后台「插件」页更新凭证）")
            return None
        if resp.status_code != 200:
            _fail(f"{action}失败：上游返回 {resp.status_code} {resp.text[:200]}")
            return None
        try:
            body = resp.json() or {}
        except ValueError:
            _fail(f"{action}失败：上游返回非 JSON")
            return None
        if body.get("code") != 0:
            _fail(f"{action}失败：{body.get('message') or body.get('code')}")
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None


# 本插件贡献的连接器（宿主 PluginService 挂载时注册）
CONNECTORS = [
    SpectraTaskConnector("spec.task.nmr", "nmr"),
    SpectraTaskConnector("spec.task.ir", "ir"),
    SpectraTaskConnector("spec.task.gpc", "gpc"),
    SpectraTaskConnector("spec.task.raman", "raman"),
    SpectraTaskConnector("spec.task.lcms", "lcms"),
]
