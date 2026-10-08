"""Poly_Agent 高分子垂类预测内置工具。

来源：Synlora 插件 catalog/plugins/poly_agent/tools.py 平移。
差异：配置改读环境变量（POLY_AGENT_BASE_URL/USERNAME/PASSWORD）；
工作区文件读取走 _workspace_io；ToolResult 改 dict 返回。
上游契约（Poly_Agent Backend 0.1.0，2026-09-22 实测）：
- 垂类算法入口为 `POST /api/v1/research-engine/algorithm-runs`（algorithm_id +
  input_snapshot），同步阻塞执行，响应 data 内直接带 status 与 output_summary；
  若上游改异步（queued/running），兜底轮询详情接口直至终态；
- 认证为平台自有账号体系（`POST /api/v1/auth/login` 换 token，12h 过期），
  token 进程内缓存 + 过期/401 自动重登；
- Raman 解析需上传光谱文件，走 `POST /research-engine/algorithm-runs:multipart`
  （表单字段 `payload` 为 JSON 字符串，文件 part 名 spectrum_file）。
"""

import asyncio
import json
import os
import time
from pathlib import PurePosixPath
from typing import Literal

import httpx
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.buildin._workspace_io import download_workspace_file
from yuxi.agents.toolkits.registry import tool

CONNECT_TIMEOUT_S = 10.0     # 连接超时（宿主不可达时快速失败）
REQUEST_TIMEOUT_S = 600.0    # 读超时（上游同步执行模型推理，实测 44s，放宽留余量）
POLL_TIMEOUT_S = 60.0        # 轮询单个请求的读超时
POLL_INTERVAL_S = 5.0        # 兜底轮询间隔（正常响应即终态，不进入轮询）
UPLOAD_TIMEOUT_S = 120.0     # 光谱文件上传单独放宽
TOKEN_REFRESH_MARGIN_S = 300.0   # 凭证到期前提前重登的余量
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 上游限制 10MiB，超限直接本地拒绝

# 上游 base 路径段（与 /api/v1 拼接）
LOGIN_PATH = "/api/v1/auth/login"
RUNS_PATH = "/api/v1/research-engine/algorithm-runs"

# 光谱文件扩展名 → MIME（上游 input_assets.mime_types 白名单内的子集）
SPECTRUM_MIME: dict[str, str] = {
    ".txt": "text/plain",
    ".dat": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# 测试注入 MockTransport 的钩子；None 时 httpx 使用默认传输
_transport: httpx.BaseTransport | None = None

# 平台级凭证缓存（worker 单进程部署，模块级即进程级；跨用户共享同一账号）
_token_cache: dict = {"token": "", "expires_at": 0.0}
_token_lock = asyncio.Lock()


def _reset_token_cache() -> None:
    """清空平台级 token 缓存（测试隔离用）。"""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0


def _settings() -> dict[str, str]:
    """读取 Poly_Agent 环境变量配置。"""
    return {
        "base_url": (os.getenv("POLY_AGENT_BASE_URL") or "").strip().rstrip("/"),
        "username": (os.getenv("POLY_AGENT_USERNAME") or "").strip(),
        "password": (os.getenv("POLY_AGENT_PASSWORD") or "").strip(),
    }


def _error(error: str, message: str, **extra) -> dict:
    """构造上游类错误的结构化返回（不抛异常，供模型阅读后自行决策）。"""
    result = {"ok": False, "error": error, "message": message}
    result.update(extra)
    return result


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端（读超时 600s：上游同步执行模型推理；连接超时短）。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        transport=_transport,
    )


def _error_text(error: object, fallback: str) -> str:
    """把上游 error 字段整理成可读文本。

    Args:
        error: 上游返回的 error 字段（dict/str/None）。
        fallback: error 为空时的兜底文案。

    Returns:
        拼好的错误描述。
    """
    if isinstance(error, dict):
        text = "：".join(str(error.get(k) or "").strip()
                        for k in ("message", "detail")
                        if error.get(k))
        return text or json.dumps(error, ensure_ascii=False)
    if error:
        return str(error)
    return fallback


async def _login(client: httpx.AsyncClient, base_url: str,
                 username: str, password: str) -> tuple[str, float]:
    """登录 Poly_Agent 换取访问凭证。

    Args:
        client: HTTP 客户端。
        base_url: 服务地址（已去尾斜杠）。
        username: 平台账号。
        password: 平台密码。

    Returns:
        (access_token, 过期时刻的 Unix 秒)；上游未给 expires_at 时按 12h 兜底。

    Raises:
        RuntimeError: 上游拒绝登录或响应结构异常（message 为可读原因）。
    """
    resp = await client.post(
        f"{base_url}{LOGIN_PATH}",
        json={"username": username, "password": password})
    if resp.status_code != 200:
        detail = ""
        try:
            err_body = resp.json() or {}
            err_data = err_body.get("data")
            detail = str((err_data or {}).get("detail") or err_body.get("message") or "")
        except ValueError:
            pass
        raise RuntimeError(
            f"登录失败：{detail or f'上游返回 {resp.status_code} {resp.text[:200]}'}")
    try:
        body = resp.json() or {}
    except ValueError as exc:
        raise RuntimeError(f"登录失败：上游返回非 JSON: {resp.text[:200]}") from exc
    if body.get("code") != 0:
        raise RuntimeError(f"登录失败：{body.get('message') or body.get('code')}")
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError(f"登录失败：上游未返回 access_token: {str(data)[:200]}")
    expires_at = float(data.get("expires_at") or (time.time() + 12 * 3600))
    return str(data["access_token"]), expires_at


async def _ensure_token(client: httpx.AsyncClient, base_url: str,
                        username: str, password: str) -> str:
    """取当前有效凭证（缓存命中直接用，否则登录并缓存；双重检查锁）。

    Args:
        client: HTTP 客户端。
        base_url: 服务地址（已去尾斜杠）。
        username: 平台账号。
        password: 平台密码。

    Returns:
        有效的 access_token。

    Raises:
        RuntimeError: 登录失败（message 为可读原因）。
    """
    if (_token_cache["token"]
            and _token_cache["expires_at"] - TOKEN_REFRESH_MARGIN_S > time.time()):
        return _token_cache["token"]
    async with _token_lock:
        # 双重检查：等锁期间可能已被并发调用刷新
        if (_token_cache["token"]
                and _token_cache["expires_at"] - TOKEN_REFRESH_MARGIN_S > time.time()):
            return _token_cache["token"]
        token, expires_at = await _login(client, base_url, username, password)
        _token_cache["token"] = token
        _token_cache["expires_at"] = expires_at
        return token


def _clear_token() -> None:
    """清空凭证缓存（401 后强制下次重登）。"""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0


async def _post_run(client: httpx.AsyncClient, base_url: str, token: str,
                    payload: dict,
                    file_part: tuple[str, tuple[str, bytes, str]] | None) -> httpx.Response:
    """提交算法运行（JSON 或 multipart 二选一）。

    Args:
        client: HTTP 客户端。
        base_url: 服务地址（已去尾斜杠）。
        token: 访问凭证。
        payload: AlgorithmRunCreate 请求体。
        file_part: 文件上传时为 (字段名, (文件名, 内容, MIME))；纯 JSON 时为 None。

    Returns:
        上游响应。
    """
    headers = {"Authorization": f"Bearer {token}"}
    if file_part is None:
        return await client.post(f"{base_url}{RUNS_PATH}", headers=headers,
                                 json=payload, timeout=REQUEST_TIMEOUT_S)
    field, upload = file_part
    return await client.post(
        f"{base_url}{RUNS_PATH}:multipart", headers=headers,
        data={"payload": json.dumps(payload, ensure_ascii=False)},
        files={field: upload}, timeout=UPLOAD_TIMEOUT_S)


async def _run_algorithm(algorithm_id: str, input_snapshot: dict,
                         file_part: tuple[str, tuple[str, bytes, str]] | None = None) -> dict:
    """调用 Poly_Agent 垂类算法并归一化为工具结果。

    上游创建接口同步阻塞执行：正常响应即终态（completed/failed）；若上游
    返回 queued/running（部署侧改为异步时），兜底轮询详情接口直至终态。

    Args:
        algorithm_id: 上游算法 id。
        input_snapshot: 算法输入快照（字段名与上游 input_schema 一致）。
        file_part: multipart 文件部分（Raman 光谱）；纯 JSON 调用为 None。

    Returns:
        成功为 {"ok": True, "run_id", "status", "content": output_summary JSON}；
        失败为 {"ok": False, "error", "message"}（error 为 poly_agent_unconfigured /
        timeout / connection_error / unauthorized / http_error / upstream_error）。
    """
    settings = _settings()
    base_url, username, password = settings["base_url"], settings["username"], settings["password"]
    if not base_url or not username or not password:
        return _error(
            "poly_agent_unconfigured",
            "Poly_Agent 未配置：请在部署配置中设置环境变量 "
            "POLY_AGENT_BASE_URL / POLY_AGENT_USERNAME / POLY_AGENT_PASSWORD",
        )
    deadline = time.monotonic() + REQUEST_TIMEOUT_S
    try:
        async with _make_client() as client:
            token = await _ensure_token(client, base_url, username, password)
            payload = {"algorithm_id": algorithm_id, "input_snapshot": input_snapshot}
            resp = await _post_run(client, base_url, token, payload, file_part)
            if resp.status_code in (401, 403):
                # 凭证可能在缓存期内被上游吊销：强制重登后重试一次
                _clear_token()
                token = await _ensure_token(client, base_url, username, password)
                resp = await _post_run(client, base_url, token, payload, file_part)
            if resp.status_code in (401, 403):
                return _error(
                    "unauthorized",
                    "访问凭证无效（重登后仍被拒绝）：请核对 POLY_AGENT_USERNAME / "
                    "POLY_AGENT_PASSWORD，或确认该账号未被停用",
                )
            if resp.status_code != 200:
                return _error("http_error",
                              f"高分子预测服务返回 {resp.status_code}: {resp.text[:300]}")
            try:
                body = resp.json() or {}
            except ValueError:
                return _error("upstream_error",
                              f"高分子预测服务返回非 JSON: {resp.text[:200]}")
            if body.get("code") != 0:
                detail = body.get("data")
                if isinstance(detail, dict):
                    detail = detail.get("detail")
                return _error(
                    "upstream_error",
                    "高分子预测服务错误: " + str(detail or body.get("message") or body.get("code")),
                )
            data = body.get("data")
            if not isinstance(data, dict) or not data.get("run_id"):
                return _error("upstream_error",
                              f"高分子预测服务返回结构异常: {str(data)[:200]}")
            run_id = str(data["run_id"])
            status = str(data.get("status") or "")
            while status in ("queued", "running") and time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL_S)
                poll = await client.get(
                    f"{base_url}{RUNS_PATH}/{run_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=POLL_TIMEOUT_S)
                if poll.status_code != 200:
                    break  # 轮询失败不掩盖已有结果：交给下方按当前状态收尾
                try:
                    poll_body = poll.json() or {}
                except ValueError:
                    break
                if poll_body.get("code") == 0 and isinstance(poll_body.get("data"), dict):
                    data = poll_body["data"]
                    status = str(data.get("status") or "")
            return _result_from_run(run_id, status, data)
    except httpx.TimeoutException as exc:
        return _error("timeout", f"高分子预测服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接高分子预测服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"高分子预测服务请求失败: {exc}")
    except RuntimeError as exc:
        return _error("unauthorized", str(exc))


def _result_from_run(run_id: str, status: str, data: dict) -> dict:
    """把终态运行记录整理为工具返回。

    Args:
        run_id: 上游运行 id。
        status: 运行状态（completed/failed/其余视为仍在执行）。
        data: 上游运行详情。

    Returns:
        成功为 content=output_summary JSON 的 dict；失败/超时给出可操作文案。
    """
    if status == "failed":
        return _error(
            "upstream_error",
            "算法执行失败：" + _error_text(data.get("error"), "上游未给出失败原因"),
            run_id=run_id, status=status,
        )
    if status != "completed":
        return _error(
            "timeout",
            f"算法仍在执行（status={status or 'unknown'}），已等待超时；"
            f"可稍后用运行 id {run_id} 到 Poly_Agent 平台查看结果。",
            run_id=run_id, status=status,
        )
    output = data.get("output_summary")
    if not isinstance(output, dict) or not output:
        return _error("upstream_error",
                      f"算法执行完成但未返回结果: {str(data)[:300]}",
                      run_id=run_id, status=status)
    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "content": json.dumps(output, ensure_ascii=False, indent=1),
    }


async def _upload_file_part(path: str, runtime: ToolRuntime) -> tuple[str, tuple[str, bytes, str]] | dict:
    """把 path 参数解析为工作区内的光谱文件并组装 multipart 文件部分。

    Args:
        path: 工作区相对路径。
        runtime: LangGraph 工具运行时。

    Returns:
        文件部分 (字段名, (文件名, 内容, MIME))；参数/路径/大小不合规时返回
        错误 dict（bad_request）。
    """
    rel = str(path or "").strip()
    if not rel:
        return _error("bad_request", "缺少参数 path（工作区内光谱文件路径）")
    try:
        content, name = await download_workspace_file(rel, runtime, max_bytes=MAX_UPLOAD_BYTES)
    except ValueError as exc:
        return _error("bad_request", f"光谱文件不存在或路径非法: {exc}")
    mime = SPECTRUM_MIME.get(PurePosixPath(name).suffix.lower(), "application/octet-stream")
    return ("spectrum_file", (name, content, mime))


class PolyTgPredictInput(BaseModel):
    smiles: str = Field(description="单体/重复单元 SMILES（单个）")


class PolyElectrolytePredictInput(BaseModel):
    formulations: list[dict] = Field(description="配方对象列表（字段见工具描述）")
    recommendation_limit: float = Field(default=5, description="推荐配方数量上限")
    recommendation_task_type: str = Field(default="", description="推荐任务类型约束（可选）")


class PolyRamanAnalyzeInput(BaseModel):
    path: str = Field(description="工作区内光谱文件的相对路径（.txt/.dat/.csv/.xlsx）")
    spectype: Literal["raman"] = Field(default="raman", description="谱图类型（当前仅支持 raman）")
    mode: str = Field(default="function_groups", description="解析模式")
    x0: float | None = Field(default=None, description="分析区间起点（波数，可选）")
    x1: float | None = Field(default=None, description="分析区间终点（波数，可选）")
    k: int = Field(default=3, description="候选结构数量")
    transmittance: bool = Field(default=False, description="输入是否为透过率数据")
    device: Literal["cpu", "cuda"] = Field(default="cpu", description="推理设备")


class PolyReactivityFitInput(BaseModel):
    data_rows: list[dict] = Field(description="实验数据行列表（字段见工具描述）")
    delta_f_default: float = Field(default=0.01, description="单体 1 摩尔分数增量步长")


class PolySiliconBondInput(BaseModel):
    sample: dict = Field(description="样品对象（对齐光谱 + 组成，原样透传上游）")


@tool(
    category="poly_agent",
    tags=["高分子", "预测"],
    display_name="聚酰亚胺 Tg 预测",
    description=(
        "聚酰亚胺 Tg 预测：给定单体/重复单元 SMILES，预测玻璃化转变温度（°C）。"
        "单次只接受一个 SMILES；多个分子请多次调用。上游模型推理约 1 分钟，"
        "调用后耐心等待，不要重复提交同一请求。"
    ),
    args_schema=PolyTgPredictInput,
)
async def poly_tg_predict(smiles: str) -> dict:
    """聚酰亚胺玻璃化转变温度预测（SMILES → Tg）。"""
    return await _run_algorithm("PI_Tg_predictor", {"smiles": smiles})


@tool(
    category="poly_agent",
    tags=["高分子", "预测"],
    display_name="氟基电解质配方预测",
    description=(
        "氟基高分子电解质配方预测：输入电解质配方列表（锂盐/溶剂/单体/填料组成），"
        "多输出回归预测第 1/4/20 圈放电比容量与库伦效率，并按任务类型约束对候选"
        "配方排序，输出推荐配方及前驱体液配制、合成工艺、原位聚合工艺。"
        "formulations 为对象数组，每个元素字段：formula_id(编号)、"
        "task_type(任务类型，如 electrolyte)、lithium_salt(锂盐 SMILES)、"
        "lithium_salt_mol_L(锂盐摩尔浓度)、electrolyte_component_1/2/3"
        "(组分 SMILES) 与对应 electrolyte_component_1/2/3_mol_ratio(摩尔比)、"
        "monomer_1/2(单体 SMILES) 与 monomer_1/2_mass_ratio(质量比)、"
        "electrolyte_mass_ratio_in_polymer_mix(电解质在聚合物混合物中的质量比)、"
        "filler_type(填料类型)、filler_wt_pct(填料质量分数)。"
        "无对应组分时传空串/0，不要编造字段值。"
    ),
    args_schema=PolyElectrolytePredictInput,
)
async def poly_electrolyte_predict(
    formulations: list[dict],
    recommendation_limit: float = 5,
    recommendation_task_type: str = "",
) -> dict:
    """氟基电解质配方性能预测与配方推荐。"""
    snapshot: dict = {"formulations": formulations or []}
    if recommendation_limit is not None:
        snapshot["recommendation_limit"] = recommendation_limit
    if recommendation_task_type:
        snapshot["recommendation_task_type"] = recommendation_task_type
    return await _run_algorithm("fluorinated_electrolyte_model_2", snapshot)


@tool(
    category="poly_agent",
    tags=["高分子", "谱图"],
    display_name="Raman 光谱解析",
    description=(
        "Raman 光谱结构解析：上传工作区内的光谱文件（.txt/.dat/.csv/.xlsx，"
        "x-y 两列数据），解析候选结构并给出得分。可选指定分析区间 [x0, x1] 与"
        "候选数 k。文件较大时解析较慢，调用后耐心等待。"
    ),
    args_schema=PolyRamanAnalyzeInput,
)
async def poly_raman_analyze(
    path: str,
    spectype: str = "raman",
    mode: str = "function_groups",
    x0: float | None = None,
    x1: float | None = None,
    k: int = 3,
    transmittance: bool = False,
    device: str = "cpu",
    runtime: ToolRuntime = None,
) -> dict:
    """Raman 光谱解析（工作区光谱文件 → 候选结构）。"""
    file_part = await _upload_file_part(path, runtime)
    if isinstance(file_part, dict):
        return file_part
    snapshot: dict = {
        "spectype": spectype,
        "mode": mode,
        "k": k,
        "transmittance": transmittance,
        "device": device,
    }
    if x0 is not None:
        snapshot["x0"] = x0
    if x1 is not None:
        snapshot["x1"] = x1
    return await _run_algorithm("raman_structure_analyzer", snapshot, file_part=file_part)


@tool(
    category="poly_agent",
    tags=["高分子", "拟合"],
    display_name="共聚竞聚率拟合",
    description=(
        "共聚竞聚率拟合：输入单体投料比与两单体转化率的实验数据行，基于 IUPAC "
        "终端模型网格搜索拟合竞聚率 r1、r2，并计算 95% 联合置信区间。"
        "data_rows 为对象数组，每行字段：比例（投料比，如 \"2:8\"）、"
        "转化率1（单体 1 转化率，如 \"2.53%\"）、转化率2（单体 2 转化率）。"
        "至少需要多组不同投料比的数据，行数过少拟合不可靠。"
    ),
    args_schema=PolyReactivityFitInput,
)
async def poly_reactivity_fit(
    data_rows: list[dict],
    delta_f_default: float = 0.01,
) -> dict:
    """共聚竞聚率拟合（实验数据行 → r1/r2 与置信区间）。"""
    snapshot: dict = {"data_rows": data_rows or []}
    if delta_f_default is not None:
        snapshot["delta_f_default"] = delta_f_default
    return await _run_algorithm("reactivity_ratio_fitter", snapshot)


@tool(
    category="poly_agent",
    tags=["高分子", "评分"],
    display_name="硅键谱图集成评分",
    description=(
        "Silicon Bond7 谱图集成评分：输入样品对象（五条对齐光谱 + 组成信息，"
        "具体结构按上游模型约定），输出七个键级的 LSU 得分。不做结构推断与"
        "聚合物状态判断，仅输出键级评分。sample 对象结构不确定时先向调用方"
        "确认或索要样例，不要自行编造字段。"
    ),
    args_schema=PolySiliconBondInput,
)
async def poly_silicon_bond(sample: dict) -> dict:
    """硅键谱图集成评分（样品对象 → 七键级 LSU 得分）。"""
    if not isinstance(sample, dict):
        return _error("bad_request", "缺少参数 sample（样品对象）")
    return await _run_algorithm("silicon_bond7_ssl_ensemble_seed0", {"sample": sample})
