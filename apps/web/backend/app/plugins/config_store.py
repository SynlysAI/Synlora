"""插件配置落库（敏感字段 Fernet 加密，留空表示保持原值）。

存储形态（集合 plugin_configs，_id = 插件 id）：
    {
      "_id": "spec_agent",
      "config": {"base_url": "http://..."},          # 非敏感字段明文
      "secrets": {"token": {"value": "<密文>", "encrypted": true}},
      "created_at": ..., "updated_at": ...,
    }
安装状态 = 存在配置记录（jiuwen 式：只认 installed，无全局开关）。
"""
from __future__ import annotations

import time
from typing import Any

from app.core.crypto import decrypt_key, encrypt_key

CONFIG_COLLECTION = "plugin_configs"


class PluginConfigStore:
    """插件配置的读写与解密。"""

    def __init__(self, store: Any, fernet_key: str) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
            fernet_key: Fernet key（空 = 明文存储，开发模式）。
        """
        self._store = store
        self._fernet_key = fernet_key

    async def get_doc(self, plugin_id: str) -> dict | None:
        """取插件配置原始文档。

        Args:
            plugin_id: 插件 id。

        Returns:
            配置文档；未安装时为 None。
        """
        return await self._store.get(CONFIG_COLLECTION, plugin_id)

    async def installed_ids(self) -> list[str]:
        """全部已安装（有配置记录）的插件 id。

        Returns:
            排序后的插件 id 列表。
        """
        docs = await self._store.list(CONFIG_COLLECTION)
        return sorted(d["_id"] for d in docs)

    async def resolved(self, plugin_id: str) -> dict:
        """解密后的扁平配置（非敏感明文 + 敏感字段解密值）。

        Args:
            plugin_id: 插件 id。

        Returns:
            扁平配置 dict；未安装时为空 dict。
        """
        doc = await self.get_doc(plugin_id)
        if doc is None:
            return {}
        out: dict = dict(doc.get("config") or {})
        for key, item in (doc.get("secrets") or {}).items():
            out[key] = decrypt_key(
                item.get("value", ""), self._fernet_key, bool(item.get("encrypted")))
        return out

    async def all_resolved(self) -> dict[str, dict]:
        """全部已安装插件的解密配置（运行期注入 ctx.extra 用）。

        Returns:
            {插件 id: 扁平配置}。
        """
        return {pid: await self.resolved(pid) for pid in await self.installed_ids()}

    async def save(self, plugin_id: str, values: dict, schema: list[dict]) -> dict:
        """保存配置：非敏感字段覆盖，敏感字段非空才更新（留空保持原值）。

        Args:
            plugin_id: 插件 id（首次保存即视为安装）。
            values: 页面提交的字段值。
            schema: 插件配置 schema（决定哪些字段是敏感的）。

        Returns:
            保存后的原始文档。
        """
        secret_keys = {f["key"] for f in schema if f.get("secret")}
        doc = await self.get_doc(plugin_id) or {}
        config = dict(doc.get("config") or {})
        secrets = dict(doc.get("secrets") or {})
        for key, value in values.items():
            if key in secret_keys:
                if value:
                    stored, encrypted = encrypt_key(str(value), self._fernet_key)
                    secrets[key] = {"value": stored, "encrypted": encrypted}
            else:
                config[key] = value
        body = {"config": config, "secrets": secrets, "updated_at": time.time()}
        if doc:
            updated = await self._store.update(CONFIG_COLLECTION, plugin_id, body)
            return updated or {**doc, **body}
        return await self._store.insert(
            CONFIG_COLLECTION, {"_id": plugin_id, **body, "created_at": time.time()})
