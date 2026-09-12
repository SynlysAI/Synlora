"""WeKnora 知识库服务：列表代理与知识检索（knowledge.search 工具的后端侧配套）。

WeKnora v0.7.1 API（X-API-Key 头鉴权，base 含 /api/v1）：
- GET  /knowledge-bases                        知识库列表（自带 knowledge_count；chunk_count 恒 0 不用）
- POST /knowledge-bases/{id}/hybrid-search     混合检索（推荐；body 的 knowledge_base_ids 可跨库）
"""
from __future__ import annotations

import httpx

# 连接超时：内网服务，5s 连接 / 20s 读取（检索含 embedding 计算可较慢）
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class WeKnoraError(Exception):
    """WeKnora 调用失败（未配置/网络错误/非 200）。"""


class WeKnoraService:
    """WeKnora 客户端（单例，挂 app.state.weknora_service；未配置时方法抛 WeKnoraError）。"""

    def __init__(self, base_url: str, api_key: str) -> None:
        """保存连接配置。

        Args:
            base_url: 服务地址（含 /api/v1）。
            api_key: X-API-Key。
        """
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        """是否已配置可用。"""
        return bool(self._base and self._api_key)

    async def _raw(self, client: httpx.AsyncClient, method: str, path: str,
                   json: dict | None = None) -> dict:
        """在给定客户端上发请求（鉴权头 + 错误归一）。

        Args:
            client: 复用的 httpx 客户端。
            method: HTTP 方法。
            path: 相对 /api/v1 的路径（可带 query）。
            json: 请求体。

        Returns:
            响应 JSON。

        Raises:
            WeKnoraError: 未配置/网络失败/非 200。
        """
        if not self.configured:
            raise WeKnoraError("WeKnora 未配置（WEKNORA_BASE_URL / WEKNORA_API_KEY）")
        try:
            resp = await client.request(
                method, f"{self._base}{path}",
                headers={"X-API-Key": self._api_key}, json=json,
            )
        except httpx.HTTPError as exc:
            raise WeKnoraError(f"WeKnora 请求失败: {exc}") from exc
        if resp.status_code != 200:
            raise WeKnoraError(f"WeKnora 返回 {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        """单次请求（自建客户端，复用 _raw 的鉴权与错误归一）。

        Args:
            method: HTTP 方法。
            path: 相对 /api/v1 的路径。
            json: 请求体。

        Returns:
            响应 JSON。

        Raises:
            WeKnoraError: 未配置/网络失败/非 200。
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            return await self._raw(client, method, path, json)

    async def list_kbs(self) -> list[dict]:
        """知识库列表（归一为 id/name/description/doc_count）。

        文档数用列表自带的 knowledge_count（chunk_count 在 v0.7.1 恒为 0）。

        Returns:
            知识库字典列表（按服务端返回序）。
        """
        data = (await self._request("GET", "/knowledge-bases")).get("data") or []
        return [{
            "id": kb.get("id", ""),
            "name": kb.get("name", ""),
            "description": kb.get("description", ""),
            "doc_count": kb.get("knowledge_count"),
        } for kb in data]

    async def search(self, query: str, kb_ids: list[str], top_k: int = 8) -> list[dict]:
        """混合检索（v0.7.1 推荐；跨库靠 body 的 knowledge_base_ids）。

        Args:
            query: 检索文本。
            kb_ids: 知识库 id 列表（非空）。
            top_k: 返回片段数上限（服务端 match_count）。

        Returns:
            命中片段列表。
        """
        data = (await self._request(
            "POST", f"/knowledge-bases/{kb_ids[0]}/hybrid-search",
            json={
                "query_text": query,
                "match_count": top_k,
                "knowledge_base_ids": kb_ids,
            },
        )).get("data") or []
        return data[:top_k]
