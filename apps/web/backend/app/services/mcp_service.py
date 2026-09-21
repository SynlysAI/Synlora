"""用户级 Streamable HTTP MCP 连接、工具发现与远程调用。"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from cryptography.fernet import InvalidToken

from app.core.crypto import decrypt_key, encrypt_key

MCP_COLLECTION = "mcp_connections"
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"
MCP_ID_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def mcp_doc_id(user_id: str, mcp_id: str) -> str:
    """生成用户 MCP 文档主键。"""
    return f"{user_id}:{mcp_id}"


class McpService:
    """MCP 连接配置与 HTTP JSON-RPC 客户端。"""

    def __init__(self, store: Any, fernet_key: str,
                 client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        """保存存储、密钥与 HTTP 客户端工厂。

        Args:
            store: DocumentStore 实例。
            fernet_key: Fernet key；空表示开发环境明文存储。
            client_factory: 测试或定制 HTTP 客户端工厂。
        """
        self._store = store
        self._fernet_key = fernet_key
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(30.0)))

    async def list_for_user(self, user_id: str) -> list[dict]:
        """列出用户的 MCP 连接，不回传任何密钥明文。"""
        docs = await self._store.list(
            MCP_COLLECTION, filters={"user_id": user_id},
            sort=[("updated_at", -1)])
        return [self._public(doc) for doc in docs]

    async def get(self, user_id: str, mcp_id: str) -> dict | None:
        """读取一个用户 MCP 的安全视图。"""
        doc = await self._store.get(MCP_COLLECTION, mcp_doc_id(user_id, mcp_id))
        if doc is None or doc.get("user_id") != user_id:
            return None
        return self._public(doc)

    async def create(self, user_id: str, payload: dict) -> dict:
        """创建用户 MCP 连接。"""
        mcp_id = self._validate_id(payload.get("id"))
        doc_id = mcp_doc_id(user_id, mcp_id)
        if await self._store.get(MCP_COLLECTION, doc_id) is not None:
            raise ValueError(f"MCP 已存在: {mcp_id}")
        body = self._build_body(user_id, mcp_id, payload, current=None)
        doc = await self._store.insert(
            MCP_COLLECTION,
            {"_id": doc_id, **body, "created_at": time.time()},
        )
        return self._public(doc)

    async def update(self, user_id: str, mcp_id: str, payload: dict) -> dict:
        """更新用户 MCP 连接，空密钥字段表示保持原值。"""
        self._validate_id(mcp_id)
        doc_id = mcp_doc_id(user_id, mcp_id)
        current = await self._store.get(MCP_COLLECTION, doc_id)
        if current is None or current.get("user_id") != user_id:
            raise KeyError(mcp_id)
        body = self._build_body(user_id, mcp_id, payload, current=current)
        updated = await self._store.update(MCP_COLLECTION, doc_id, body)
        return self._public(updated or {**current, **body})

    async def delete(self, user_id: str, mcp_id: str) -> bool:
        """删除用户自己的 MCP 连接。"""
        doc_id = mcp_doc_id(user_id, mcp_id)
        current = await self._store.get(MCP_COLLECTION, doc_id)
        if current is None or current.get("user_id") != user_id:
            return False
        return await self._store.delete(MCP_COLLECTION, doc_id)

    async def resolved(self, user_id: str, mcp_id: str) -> dict:
        """读取运行时使用的完整解密配置。"""
        doc = await self._store.get(MCP_COLLECTION, mcp_doc_id(user_id, mcp_id))
        if doc is None or doc.get("user_id") != user_id:
            raise KeyError(mcp_id)
        out = self._public(doc)
        try:
            out["headers"] = json.loads(self._decrypt(doc.get("headers_secret"))) \
                if doc.get("headers_secret") else {}
            out["bearer_token"] = self._decrypt(doc.get("bearer_token_secret"))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MCP {mcp_id} 配置解密失败，请重新填写凭证") from exc
        return out

    async def discover_tools(self, user_id: str, mcp_id: str,
                             persist: bool = False) -> list[dict]:
        """连接 MCP 并读取工具列表。"""
        config = await self.resolved(user_id, mcp_id)
        result = await self._request(config, "tools/list", {})
        tools = result.get("tools") or []
        normalized = [self._normalize_tool(item) for item in tools if isinstance(item, dict)]
        if persist:
            await self._store.update(
                MCP_COLLECTION,
                mcp_doc_id(user_id, mcp_id),
                {
                    "tools": normalized,
                    "status": "connected",
                    "last_error": "",
                    "checked_at": time.time(),
                    "updated_at": time.time(),
                },
            )
        return normalized

    async def test_connection(self, user_id: str, mcp_id: str) -> dict:
        """测试连接并持久化工具快照与状态。"""
        try:
            tools = await self.discover_tools(user_id, mcp_id, persist=True)
        except Exception as exc:
            await self._store.update(
                MCP_COLLECTION,
                mcp_doc_id(user_id, mcp_id),
                {
                    "status": "error",
                    "last_error": str(exc),
                    "checked_at": time.time(),
                    "updated_at": time.time(),
                },
            )
            raise
        return {"ok": True, "tools": tools}

    async def call_tool(self, user_id: str, mcp_id: str,
                        tool_name: str, arguments: dict) -> dict:
        """调用远程 MCP 工具。"""
        config = await self.resolved(user_id, mcp_id)
        if config.get("enabled") is not True:
            raise ValueError(f"MCP 已停用: {mcp_id}")
        return await self._request(
            config,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def runtime_connections(self, user_id: str,
                                  mcp_ids: list[str]) -> list[dict]:
        """解析本轮专家引用的已启用 MCP 与工具快照。

        Args:
            user_id: 用户 sub。
            mcp_ids: 专家引用的 MCP ID。

        Returns:
            解密配置列表，每项包含 tools；不存在或停用的连接被跳过。
        """
        out: list[dict] = []
        for mcp_id in dict.fromkeys(mcp_ids):
            try:
                config = await self.resolved(user_id, mcp_id)
            except (KeyError, RuntimeError):
                continue
            if config.get("enabled") is not True:
                continue
            tools = list(config.get("tools") or [])
            if not tools:
                try:
                    tools = await self.discover_tools(user_id, mcp_id, persist=True)
                except Exception:
                    continue
            config["tools"] = tools
            out.append(config)
        return out

    async def _request(self, config: dict, method: str, params: dict) -> dict:
        """优先使用 2026-07-28 无状态协议，失败时兼容旧握手协议。"""
        try:
            return await self._modern_request(config, method, params)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return await self._legacy_request(config, method, params)

    async def _modern_request(self, config: dict, method: str, params: dict) -> dict:
        request_id = uuid.uuid4().hex
        headers = self._headers(config)
        headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        headers["Mcp-Method"] = method
        if isinstance(params.get("name"), str):
            headers["Mcp-Name"] = params["name"]
        body_params = dict(params)
        body_params.setdefault("_meta", {})[
            "io.modelcontextprotocol/clientInfo"
        ] = {"name": "Synlora", "version": "0.14"}
        payload = {
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": body_params,
        }
        async with self._client_factory() as client:
            response = await client.post(config["url"], headers=headers, json=payload)
        return self._parse_response(response, request_id)

    async def _legacy_request(self, config: dict, method: str, params: dict) -> dict:
        headers = self._headers(config)
        initialize_id = uuid.uuid4().hex
        initialize = {
            "jsonrpc": "2.0", "id": initialize_id, "method": "initialize",
            "params": {
                "protocolVersion": MCP_LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Synlora", "version": "0.14"},
            },
        }
        async with self._client_factory() as client:
            response = await client.post(config["url"], headers=headers, json=initialize)
            self._parse_response(response, initialize_id)
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            await client.post(
                config["url"], headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            request_id = uuid.uuid4().hex
            response = await client.post(
                config["url"], headers=headers,
                json={
                    "jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params,
                },
            )
        return self._parse_response(response, request_id)

    @staticmethod
    def _parse_response(response: httpx.Response, request_id: str) -> dict:
        """解析 JSON 或 SSE 形式的 JSON-RPC 响应。"""
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            messages = []
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    messages.append(json.loads(line[5:].strip()))
            payload = next(
                (item for item in messages if str(item.get("id")) == str(request_id)),
                messages[-1] if messages else {},
            )
        else:
            payload = response.json() if response.content else {}
        if not isinstance(payload, dict):
            raise RuntimeError("MCP 返回了非法响应")
        if payload.get("error"):
            error = payload["error"]
            raise RuntimeError(str(error.get("message") if isinstance(error, dict) else error))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP 响应缺少 result")
        return result

    def _build_body(self, user_id: str, mcp_id: str, payload: dict,
                    current: dict | None) -> dict:
        name = str(payload.get("name") or (current or {}).get("name") or "").strip()
        url = str(payload.get("url") or (current or {}).get("url") or "").strip()
        if not name:
            raise ValueError("MCP 名称不能为空")
        if not url.startswith(("http://", "https://")):
            raise ValueError("MCP 地址必须是 http:// 或 https://")
        headers_secret = (current or {}).get("headers_secret")
        if "headers" in payload:
            raw_headers = payload.get("headers") or {}
            if not isinstance(raw_headers, dict):
                raise ValueError("headers 必须是对象")
            clean_headers = {
                str(key).strip(): str(value)
                for key, value in raw_headers.items() if str(key).strip()
            }
            if current is not None:
                existing_headers = self._stored_headers(current)
                clean_headers = {
                    key: (value if value else existing_headers.get(key, value))
                    for key, value in clean_headers.items()
                }
            headers_secret = self._encrypt(json.dumps(clean_headers, ensure_ascii=False))
        bearer_secret = (current or {}).get("bearer_token_secret")
        bearer = payload.get("bearer_token")
        if bearer is not None and str(bearer).strip():
            bearer_secret = self._encrypt(str(bearer).strip())
        return {
            "user_id": user_id,
            "mcp_id": mcp_id,
            "name": name,
            "description": str(payload.get(
                "description", (current or {}).get("description") or "")),
            "transport": "streamable-http",
            "url": url,
            "headers_secret": headers_secret,
            "bearer_token_secret": bearer_secret,
            "enabled": bool(payload.get(
                "enabled", (current or {}).get("enabled", True))),
            "tools": list((current or {}).get("tools") or []),
            "status": str((current or {}).get("status") or "unchecked"),
            "last_error": str((current or {}).get("last_error") or ""),
            "checked_at": (current or {}).get("checked_at"),
            "updated_at": time.time(),
        }

    def _public(self, doc: dict) -> dict:
        return {
            "id": str(doc.get("mcp_id") or ""),
            "name": str(doc.get("name") or ""),
            "description": str(doc.get("description") or ""),
            "transport": "streamable-http",
            "url": str(doc.get("url") or ""),
            "enabled": doc.get("enabled") is True,
            "status": str(doc.get("status") or "unchecked"),
            "last_error": str(doc.get("last_error") or ""),
            "checked_at": doc.get("checked_at"),
            "tools": list(doc.get("tools") or []),
            "header_names": self._header_names(doc),
            "bearer_token_set": bool(doc.get("bearer_token_secret")),
        }

    def _header_names(self, doc: dict) -> list[str]:
        return sorted(self._stored_headers(doc))

    def _stored_headers(self, doc: dict) -> dict[str, str]:
        """读取已存 Header，供更新时保留未重新填写的值。"""
        try:
            raw = self._decrypt(doc.get("headers_secret"))
            data = json.loads(raw) if raw else {}
            return {
                str(key): str(value)
                for key, value in data.items()
                if str(key).strip()
            } if isinstance(data, dict) else {}
        except (InvalidToken, ValueError, json.JSONDecodeError):
            return {}

    def _headers(self, config: dict) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **{str(key): str(value) for key, value in config.get("headers", {}).items()},
        }
        if config.get("bearer_token"):
            headers["Authorization"] = f"Bearer {config['bearer_token']}"
        return headers

    def _encrypt(self, value: str) -> dict:
        stored, encrypted = encrypt_key(value, self._fernet_key)
        return {"value": stored, "encrypted": encrypted}

    def _decrypt(self, item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        return decrypt_key(
            str(item.get("value") or ""), self._fernet_key,
            item.get("encrypted") is True)

    @staticmethod
    def _validate_id(value: Any) -> str:
        mcp_id = str(value or "").strip()
        if not MCP_ID_OK.fullmatch(mcp_id):
            raise ValueError("MCP ID 必须是 kebab-case")
        return mcp_id

    @staticmethod
    def _normalize_tool(item: dict) -> dict:
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("MCP 工具缺少 name")
        schema = item.get("inputSchema") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        return {
            "name": name,
            "description": str(item.get("description") or name),
            "input_schema": schema,
        }
