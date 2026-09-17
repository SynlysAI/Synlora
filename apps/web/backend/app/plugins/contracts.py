"""外部科研任务连接器的宿主公共接口。"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from synlys_harness import JobStatus


class JobConnectorError(Exception):
    """外部任务连接器错误基类。"""


class JobSubmitFailed(JobConnectorError):
    """外部系统拒绝或未能接受任务。"""


class JobPollFailed(JobConnectorError):
    """外部任务状态查询失败，宿主应保留原状态。"""


@runtime_checkable
class JobConnector(Protocol):
    """外部科研服务任务适配器。"""

    kind: str
    plugin_id: str
    status_map: dict[str, JobStatus]

    async def submit(self, params: dict, ctx: dict) -> str:
        """向外部服务提交任务并返回外部任务 ID。"""
        ...

    async def poll(self, external_id: str, ctx: dict) -> str:
        """查询外部任务状态原文。"""
        ...

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """请求外部服务取消任务。"""
        ...


@dataclass
class RegisteredConnector:
    """外部连接器及其统一状态映射。"""

    connector: JobConnector
    status_map: dict[str, JobStatus] = field(default_factory=dict)

    def map_status(self, raw: str) -> JobStatus | None:
        """将外部状态原文映射为统一状态。"""
        if not isinstance(raw, str):
            return None
        return self.status_map.get(raw.strip().lower())


class JobConnectorRegistry:
    """外部任务 kind 到连接器的进程内注册表。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._items: dict[str, RegisteredConnector] = {}

    def register(
        self,
        connector: JobConnector,
        status_map: dict[str, JobStatus] | None = None,
    ) -> None:
        """校验并注册外部任务连接器。

        Args:
            connector: 外部任务连接器。
            status_map: 可选状态映射覆盖值。

        Raises:
            ValueError: 连接器不合法、占用保留命名空间或 kind 重复。
        """
        kind = str(getattr(connector, "kind", "")).strip()
        if not kind:
            raise ValueError("连接器 kind 不能为空")
        if kind == "sandbox" or kind.startswith("sandbox."):
            raise ValueError(f"任务类型使用了平台保留命名空间: {kind}")
        if not isinstance(connector, JobConnector):
            raise ValueError(
                f"连接器未实现 JobConnector 协议（需 kind/plugin_id/status_map 与 "
                f"submit/poll/cancel 三个异步方法）: {type(connector).__name__}"
            )
        for method in ("submit", "poll", "cancel"):
            if not inspect.iscoroutinefunction(getattr(connector, method)):
                raise ValueError(
                    f"连接器方法必须是 async def: {type(connector).__name__}.{method}"
                )
        if kind in self._items:
            raise ValueError(f"任务类型已注册: {kind}")
        source = status_map if status_map is not None else getattr(
            connector, "status_map", None
        )
        if not isinstance(source, dict):
            raise ValueError(
                f"连接器 {kind} 的状态映射必须是 dict（外部状态原文 → JobStatus），"
                f"实际为 {type(source).__name__}"
            )
        if not source:
            raise ValueError(
                f"连接器 {kind} 未提供状态映射（register 的 status_map 参数或"
                " connector.status_map 属性），缺映射会让任务状态永远映射不上"
            )
        normalized = {str(key).strip().lower(): value for key, value in source.items()}
        self._items[kind] = RegisteredConnector(connector, normalized)

    def get(self, kind: str) -> RegisteredConnector | None:
        """按 kind 查询连接器。"""
        return self._items.get(kind)

    @property
    def kinds(self) -> list[str]:
        """返回排序后的已注册外部任务类型。"""
        return sorted(self._items)
