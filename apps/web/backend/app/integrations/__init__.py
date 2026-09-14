"""AI⁴MS 集成插件层：按配置把子平台能力注册为工具。

约定（可插拔）：每个子平台接入 = 一个模块，暴露带 `name` 属性与
`register(registry)` 方法的 provider 对象，内部用 harness 的 `@tool`
装饰器声明工具。未在 `AI4MS_PROVIDERS` 中启用的 provider 不注册——
工具对 LLM 与助手白名单校验都不可见（默认全关，避免误暴露）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from synlys_harness import ToolRegistry

    from app.core.settings import Settings

logger = logging.getLogger(__name__)


class IntegrationProvider(Protocol):
    """子平台接入插件接口。"""

    name: str

    def register(self, registry: "ToolRegistry") -> None:
        """把本子平台的工具注册进 registry。

        Args:
            registry: 目标注册表。
        """
        ...


def enabled_provider_names(settings: "Settings") -> list[str]:
    """解析 AI4MS_PROVIDERS 配置为 provider 名列表。

    Args:
        settings: 应用配置。

    Returns:
        去空白且同名去重（按首次出现顺序）的 provider 名列表；
        未配置时为空列表（默认全关）。
    """
    return list(dict.fromkeys(
        p.strip() for p in settings.ai4ms_providers.split(",") if p.strip()))


def _provider_table() -> dict[str, IntegrationProvider]:
    """provider 名 → 实例（延迟导入，未启用时零开销）。

    Returns:
        provider 名到实例的映射；尚无接入时为空表。
    """
    return {}


def register_providers(registry: "ToolRegistry", settings: "Settings") -> list[str]:
    """把已启用 provider 的工具注册进 registry。

    未知 provider 名跳过并告警（配置写错不阻断服务启动）。

    Args:
        registry: 目标注册表。
        settings: 应用配置（决定启用哪些 provider）。

    Returns:
        实际注册成功的 provider 名列表。
    """
    names = enabled_provider_names(settings)
    if not names:
        return []
    table = _provider_table()
    registered: list[str] = []
    for name in names:
        provider = table.get(name)
        if provider is None:
            logger.warning("未知的 AI4MS provider，已跳过: %s", name)
            continue
        provider.register(registry)
        registered.append(name)
    return registered
