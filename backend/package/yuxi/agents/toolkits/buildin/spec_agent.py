"""Spec_Agent 谱图解析内置工具。

来源：Synlora 插件 catalog/plugins/spec_agent（tools.py 同步三件套平移；
connectors.py 的五类异步任务改造为同步内嵌轮询工具，见文末 spec_task_* 部分）。
认证走 AI4MS 平台账号（AI4MS_USERNAME / AI4MS_PASSWORD，经 /api/v1/auth/login
换 token，与 Poly_Agent 同款接口、token 互不通用）；未配置账号时不发认证头
（服务端未开鉴权的部署可用）。
"""

import asyncio
import json
import os
import time
from typing import Any

import httpx
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.buildin._ai4ms_auth import Ai4msTokenProvider
from yuxi.agents.toolkits.buildin._workspace_io import download_workspace_file
from yuxi.agents.toolkits.registry import tool

CONNECT_TIMEOUT_S = 10.0
NMR_REQUEST_TIMEOUT_S = 360.0

# 测试注入 MockTransport 的钩子；None 时 httpx 使用默认传输
_transport: httpx.BaseTransport | None = None

# 本服务独立的 token 提供者（与 Poly_Agent 的 token 互不通用）
_token_provider = Ai4msTokenProvider()


def _settings() -> dict[str, str]:
    """读取 Spec_Agent 环境变量配置（账号走全局 AI4MS_*）。"""
    username, password = Ai4msTokenProvider.credentials()
    return {
        "base_url": (os.getenv("SPEC_AGENT_BASE_URL") or "").strip().rstrip("/"),
        "username": username,
        "password": password,
    }


def _error(error: str, message: str) -> dict:
    """构造上游类错误的结构化返回（不抛异常，供模型阅读后自行决策）。"""
    return {"ok": False, "error": error, "message": message}


def _make_client(**kwargs) -> httpx.AsyncClient:
    """构造带统一超时的 httpx 客户端。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(kwargs.pop("timeout", NMR_REQUEST_TIMEOUT_S), connect=CONNECT_TIMEOUT_S),
        transport=_transport,
        **kwargs,
    )


async def _auth_headers(client: httpx.AsyncClient, base_url: str,
                        username: str, password: str) -> dict[str, str]:
    """构造请求头；配置了 AI4MS 账号时登录换 token，未配置时不发送认证头。

    Args:
        client: HTTP 客户端（复用已打开的连接与传输配置）。
        base_url: 服务地址（已去尾斜杠）。
        username: AI4MS 平台账号。
        password: AI4MS 平台密码。

    Returns:
        请求头 dict；未配置账号时为空 dict。
    """
    if not username or not password:
        return {}
    token = await _token_provider.ensure_token(client, base_url, username, password)
    return {"Authorization": f"Bearer {token}"}


async def _call_nmrserver(path: str, payload: dict) -> dict:
    """调用 nmrserver 同步接口并做错误归一化。

    Args:
        path: 接口名（forward / reverse / search）。
        payload: 请求 JSON 体。

    Returns:
        成功为 {"ok": True, "count": N, "content": "逐条 JSON 行"}；
        失败为 {"ok": False, "error": 错误码, "message": 描述}。
    """
    settings = _settings()
    if not settings["base_url"]:
        return _error(
            "spec_agent_unconfigured",
            "Spec_Agent 未配置服务地址：请在部署配置中设置环境变量 SPEC_AGENT_BASE_URL",
        )
    try:
        async with _make_client() as client:
            headers = await _auth_headers(
                client, settings["base_url"], settings["username"], settings["password"])
            resp = await client.post(
                f"{settings['base_url']}/api/v1/nmrserver/{path}",
                headers=headers,
                json=payload,
            )
    except httpx.TimeoutException as exc:
        return _error("timeout", f"谱图服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接谱图服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"谱图服务请求失败: {exc}")
    except RuntimeError as exc:
        return _error("unauthorized", str(exc))
    if resp.status_code in (401, 403):
        _token_provider.reset()  # 下次调用重新登录
        return _error(
            "unauthorized",
            "访问凭证无效或已过期：请核对 AI4MS 平台账号（AI4MS_USERNAME / AI4MS_PASSWORD）；"
            "凭证缓存已清除，稍后重试将自动重新登录（这不是参数错误，请勿原样重试）",
        )
    if resp.status_code != 200:
        return _error("http_error", f"谱图服务返回 {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json() or {}
    except ValueError:
        return _error("upstream_error", f"谱图服务返回非 JSON: {resp.text[:200]}")
    if body.get("code") != 0:
        return _error("upstream_error", f"谱图服务错误: {body.get('message') or body.get('code')}")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _error("upstream_error", f"谱图服务返回结构异常: {str(data)[:200]}")
    items = data["items"]
    if not items:
        return {"ok": True, "count": 0, "content": "（未返回结果）"}
    return {
        "ok": True,
        "count": len(items),
        "content": "\n".join(json.dumps(item, ensure_ascii=False) for item in items),
    }


class SpecNmrForwardInput(BaseModel):
    smiles_input: str = Field(description="待预测的分子 SMILES，多个分子每行一个")


class SpecNmrReverseInput(BaseModel):
    c_shifts_input: str = Field(default="", description="实测碳谱化学位移，逗号或换行分隔")
    h_shifts_input: str = Field(default="", description="实测氢谱化学位移，逗号或换行分隔")
    h_split_input: str = Field(default="", description="氢谱峰裂分信息（如 3.2, t, 2H 每行一条）")
    formula: str = Field(default="", description="已知分子式（如 C2H6O），可留空")
    allowed_elements: str = Field(default="", description="候选元素集合，逗号分隔，可留空")
    candidates: str = Field(default="", description="候选 SMILES 列表约束，可留空")


class SpecNmrSearchInput(BaseModel):
    c_shifts_input: str = Field(default="", description="实测碳谱化学位移，逗号或换行分隔")
    h_shifts_input: str = Field(default="", description="实测氢谱化学位移，逗号或换行分隔")
    h_split_input: str = Field(default="", description="氢谱峰裂分信息，可留空")
    num_search: int = Field(default=500, ge=10, le=10000, description="数据库检索样本数，默认 500")
    topk: int = Field(default=10, ge=1, le=100, description="返回候选数，默认 10")
    allowed_elements: str = Field(default="C,H,N,O", description="候选元素集合，默认 C,H,N,O")


SPEC_NMR_FORWARD_DESCRIPTION = """核磁正向预测：给定分子 SMILES（可多行）预测其 13C/1H 化学位移。
上游为模型推理，单次可能需要数分钟，调用后请耐心等待，不要重复提交相同请求。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁正向预测",
    description=SPEC_NMR_FORWARD_DESCRIPTION,
    args_schema=SpecNmrForwardInput,
)
async def spec_nmr_forward(smiles_input: str) -> dict:
    """给定分子 SMILES 预测 13C/1H 化学位移（正向预测）。"""
    return await _call_nmrserver("forward", {"smiles_input": smiles_input})


SPEC_NMR_REVERSE_DESCRIPTION = """核磁反向预测：给定实测化学位移（碳谱/氢谱/裂分）反推候选分子结构。
可附分子式、元素约束或候选列表缩小范围。上游为模型推理，单次可能需要数分钟。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁反向预测",
    description=SPEC_NMR_REVERSE_DESCRIPTION,
    args_schema=SpecNmrReverseInput,
)
async def spec_nmr_reverse(
    c_shifts_input: str = "",
    h_shifts_input: str = "",
    h_split_input: str = "",
    formula: str = "",
    allowed_elements: str = "",
    candidates: str = "",
) -> dict:
    """给定实测化学位移反推候选分子结构（反向预测）。"""
    return await _call_nmrserver(
        "reverse",
        {
            "c_shifts_input": c_shifts_input,
            "h_shifts_input": h_shifts_input,
            "h_split_input": h_split_input,
            "formula": formula,
            "allowed_elements": allowed_elements,
            "candidates": candidates,
        },
    )


SPEC_NMR_SEARCH_DESCRIPTION = """核磁数据库检索：给定实测化学位移在数据库中检索匹配分子。
返回按匹配度排序的候选列表。检索样本数与候选数可调。"""


@tool(
    category="spec_agent",
    tags=["谱图", "核磁"],
    display_name="核磁数据库检索",
    description=SPEC_NMR_SEARCH_DESCRIPTION,
    args_schema=SpecNmrSearchInput,
)
async def spec_nmr_search(
    c_shifts_input: str = "",
    h_shifts_input: str = "",
    h_split_input: str = "",
    num_search: int = 500,
    topk: int = 10,
    allowed_elements: str = "C,H,N,O",
) -> dict:
    """给定实测化学位移在核磁数据库中检索匹配分子。"""
    return await _call_nmrserver(
        "search",
        {
            "c_shifts_input": c_shifts_input,
            "h_shifts_input": h_shifts_input,
            "h_split_input": h_split_input,
            "num_search": str(num_search),
            "topk": str(topk),
            "allowed_elements": allowed_elements,
        },
    )


# ---------------------------------------------------------------------------
# 五类谱图文件解析（同步内嵌轮询；替代 Synlora 的 job 模式）
# ---------------------------------------------------------------------------

TASK_QUICK_TIMEOUT_S = 60.0    # 上传/提交/查状态/取结果都是秒级操作
TASK_UPLOAD_TIMEOUT_S = 120.0  # 上传单独放宽
TASK_POLL_INTERVAL_S = 5.0
TASK_MAX_WAIT_S_DEFAULT = 1500.0  # 轮询总预算；必须显著小于 worker job_timeout(3600s)
TASK_MAX_WAIT_S_CEILING = 3300.0
TASK_MAX_WAIT_S_FLOOR = 60.0
TASK_FILE_MAX_BYTES = 100 * 1024 * 1024
RESULT_RENDER_LIMIT = 30000  # 回给模型的结果字符预算


def _task_max_wait_s() -> float:
    """读取并钳制谱图任务轮询总预算（秒）。"""
    raw = (os.getenv("SPEC_TASK_MAX_WAIT_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return TASK_MAX_WAIT_S_DEFAULT
    return min(max(value, TASK_MAX_WAIT_S_FLOOR), TASK_MAX_WAIT_S_CEILING)


def _unwrap(resp: httpx.Response, action: str) -> tuple[dict | None, dict | None]:
    """解析上游 {code, message, data} 包络。

    Args:
        resp: 上游响应。
        action: 动作名（用于错误消息，如"上传谱图文件"）。

    Returns:
        (错误 dict 或 None, data dict 或 None)。
    """
    if resp.status_code in (401, 403):
        _token_provider.reset()  # 下次调用重新登录
        return _error(
            "unauthorized",
            "访问凭证无效或已过期：请核对 AI4MS 平台账号（AI4MS_USERNAME / AI4MS_PASSWORD）；"
            "凭证缓存已清除，稍后重试将自动重新登录（这不是参数错误，请勿原样重试）",
        ), None
    if resp.status_code != 200:
        return _error("http_error", f"{action}失败：上游返回 {resp.status_code} {resp.text[:200]}"), None
    try:
        body = resp.json() or {}
    except ValueError:
        return _error("upstream_error", f"{action}失败：上游返回非 JSON: {resp.text[:200]}"), None
    if body.get("code") != 0:
        return _error("upstream_error", f"{action}失败: {body.get('message') or body.get('code')}"), None
    data = body.get("data")
    if not isinstance(data, dict):
        return _error("upstream_error", f"{action}失败：上游返回结构异常: {str(data)[:200]}"), None
    return None, data


def _render_result(result: Any) -> Any:
    """超大结果截断，保护模型上下文。"""
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= RESULT_RENDER_LIMIT:
        return result
    return {
        "truncated": True,
        "preview": text[:RESULT_RENDER_LIMIT],
        "hint": f"结果共 {len(text)} 字符已截断；完整结果请在服务端按 task_id 取回",
    }


def _error_text(error: Any) -> str:
    """把上游 error 字段拼成可读文本。"""
    if isinstance(error, dict):
        code = error.get("error_code")
        message = error.get("error_message")
        detail = error.get("error_detail")
        parts = f"[{code}] {message}：{detail}" if code else f"{message}：{detail}"
        return parts if message or detail else str(error)
    return str(error or "未知原因")


async def _run_spectra_task(
    endpoint: str, default_mode: str | None, path: str, params: dict | None, runtime: ToolRuntime
) -> dict:
    """上传谱图文件→提交解析任务→内嵌轮询至终态→取结果（同步语义）。

    Args:
        endpoint: 上游任务类型（nmr/ir/gpc/raman/lcms）。
        default_mode: 缺省 mode 兜底值（仅 raman 用 function_groups）。
        path: 谱图文件的工作区相对路径。
        params: 任务参数（可选，自由字典，字段见 spec-spectra 技能参数表）。
        runtime: LangGraph 工具运行时。

    Returns:
        成功为 {"ok": True, "task_id", "status", "result"}；失败为错误 dict。
    """
    settings = _settings()
    if not settings["base_url"]:
        return _error(
            "spec_agent_unconfigured",
            "Spec_Agent 未配置服务地址：请在部署配置中设置环境变量 SPEC_AGENT_BASE_URL",
        )
    try:
        file_bytes, file_name = await download_workspace_file(
            path, runtime, max_bytes=TASK_FILE_MAX_BYTES
        )
    except ValueError:
        raise  # 路径类问题按参数错误抛出，由工具框架兜底
    upstream_params = dict(params or {})
    if default_mode and not upstream_params.get("mode"):
        upstream_params["mode"] = default_mode
    base = settings["base_url"]
    max_wait = _task_max_wait_s()
    deadline = time.monotonic() + max_wait
    try:
        async with _make_client(timeout=TASK_QUICK_TIMEOUT_S) as client:
            headers = await _auth_headers(client, base, settings["username"], settings["password"])
            try:
                upload = await client.post(
                    f"{base}/api/v1/files/upload",
                    headers=headers,
                    files={"file": (file_name, file_bytes, "application/octet-stream")},
                    timeout=TASK_UPLOAD_TIMEOUT_S,
                )
            except httpx.HTTPError as exc:
                return _error("http_error", f"上传谱图文件失败: {exc}")
            err, data = _unwrap(upload, "上传谱图文件")
            if err:
                return err
            file_id = data.get("file_id")
            if not file_id:
                return _error("upstream_error", f"上传谱图文件失败：上游未返回 file_id: {str(data)[:200]}")
            try:
                submit = await client.post(
                    f"{base}/api/v1/tasks/{endpoint}",
                    headers=headers,
                    json={"input": {"input_type": "file_id", "file_id": file_id},
                          "params": upstream_params},
                )
            except httpx.HTTPError as exc:
                return _error("http_error", f"提交解析任务失败: {exc}")
            err, data = _unwrap(submit, "提交解析任务")
            if err:
                return err
            task_id = data.get("task_id")
            if not task_id:
                return _error("upstream_error", f"提交解析任务失败：上游未返回 task_id: {str(data)[:200]}")
            # 内嵌轮询：pending/queued/running → 终态
            while True:
                try:
                    status_resp = await client.get(f"{base}/api/v1/tasks/{task_id}", headers=headers)
                except httpx.HTTPError as exc:
                    return _error("http_error", f"查询任务状态失败（task_id={task_id}）: {exc}")
                err, data = _unwrap(status_resp, "查询任务状态")
                if err:
                    return err
                status = str(data.get("status") or "").strip().lower()
                if status in ("success", "completed"):
                    break
                if status in ("failed", "canceled", "cancelled"):
                    try:
                        result_resp = await client.get(
                            f"{base}/api/v1/tasks/{task_id}/result", headers=headers)
                        err, data = _unwrap(result_resp, "获取任务失败原因")
                        reason = _error_text((data or {}).get("error")) if not err else err["message"]
                    except httpx.HTTPError:
                        reason = "上游未返回失败原因"
                    return _error("upstream_failed", f"任务失败（task_id={task_id}）：{reason}")
                if time.monotonic() > deadline:
                    return _error(
                        "timeout",
                        f"任务 {task_id} 超过 {int(max_wait)} 秒仍在 {status} 状态，本次调用放弃等待；"
                        f"上游任务仍在运行，再次调用会重新提交新任务，请酌情调整参数后重试",
                    )
                await asyncio.sleep(TASK_POLL_INTERVAL_S)
            try:
                result_resp = await client.get(f"{base}/api/v1/tasks/{task_id}/result", headers=headers)
            except httpx.HTTPError as exc:
                return _error("http_error", f"获取任务结果失败（task_id={task_id}）: {exc}")
            err, data = _unwrap(result_resp, "获取任务结果")
            if err:
                return err
            return {
                "ok": True,
                "task_id": task_id,
                "status": "success",
                "result": _render_result(data.get("result")),
            }
    except httpx.TimeoutException as exc:
        return _error("timeout", f"谱图服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接谱图服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"谱图服务请求失败: {exc}")
    except RuntimeError as exc:
        return _error("unauthorized", str(exc))


class SpecTaskInput(BaseModel):
    path: str = Field(description="谱图文件的工作区相对路径（如 uploads/nmr.txt）")
    params: dict | None = Field(
        default=None,
        description="任务参数字典（可选）。可用字段与默认值见 spec-spectra 技能的参数表；"
                    "不认识的字段不要传，自造字段会被上游静默忽略",
    )


def _spectra_tool(endpoint: str, display_name: str, description: str, default_mode: str | None = None):
    """工厂：生成五类谱图任务的同构工具（显式命名，避免闭包函数名重名）。"""

    @tool(
        category="spec_agent",
        tags=["谱图", "文件解析"],
        display_name=display_name,
        description=description,
        args_schema=SpecTaskInput,
        name_or_callable=f"spec_task_{endpoint}",
    )
    async def spectra_task(
        path: str, params: dict | None = None, runtime: ToolRuntime = None
    ) -> dict:
        return await _run_spectra_task(endpoint, default_mode, path, params, runtime)

    return spectra_task


SPEC_TASK_NMR_DESCRIPTION = """解析核磁（NMR）谱图文件：峰识别、积分、multiplet 检测。
常用参数：nucleus（1H/13C）、threshold、integration_method 等，完整参数表见 spec-spectra 技能。
同步等待结果，通常数十秒到数分钟。"""

SPEC_TASK_IR_DESCRIPTION = """解析红外（IR）光谱文件。
常用参数：mode（greedy_decode/beam_search/retrieval 等 4 种）、k、x0/x1 波数区间、transmittance。
同步等待结果；完整参数表见 spec-spectra 技能。"""

SPEC_TASK_GPC_DESCRIPTION = """解析凝胶渗透色谱（GPC）谱图文件：分子量分布。
常用参数：detect_mode（auto/manual）、manual_interval（manual 时必填 [start, end]）。
同步等待结果；完整参数表见 spec-spectra 技能。"""

SPEC_TASK_RAMAN_DESCRIPTION = """解析拉曼（Raman）光谱文件。
默认 mode=function_groups（官能团识别）；greedy_decode/beam_search 当前部署不可用，除非明确要求不要传。
其余参数与 IR 一致；完整参数表见 spec-spectra 技能。同步等待结果。"""

SPEC_TASK_LCMS_DESCRIPTION = """解析液质（LC-MS）数据文件，基本无可调参数。
同步等待结果。"""


spec_task_nmr = _spectra_tool("nmr", "核磁谱图文件解析", SPEC_TASK_NMR_DESCRIPTION)
spec_task_ir = _spectra_tool("ir", "红外光谱文件解析", SPEC_TASK_IR_DESCRIPTION)
spec_task_gpc = _spectra_tool("gpc", "GPC 谱图文件解析", SPEC_TASK_GPC_DESCRIPTION)
spec_task_raman = _spectra_tool("raman", "拉曼光谱文件解析", SPEC_TASK_RAMAN_DESCRIPTION, default_mode="function_groups")
spec_task_lcms = _spectra_tool("lcms", "液质数据文件解析", SPEC_TASK_LCMS_DESCRIPTION)
