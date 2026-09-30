import json
import re
import traceback
from typing import Any

import httpx

from yuxi.knowledge.implementations.read_only_connectors import ReadOnlyConnectors
from yuxi.knowledge.read_models import KnowledgeBaseConfig
from yuxi.utils import logger

WEKNORA_REQUIRED_PARAMS = ("weknora_api_url", "weknora_api_key", "weknora_kb_ids")

# 检索含 embedding 计算可较慢，连接 5s / 读取 20s
WEKNORA_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# search_mode 到 WeKnora 检索开关的映射；disable_* 缺省均为 False（即混合检索）
WEKNORA_SEARCH_MODE_SWITCHES = {
    "hybrid": {},
    "vector": {"disable_keywords_match": True},
    "keyword": {"disable_vector_match": True},
}


class WeKnoraKB(ReadOnlyConnectors):
    """基于 WeKnora Hybrid Search API 的只读检索知识库实现。

    WeKnora v0.7.x：X-API-Key 头鉴权，base 含 /api/v1。检索请求统一带
    ?resource_urls=public，结果中的 resource:// 内部存储引用会被替换为可直接
    访问的限时直链（含 image_info 内的图片地址）；直链有时效，消费方用完即弃，
    不可持久化到数据库或长期展示。跨库检索（knowledge_base_ids）要求各库使用
    相同的 embedding 模型。
    """

    kb_type = "weknora"
    name = "WeKnora"
    description = "连接腾讯 WeKnora 的只读检索知识库，支持跨库混合检索"

    def __init__(self, work_dir: str, **kwargs):
        del kwargs
        super().__init__(work_dir)

    @classmethod
    def get_create_params_config(cls) -> dict[str, Any]:
        return {
            "options": [
                {
                    "key": "weknora_api_url",
                    "label": "WeKnora API URL",
                    "type": "text",
                    "required": True,
                    "placeholder": "例如: http://127.0.0.1:9000/api/v1",
                    "description": "WeKnora API 地址，必须以 /api/v1 结尾",
                },
                {
                    "key": "weknora_api_key",
                    "label": "WeKnora API Key",
                    "type": "password",
                    "required": True,
                    "placeholder": "请输入 WeKnora X-API-Key",
                },
                {
                    "key": "weknora_kb_ids",
                    "label": "知识库 ID",
                    "type": "text",
                    "required": True,
                    "placeholder": "例如: kb_xxxxxxxx，多个用逗号分隔",
                    "description": "WeKnora 知识库 ID，逗号分隔多个可跨库检索；跨库要求各库使用相同的 embedding 模型",
                },
            ]
        }

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        params = dict(additional_params or {})
        missing_fields = [field for field in WEKNORA_REQUIRED_PARAMS if not params.get(field)]
        if missing_fields:
            raise ValueError(f"WeKnora 参数缺失: {', '.join(missing_fields)}")

        params["weknora_api_url"] = str(params.get("weknora_api_url") or "").strip().rstrip("/")
        params["weknora_api_key"] = str(params.get("weknora_api_key") or "").strip()
        if not params["weknora_api_url"].endswith("/api/v1"):
            raise ValueError("WeKnora api_url 必须以 /api/v1 结尾")

        params["weknora_kb_ids"] = cls._normalize_kb_ids(params.get("weknora_kb_ids"))
        if not params["weknora_kb_ids"]:
            raise ValueError("WeKnora 参数缺失: weknora_kb_ids")
        return params

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        agent_call: bool = False,
        **kwargs,
    ) -> list[dict]:
        """调用 WeKnora hybrid-search 执行检索并映射为统一结果结构。

        Args:
            query_text: 查询文本。
            kb_id: 数据库ID。
            config: 知识库运行配置。
            agent_call: 是否来自 Agent 调用（当前无行为差异）。
            **kwargs: 查询参数覆盖项。

        Returns:
            检索到的文档块列表，元素为 {content, score, metadata}。
        """
        del agent_call
        additional_params = config.additional_params
        api_url = str(additional_params.get("weknora_api_url") or "").strip()
        api_key = str(additional_params.get("weknora_api_key") or "").strip()
        remote_kb_ids = self._normalize_kb_ids(additional_params.get("weknora_kb_ids"))

        if not api_url or not api_key or not remote_kb_ids:
            logger.error(f"WeKnora config incomplete for kb_id={kb_id}")
            return []

        merged = {**config.query_options, **kwargs}

        search_mode = str(merged.get("search_mode", "hybrid")).lower()
        mode_switches = WEKNORA_SEARCH_MODE_SWITCHES.get(search_mode, {})

        match_count = int(merged.get("match_count", 8))
        match_count = max(match_count, 1)
        vector_threshold = float(merged.get("vector_threshold", 0.0) or 0.0)
        keyword_threshold = float(merged.get("keyword_threshold", 0.0) or 0.0)

        payload: dict[str, Any] = {
            "query_text": query_text,
            "match_count": match_count,
            "knowledge_base_ids": remote_kb_ids,
            **mode_switches,
        }
        if vector_threshold > 0:
            payload["vector_threshold"] = vector_threshold
        if keyword_threshold > 0:
            payload["keyword_threshold"] = keyword_threshold

        # 路径 :id 取第一个库；body 的 knowledge_base_ids 携带全量，服务端以 body 为准实现跨库
        request_url = f"{api_url}/knowledge-bases/{remote_kb_ids[0]}/hybrid-search?resource_urls=public"
        headers = {"X-API-Key": api_key}

        try:
            response_json = await self._request_weknora(client_payload=payload, request_url=request_url, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.error(f"WeKnora query failed for kb_id={kb_id}: {e}, {traceback.format_exc()}")
            return []

        records = response_json.get("data") if isinstance(response_json, dict) else None
        if not isinstance(records, list):
            return []

        results = []
        for record in records:
            if not isinstance(record, dict):
                continue
            content = record.get("content")
            if not content:
                continue

            results.append(
                {
                    "content": content,
                    "score": float(record.get("score") or 0.0),
                    "metadata": self._build_chunk_metadata(record),
                }
            )

        return results

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        del kb_id, kwargs
        options = [
            {
                "key": "search_mode",
                "label": "检索模式",
                "type": "select",
                "default": "hybrid",
                "options": [
                    {"value": "hybrid", "label": "混合检索", "description": "向量 + 关键词"},
                    {"value": "vector", "label": "向量检索", "description": "映射为 disable_keywords_match=true"},
                    {"value": "keyword", "label": "关键词检索", "description": "映射为 disable_vector_match=true"},
                ],
                "description": "WeKnora 检索模式映射",
            },
            {
                "key": "match_count",
                "label": "返回片段数",
                "type": "number",
                "default": 8,
                "min": 1,
                "max": 100,
                "description": "映射为 WeKnora match_count",
            },
            {
                "key": "vector_threshold",
                "label": "向量阈值（0-1）",
                "type": "number",
                "default": 0.0,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "description": "向量相似度阈值过滤，0 表示不过滤",
            },
            {
                "key": "keyword_threshold",
                "label": "关键词阈值（0-1）",
                "type": "number",
                "default": 0.0,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "description": "关键词得分阈值过滤，0 表示不过滤",
            },
        ]
        return {"type": "weknora", "options": options}

    @staticmethod
    async def _request_weknora(client_payload: dict[str, Any], request_url: str, headers: dict[str, str]) -> dict:
        """发送 WeKnora 检索请求，非 200 时记录响应体并抛出原始异常。

        Args:
            client_payload: hybrid-search 请求体。
            request_url: 完整请求地址。
            headers: 含 X-API-Key 的请求头。

        Returns:
            WeKnora 响应 JSON。

        Raises:
            httpx.HTTPError: 网络失败或非 200 响应。
        """
        async with httpx.AsyncClient(timeout=WEKNORA_TIMEOUT) as client:
            response = await client.post(request_url, json=client_payload, headers=headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # WeKnora 错误体形如 {"success": false, "error": {"code": ..., "message": ...}}
                body_preview = response.text[:1000] if response.text else ""
                logger.error(
                    f"WeKnora HTTP error: status={response.status_code}, url={request_url}, "
                    f"payload_keys={list(client_payload.keys())}, body={body_preview}"
                )
                raise e
            return response.json()

    @staticmethod
    def _normalize_kb_ids(raw: Any) -> list[str]:
        """把逗号/分号/空白分隔的字符串或列表归一化为去重保序的库 ID 列表。

        Args:
            raw: 原始输入（字符串或列表）。

        Returns:
            去重保序的库 ID 列表。
        """
        if raw is None:
            return []
        parts = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\s]+", str(raw))
        ids: list[str] = []
        for part in parts:
            value = str(part).strip()
            if value and value not in ids:
                ids.append(value)
        return ids

    @staticmethod
    def _build_chunk_metadata(record: dict) -> dict:
        """把 WeKnora 命中片段的原始字段映射为 Yuxi 统一的 metadata 结构。"""
        metadata = {
            "source": record.get("knowledge_filename") or record.get("knowledge_title") or "WeKnora",
            "file_id": record.get("knowledge_id"),
            "chunk_id": record.get("id"),
            "chunk_index": record.get("chunk_index"),
            "knowledge_title": record.get("knowledge_title"),
            "knowledge_source": record.get("knowledge_source"),
            "chunk_type": record.get("chunk_type"),
        }
        image_info = WeKnoraKB._parse_image_info(record.get("image_info"))
        if image_info is not None:
            metadata["image_info"] = image_info
        return {key: value for key, value in metadata.items() if value is not None}

    @staticmethod
    def _parse_image_info(raw: Any) -> dict | None:
        """解析 image_info 字段（WeKnora 返回 JSON 字符串），非字典或解析失败时返回 None。

        Args:
            raw: 原始 image_info 输入。

        Returns:
            解析后的字典，无法解析时为 None。
        """
        if raw is None or raw == "":
            return None
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
