"""Poly_Agent 高分子垂类模型预测工具（插件包自带，宿主动态加载）。

配置由宿主按命名空间注入 ctx.extra["plugins"]["poly_agent"]（见 plugin.json 的
config_schema），本模块不认识任何宿主全局配置——插件只依赖宿主给出的这个通用通道。

上游契约（Poly_Agent Backend 0.1.0，2026-09-22 实测）：
- 垂类算法入口为 `POST /api/v1/research-engine/algorithm-runs`（algorithm_id +
  input_snapshot），**同步阻塞执行**，响应 data 内直接带 status 与 output_summary；
- 算法清单为 `GET /api/v1/agent-tools`（本插件静态注册，不动态枚举）；
- 认证为平台自有账号体系（`POST /api/v1/auth/login` 换 HMAC token，12h 过期），
  与 AI⁴MS 代签 token 不互通，故插件配置平台级账号，token 进程内缓存 + 过期/401
  自动重登；
- Raman 解析需上传光谱文件，走 `POST /research-engine/algorithm-runs:multipart`
  （表单字段 `payload` 为 JSON 字符串，文件 part 名与上游 input_assets.key 对齐）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from synlys_harness import ToolContext, ToolResult, tool

PLUGIN_ID = "poly_agent"
CONNECT_TIMEOUT_S = 10.0     # 连接超时（宿主不可达时快速失败）
REQUEST_TIMEOUT_S = 600.0    # 读超时（上游同步执行模型推理，实测 44s，放宽留余量）
POLL_TIMEOUT_S = 60.0        # 轮询单个请求的读超时
POLL_INTERVAL_S = 5.0        # 兜底轮询间隔（正常响应即终态，不进入轮询）
UPLOAD_TIMEOUT_S = 120.0     # 光谱文件上传单独放宽
TOOL_TIMEOUT_S = 640.0       # 工具级超时必须大于 HTTP 超时：让 HTTP 层先报错
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

# 平台级凭证缓存（宿主单进程部署，模块级即进程级；跨用户共享同一账号）
_token_cache: dict = {"token": "", "expires_at": 0.0}
_token_lock = asyncio.Lock()


def _config(ctx: ToolContext) -> dict:
    """取本插件在运行上下文中的配置（宿主按插件 id 命名空间注入）。

    Args:
        ctx: 工具上下文。

    Returns:
        插件配置 dict（未安装/未配置时为空 dict）。
    """
    return (ctx.extra.get("plugins") or {}).get(PLUGIN_ID) or {}


def _str_arg(args: dict, key: str, default: str = "") -> str:
    """取字符串参数（None/缺失一律回落默认值，避免 str(None) 变成 "None"）。

    Args:
        args: 工具参数。
        key: 参数名。
        default: 缺失或为 None 时的默认值。

    Returns:
        字符串参数值。
    """
    value = args.get(key)
    return default if value is None else str(value)


def _unconfigured() -> ToolResult:
    """插件未配置（缺地址或账号）时的统一错误。"""
    return ToolResult(
        ok=False,
        content=("高分子预测插件未配置服务地址或登录账号"
                 "（请在管理后台的插件页安装并填写）"),
        error="poly_agent_unconfigured",
    )


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端（测试经 monkeypatch 注入 MockTransport）。

    读超时放宽到 600s（上游同步执行模型推理）；连接超时短，宿主不可达时快速失败。

    Returns:
        配好超时的 httpx 异步客户端。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S))


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


def _safe_path(root: Path, rel: str) -> Path | None:
    """解析相对路径并确保不逃逸出 root。

    Args:
        root: 会话工作区根目录。
        rel: LLM 给出的相对路径。

    Returns:
        绝对 Path；逃逸或指向根自身时返回 None。
    """
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target == root_resolved or root_resolved not in target.parents:
        return None
    return target


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
        httpx.HTTPError: 网络异常（由调用方归一化）。
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
    """取当前有效凭证（缓存命中直接用，否则登录并缓存）。

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
                    file_part: tuple[str, tuple[str, bytes, str]] | None,
                    timeout_s: float) -> httpx.Response:
    """提交算法运行（JSON 或 multipart 二选一）。

    Args:
        client: HTTP 客户端。
        base_url: 服务地址（已去尾斜杠）。
        token: 访问凭证。
        payload: AlgorithmRunCreate 请求体。
        file_part: 文件上传时为 (字段名, (文件名, 内容, MIME))；纯 JSON 时为 None。
        timeout_s: 本次请求读超时。

    Returns:
        上游响应。
    """
    headers = {"Authorization": f"Bearer {token}"}
    if file_part is None:
        return await client.post(f"{base_url}{RUNS_PATH}", headers=headers,
                                 json=payload, timeout=timeout_s)
    field, upload = file_part
    return await client.post(
        f"{base_url}{RUNS_PATH}:multipart", headers=headers,
        data={"payload": json.dumps(payload, ensure_ascii=False)},
        files={field: upload}, timeout=timeout_s)


async def _run_algorithm(ctx: ToolContext, algorithm_id: str,
                         input_snapshot: dict,
                         file_part: tuple[str, tuple[str, bytes, str]] | None = None,
                         ) -> ToolResult:
    """调用 Poly_Agent 垂类算法并归一化为工具结果。

    上游创建接口同步阻塞执行：正常响应即终态（completed/failed）；若上游
    返回 queued/running（部署侧改为异步时），兜底轮询详情接口直至终态。

    Args:
        ctx: 工具上下文（取插件配置）。
        algorithm_id: 上游算法 id。
        input_snapshot: 算法输入快照（字段名与上游 input_schema 一致）。
        file_part: multipart 文件部分（Raman 光谱）；纯 JSON 调用为 None。

    Returns:
        ToolResult：成功时 content 为 output_summary 的 JSON 文本、data 带
        run_id；失败时 error 为 poly_agent_unconfigured / timeout /
        connection_error / unauthorized / http_error / upstream_error。
    """
    config = _config(ctx)
    base_url = str(config.get("base_url") or "").rstrip("/")
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
    if not base_url or not username or not password:
        return _unconfigured()
    deadline = time.monotonic() + REQUEST_TIMEOUT_S
    try:
        async with _make_client() as client:
            token = await _ensure_token(client, base_url, username, password)
            resp = await _post_run(client, base_url, token, {
                "algorithm_id": algorithm_id,
                "input_snapshot": input_snapshot,
            }, file_part, REQUEST_TIMEOUT_S)
            if resp.status_code in (401, 403):
                # 凭证可能在缓存期内被上游吊销：强制重登后重试一次
                _clear_token()
                token = await _ensure_token(client, base_url, username, password)
                resp = await _post_run(client, base_url, token, {
                    "algorithm_id": algorithm_id,
                    "input_snapshot": input_snapshot,
                }, file_part, REQUEST_TIMEOUT_S)
            if resp.status_code in (401, 403):
                return ToolResult(
                    ok=False,
                    content=("访问凭证无效（重登后仍被拒绝）。请核对插件配置中的"
                             "Poly_Agent 账号密码，或确认该账号未被停用。"),
                    error="unauthorized",
                )
            if resp.status_code != 200:
                return ToolResult(
                    ok=False,
                    content=f"高分子预测服务返回 {resp.status_code}: {resp.text[:300]}",
                    error="http_error",
                )
            try:
                body = resp.json() or {}
            except ValueError:
                return ToolResult(
                    ok=False, content=f"高分子预测服务返回非 JSON: {resp.text[:200]}",
                    error="upstream_error")
            if body.get("code") != 0:
                detail = body.get("data")
                if isinstance(detail, dict):
                    detail = detail.get("detail")
                return ToolResult(
                    ok=False,
                    content=("高分子预测服务错误: "
                             + str(detail or body.get("message") or body.get("code"))),
                    error="upstream_error",
                )
            data = body.get("data")
            if not isinstance(data, dict) or not data.get("run_id"):
                return ToolResult(
                    ok=False, content=f"高分子预测服务返回结构异常: {str(data)[:200]}",
                    error="upstream_error")
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
        return ToolResult(ok=False, content=f"高分子预测服务响应超时: {exc}",
                          error="timeout")
    except httpx.ConnectError as exc:
        return ToolResult(ok=False, content=f"无法连接高分子预测服务: {exc}",
                          error="connection_error")
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"高分子预测服务请求失败: {exc}",
                          error="http_error")
    except RuntimeError as exc:
        return ToolResult(ok=False, content=str(exc), error="unauthorized")


def _result_from_run(run_id: str, status: str, data: dict) -> ToolResult:
    """把终态运行记录整理为工具结果。

    Args:
        run_id: 上游运行 id。
        status: 运行状态（completed/failed/其余视为仍在执行）。
        data: 上游运行详情。

    Returns:
        成功时 content 为 output_summary JSON；失败/超时给出可操作文案。
    """
    if status == "failed":
        return ToolResult(
            ok=False,
            content=("算法执行失败："
                     + _error_text(data.get("error"), "上游未给出失败原因")),
            data={"run_id": run_id, "status": status},
            error="upstream_error",
        )
    if status != "completed":
        return ToolResult(
            ok=False,
            content=f"算法仍在执行（status={status or 'unknown'}），已等待超时；"
                    f"可稍后用运行 id {run_id} 到 Poly_Agent 平台查看结果。",
            data={"run_id": run_id, "status": status},
            error="timeout",
        )
    output = data.get("output_summary")
    if not isinstance(output, dict) or not output:
        return ToolResult(
            ok=False,
            content=f"算法执行完成但未返回结果: {str(data)[:300]}",
            data={"run_id": run_id, "status": status},
            error="upstream_error",
        )
    return ToolResult(
        ok=True,
        content=json.dumps(output, ensure_ascii=False, indent=1),
        data={"run_id": run_id, "status": status},
    )


def _upload_file_part(ctx: ToolContext, args: dict) -> tuple[str, tuple[str, bytes, str]] | ToolResult:
    """把 path 参数解析为工作区内的光谱文件并组装 multipart 文件部分。

    Args:
        ctx: 工具上下文（取 workspace_root）。
        args: 工具参数（取 path）。

    Returns:
        文件部分 (字段名, (文件名, 内容, MIME))；参数/路径/大小不合规时返回
        ToolResult 错误（no_workspace / bad_request）。
    """
    rel = _str_arg(args, "path").strip()
    if not rel:
        return ToolResult(ok=False, content="缺少参数 path（工作区内光谱文件路径）",
                          error="bad_request")
    root_str = ctx.workspace_root
    if root_str is None:
        return ToolResult(ok=False, content="当前会话未挂载工作区，无法读取光谱文件",
                          error="no_workspace")
    target = _safe_path(Path(root_str), rel)
    if target is None or not target.is_file():
        return ToolResult(ok=False, content=f"光谱文件不存在或路径非法: {rel}",
                          error="bad_request")
    size = target.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        return ToolResult(
            ok=False,
            content=f"光谱文件过大（{size} 字节，上游上限 10MiB）",
            error="bad_request")
    try:
        content = target.read_bytes()
    except OSError as exc:
        return ToolResult(ok=False, content=f"读取光谱文件失败: {exc}",
                          error="bad_request")
    mime = SPECTRUM_MIME.get(target.suffix.lower(), "application/octet-stream")
    return ("spectrum_file", (target.name, content, mime))


@tool(
    name="poly.tg_predict",
    description=(
        "聚酰亚胺 Tg 预测：给定单体/重复单元 SMILES，预测玻璃化转变温度（°C）。"
        "单次只接受一个 SMILES；多个分子请多次调用。上游模型推理约 1 分钟，"
        "调用后耐心等待，不要重复提交同一请求。"
    ),
    parameters={"type": "object", "properties": {
        "smiles": {"type": "string", "description": "单体/重复单元 SMILES（单个）"},
    }, "required": ["smiles"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def poly_tg_predict(ctx: ToolContext, args: dict) -> ToolResult:
    """聚酰亚胺玻璃化转变温度预测（SMILES → Tg）。"""
    return await _run_algorithm(
        ctx, "PI_Tg_predictor", {"smiles": _str_arg(args, "smiles")})


@tool(
    name="poly.electrolyte_predict",
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
    parameters={"type": "object", "properties": {
        "formulations": {"type": "array", "items": {"type": "object"},
                         "description": "配方对象列表（字段见工具描述）"},
        "recommendation_limit": {"type": "number", "default": 5,
                                 "description": "推荐配方数量上限"},
        "recommendation_task_type": {"type": "string", "default": "",
                                     "description": "推荐任务类型约束（可选）"},
    }, "required": ["formulations"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def poly_electrolyte_predict(ctx: ToolContext, args: dict) -> ToolResult:
    """氟基电解质配方性能预测与配方推荐。"""
    snapshot: dict = {"formulations": args.get("formulations") or []}
    limit = args.get("recommendation_limit")
    if limit is not None:
        snapshot["recommendation_limit"] = limit
    task_type = _str_arg(args, "recommendation_task_type")
    if task_type:
        snapshot["recommendation_task_type"] = task_type
    return await _run_algorithm(
        ctx, "fluorinated_electrolyte_model_2", snapshot)


@tool(
    name="poly.raman_analyze",
    description=(
        "Raman 光谱结构解析：上传工作区内的光谱文件（.txt/.dat/.csv/.xlsx，"
        "x-y 两列数据），解析候选结构并给出得分。可选指定分析区间 [x0, x1] 与"
        "候选数 k。文件较大时解析较慢，调用后耐心等待。"
    ),
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "工作区内光谱文件的相对路径"},
        "spectype": {"type": "string", "enum": ["raman"], "default": "raman",
                     "description": "谱图类型（当前仅支持 raman）"},
        "mode": {"type": "string", "enum": ["function_groups"],
                 "default": "function_groups", "description": "解析模式"},
        "x0": {"type": "number", "description": "分析区间起点（波数，可选）"},
        "x1": {"type": "number", "description": "分析区间终点（波数，可选）"},
        "k": {"type": "integer", "default": 3, "description": "候选结构数量"},
        "transmittance": {"type": "boolean", "default": False,
                          "description": "输入是否为透过率数据"},
        "device": {"type": "string", "enum": ["cpu", "cuda"], "default": "cpu",
                   "description": "推理设备"},
    }, "required": ["path"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def poly_raman_analyze(ctx: ToolContext, args: dict) -> ToolResult:
    """Raman 光谱解析（工作区光谱文件 → 候选结构）。"""
    file_part = _upload_file_part(ctx, args)
    if isinstance(file_part, ToolResult):
        return file_part
    snapshot: dict = {
        "spectype": _str_arg(args, "spectype", "raman"),
        "mode": _str_arg(args, "mode", "function_groups"),
        "k": args.get("k") if isinstance(args.get("k"), int) else 3,
        "transmittance": bool(args.get("transmittance")),
        "device": _str_arg(args, "device", "cpu"),
    }
    for key in ("x0", "x1"):
        if isinstance(args.get(key), (int, float)):
            snapshot[key] = args[key]
    return await _run_algorithm(
        ctx, "raman_structure_analyzer", snapshot, file_part=file_part)


@tool(
    name="poly.reactivity_fit",
    description=(
        "共聚竞聚率拟合：输入单体投料比与两单体转化率的实验数据行，基于 IUPAC "
        "终端模型网格搜索拟合竞聚率 r1、r2，并计算 95% 联合置信区间。"
        "data_rows 为对象数组，每行字段：比例（投料比，如 \"2:8\"）、"
        "转化率1（单体 1 转化率，如 \"2.53%\"）、转化率2（单体 2 转化率）。"
        "至少需要多组不同投料比的数据，行数过少拟合不可靠。"
    ),
    parameters={"type": "object", "properties": {
        "data_rows": {"type": "array", "items": {"type": "object"},
                      "description": "实验数据行列表（字段见工具描述）"},
        "delta_f_default": {"type": "number", "default": 0.01,
                            "description": "单体 1 摩尔分数增量步长"},
    }, "required": ["data_rows"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def poly_reactivity_fit(ctx: ToolContext, args: dict) -> ToolResult:
    """共聚竞聚率拟合（实验数据行 → r1/r2 与置信区间）。"""
    snapshot: dict = {"data_rows": args.get("data_rows") or []}
    delta = args.get("delta_f_default")
    if isinstance(delta, (int, float)):
        snapshot["delta_f_default"] = delta
    return await _run_algorithm(ctx, "reactivity_ratio_fitter", snapshot)


@tool(
    name="poly.silicon_bond",
    description=(
        "Silicon Bond7 谱图集成评分：输入样品对象（五条对齐光谱 + 组成信息，"
        "具体结构按上游模型约定），输出七个键级的 LSU 得分。不做结构推断与"
        "聚合物状态判断，仅输出键级评分。sample 对象结构不确定时先向用户确认"
        "或让对方提供样例，不要自行编造字段。"
    ),
    parameters={"type": "object", "properties": {
        "sample": {"type": "object",
                   "description": "样品对象（对齐光谱 + 组成，原样透传上游）"},
    }, "required": ["sample"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def poly_silicon_bond(ctx: ToolContext, args: dict) -> ToolResult:
    """硅键谱图集成评分（样品对象 → 七键级 LSU 得分）。"""
    sample = args.get("sample")
    if not isinstance(sample, dict):
        return ToolResult(ok=False, content="缺少参数 sample（样品对象）",
                          error="bad_request")
    return await _run_algorithm(
        ctx, "silicon_bond7_ssl_ensemble_seed0", {"sample": sample})
