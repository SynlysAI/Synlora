"""Spec_Agent 谱图解析内置工具。

来源：Synlora 插件 catalog/plugins/spec_agent（tools.py 同步三件套平移；
connectors.py 的五类异步任务改造为同步内嵌轮询工具，见文末 spec_task_* 部分）。
认证只用静态 SPEC_AGENT_TOKEN（不迁移 ai4ms_token 代签机制）。
"""

import json
import os

import httpx
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool

CONNECT_TIMEOUT_S = 10.0
NMR_REQUEST_TIMEOUT_S = 360.0

# 测试注入 MockTransport 的钩子；None 时 httpx 使用默认传输
_transport: httpx.BaseTransport | None = None


def _settings() -> dict[str, str]:
    """读取 Spec_Agent 环境变量配置。"""
    return {
        "base_url": (os.getenv("SPEC_AGENT_BASE_URL") or "").strip().rstrip("/"),
        "token": (os.getenv("SPEC_AGENT_TOKEN") or "").strip(),
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


def _headers(settings: dict[str, str]) -> dict[str, str]:
    """构造请求头；未配置 token 时不发送认证头。"""
    return {"Authorization": f"Bearer {settings['token']}"} if settings["token"] else {}


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
            resp = await client.post(
                f"{settings['base_url']}/api/v1/nmrserver/{path}",
                headers=_headers(settings),
                json=payload,
            )
    except httpx.TimeoutException as exc:
        return _error("timeout", f"谱图服务响应超时: {exc}")
    except httpx.ConnectError as exc:
        return _error("connection_error", f"无法连接谱图服务: {exc}")
    except httpx.HTTPError as exc:
        return _error("http_error", f"谱图服务请求失败: {exc}")
    if resp.status_code in (401, 403):
        return _error(
            "unauthorized",
            "访问凭证无效或已过期：请核对部署配置中的 SPEC_AGENT_TOKEN（这不是参数错误，请勿原样重试）",
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
