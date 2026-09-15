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

import logging
import time
from typing import Any

from cryptography.fernet import InvalidToken

from app.core.crypto import decrypt_key, encrypt_key

CONFIG_COLLECTION = "plugin_configs"

logger = logging.getLogger(__name__)


def user_doc_id(user_id: str, plugin_id: str) -> str:
    """用户维度配置的文档 id。

    Args:
        user_id: 用户 sub。
        plugin_id: 插件 id。

    Returns:
        f"user:{user_id}:{plugin_id}"（与公共配置的 `_id = plugin_id` 区分）。
    """
    return f"user:{user_id}:{plugin_id}"


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

        Raises:
            RuntimeError: 密文解密失败（FERNET_KEY 变更或缺失，需重新填写凭证）。
        """
        doc = await self.get_doc(plugin_id)
        if doc is None:
            return {}
        return self._resolve_doc(doc)

    async def resolved_for_user(self, user_id: str, plugin_id: str) -> dict:
        """用户视角的插件配置（公共配置打底 + 个人配置覆盖）。

        Args:
            user_id: 用户 sub。
            plugin_id: 插件 id。

        Returns:
            浅合并后的扁平配置；两者都无记录时为空 dict。

        Raises:
            RuntimeError: 任一记录密文解密失败（FERNET_KEY 变更或缺失）。
        """
        out: dict = {}
        public = await self.get_doc(plugin_id)
        if public is not None:
            out.update(self._resolve_doc(public))
        personal = await self.get_doc(user_doc_id(user_id, plugin_id))
        if personal is not None:
            out.update(self._resolve_doc(personal))
        return out

    async def all_resolved(self) -> dict[str, dict]:
        """全部已安装插件的解密配置（运行期注入 ctx.extra 用）。

        单条解密失败（key 轮换/记录畸形）只告警并跳过该插件——该插件工具后续会返回
        "未配置"错误（明确的用户可见结果），不应让整轮对话崩掉。

        Returns:
            {插件 id: 扁平配置}，不可解密/畸形的插件被剔除。
        """
        out: dict[str, dict] = {}
        for doc in await self._store.list(CONFIG_COLLECTION):
            pid = doc.get("_id")
            try:
                out[pid] = self._resolve_doc(doc)
            except RuntimeError as exc:
                logger.warning("插件 %s 配置解密失败，已跳过运行期注入：%s", pid, exc)
        return out

    def _resolve_doc(self, doc: dict) -> dict:
        """解密单个配置文档为扁平 dict（resolved / all_resolved 共用）。

        Args:
            doc: 插件配置原始文档。

        Returns:
            扁平配置 dict（畸形的 secrets 项直接跳过）。

        Raises:
            RuntimeError: 密文解密失败（FERNET_KEY 变更或缺失）。
        """
        out: dict = dict(doc.get("config") or {})
        for key, item in (doc.get("secrets") or {}).items():
            if not isinstance(item, dict):
                logger.warning("插件 %s 的敏感字段 %s 结构畸形，已跳过", doc.get("_id"), key)
                continue
            try:
                out[key] = decrypt_key(
                    item.get("value", ""), self._fernet_key, item.get("encrypted") is True)
            except (InvalidToken, ValueError) as exc:
                # key 轮换/缺失（如加密数据配空 key 时 Fernet 构造抛 ValueError）统一转友好错误，
                # 与 ProviderRepo.get_decrypted 口径一致，避免底层异常裸抛到 API 层。
                raise RuntimeError(
                    f"插件 {doc.get('_id')} 配置解密失败：FERNET_KEY 是否已更换？"
                    "请到插件页重新填写凭证") from exc
        return out

    async def save(self, plugin_id: str, values: dict, schema: list[dict],
                   user_id: str | None = None) -> dict:
        """保存配置：按 schema 声明的 key 白名单过滤后写入。

        未在 schema 中声明的 key 一律忽略（记 warning），避免 UI 传参差异被升级为 500；
        这同时是敏感字段的唯一护栏——漏传 schema 时敏感值被丢弃而非明文落库（fail-closed）。
        敏感字段留空（含纯空白）表示保持原值；当前不提供清除路径，需清除时直接删插件配置记录。

        Args:
            plugin_id: 插件 id（首次保存即视为安装）。
            values: 页面提交的字段值。
            schema: 插件配置 schema（既是字段白名单，也决定哪些字段是敏感的）。
            user_id: 用户 sub；None = 公共配置（现状），否则写用户维度的个人配置。

        Returns:
            保存后的原始文档。
        """
        doc_id = user_doc_id(user_id, plugin_id) if user_id else plugin_id
        declared = {f["key"] for f in schema}
        secret_keys = {f["key"] for f in schema if f.get("secret")}
        doc = await self.get_doc(doc_id) or {}
        config = dict(doc.get("config") or {})
        secrets = dict(doc.get("secrets") or {})
        for key, value in values.items():
            if key not in declared:
                logger.warning("插件 %s 收到 schema 未声明的字段 %s，已忽略", plugin_id, key)
                continue
            if key in secret_keys:
                if value is not None and str(value).strip():
                    stored, encrypted = encrypt_key(str(value), self._fernet_key)
                    secrets[key] = {"value": stored, "encrypted": encrypted}
            else:
                config[key] = value
        body = {"config": config, "secrets": secrets, "updated_at": time.time()}
        if doc:
            updated = await self._store.update(CONFIG_COLLECTION, doc_id, body)
            return updated or {**doc, **body}
        return await self._store.insert(
            CONFIG_COLLECTION, {"_id": doc_id, **body, "created_at": time.time()})

    async def save_for_user(self, user_id: str, plugin_id: str,
                            values: dict, schema: list[dict]) -> dict:
        """保存用户维度的插件配置（调用方不必自行拼 doc id）。

        Args:
            user_id: 用户 sub。
            plugin_id: 插件 id。
            values: 页面提交的字段值。
            schema: 插件配置 schema。

        Returns:
            保存后的原始文档。
        """
        return await self.save(plugin_id, values, schema, user_id=user_id)
