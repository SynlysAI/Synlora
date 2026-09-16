"""后台任务连接器：宿主与各子平台（Spec_Agent 等）之间的适配协议。

分工：宿主 JobService 管状态机与持久化；连接器只管"怎么跟某个外部系统
说话"——提交、查状态、取消，以及把外部状态原文翻译成 harness 的统一状态。
插件在自己的包内实现连接器并在启动时注册（见 PluginService 扩展点）。
"""
from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from synlys_harness import JobStatus


class JobConnectorError(Exception):
    """连接器错误基类（宿主据此把任务标记为 failed 并向用户提示）。"""


class JobSubmitFailed(JobConnectorError):
    """提交被外部系统拒绝（参数非法、权限不足、服务不可用等）。"""


class JobPollFailed(JobConnectorError):
    """查询状态失败（网络/鉴权）。宿主保持原状态、下轮重试，不判失败。"""


@runtime_checkable
class JobConnector(Protocol):
    """子平台任务适配器。

    Attributes:
        kind: 任务类型（如 spec.nmr.forward），全局唯一。
        plugin_id: 归属插件 id（宿主据此在轮询时解析该插件配置）。
        status_map: 外部状态原文 → 统一状态（注册时的缺省映射来源）。
    """

    kind: str
    plugin_id: str
    status_map: dict[str, JobStatus]
    """外部状态原文（小写）→ 统一状态。连接器自带词汇表，注册时无需外部传入。"""

    async def submit(self, params: dict, ctx: dict) -> str:
        """提交任务。

        Args:
            params: 工具传入的参数对象。
            ctx: 宿主填充的调用上下文 {"config": 插件配置, "ai4ms_token": 用户凭证}。

        Returns:
            外部系统的任务 id。

        Raises:
            JobSubmitFailed: 提交失败。
        """
        ...

    async def poll(self, external_id: str, ctx: dict) -> str:
        """查询任务状态。

        Args:
            external_id: submit 返回的外部 id。
            ctx: 同 submit。

        Returns:
            外部系统的状态原文（由 status_map 翻译）。

        Raises:
            JobPollFailed: 查询失败（宿主保持原状态、下轮重试）。
        """
        ...

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """请求取消任务。

        Args:
            external_id: submit 返回的外部 id。
            ctx: 同 submit。

        Returns:
            外部系统是否受理取消；失败返回 False（宿主仍标记为 cancelled 由
            用户语义决定，见 JobService.cancel）。
        """
        ...


@dataclass
class RegisteredConnector:
    """注册表条目：连接器 + 状态映射表。"""

    connector: JobConnector
    status_map: dict[str, JobStatus] = field(default_factory=dict)

    def map_status(self, raw: str) -> JobStatus | None:
        """把外部状态原文映射为统一状态。

        Args:
            raw: 外部系统返回的状态字符串（大小写与首尾空白不敏感）。

        Returns:
            统一状态；无映射（含 None/未预期值）返回 None，
            调用方应保持任务原状态，避免状态倒退。
        """
        if not isinstance(raw, str):
            return None
        return self.status_map.get(raw.strip().lower())


class JobConnectorRegistry:
    """kind → 连接器 的注册表（进程内单例，随应用启动装配）。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._items: dict[str, RegisteredConnector] = {}

    def register(self, connector: JobConnector,
                 status_map: dict[str, JobStatus] | None = None) -> None:
        """注册一个连接器。

        Args:
            connector: 连接器实例。
            status_map: 外部状态原文 → 统一状态；缺省回落到连接器自带的
                `connector.status_map`（状态词汇表本就属于连接器）。键在注册时
                统一归一为小写并去首尾空白，调用方大小写可随意。空映射
                （`{}`）视为未提供、直接报错——缺映射会让任务状态永远映射不上。

        Raises:
            ValueError: kind 为空、连接器未实现协议（缺 submit/poll/cancel
                或它们不是 async def）、状态映射不是 dict、未提供状态映射、
                或该 kind 已被注册。

        Note:
            状态映射必须是 dict：非 mapping 的 truthy 值若放行，会在下面的
            `.items()` 处抛 AttributeError，而调用方只捕 ValueError——
            异常会穿透到 lifespan 让应用起不来。
        """
        kind = str(getattr(connector, "kind", "")).strip()
        if not kind:
            raise ValueError("连接器 kind 不能为空")
        if not isinstance(connector, JobConnector):
            raise ValueError(
                f"连接器未实现 JobConnector 协议（需 kind/plugin_id/status_map 与 "
                f"submit/poll/cancel 三个异步方法）: {type(connector).__name__}")
        # isinstance 只校验成员存在性、不校验方法签名（runtime_checkable 的
        # 局限），故额外逐个确认是 async def：插件漏写 async 时若放行，会在
        # 轮询 await 时才抛 TypeError，而那条路径只累计失败计数、任务永不终结
        for method in ("submit", "poll", "cancel"):
            if not inspect.iscoroutinefunction(getattr(connector, method)):
                raise ValueError(
                    f"连接器方法必须是 async def: "
                    f"{type(connector).__name__}.{method}")
        if kind in self._items:
            raise ValueError(f"任务类型已注册: {kind}")
        source = status_map if status_map is not None else getattr(
            connector, "status_map", None)
        if not isinstance(source, dict):
            raise ValueError(
                f"连接器 {kind} 的状态映射必须是 dict（外部状态原文 → JobStatus），"
                f"实际为 {type(source).__name__}")
        if not source:
            raise ValueError(
                f"连接器 {kind} 未提供状态映射（register 的 status_map 参数或"
                f" connector.status_map 属性），缺映射会让任务状态永远映射不上")
        normalized = {str(k).strip().lower(): v for k, v in source.items()}
        self._items[kind] = RegisteredConnector(connector, normalized)

    def get(self, kind: str) -> RegisteredConnector | None:
        """按 kind 取注册条目（未注册返回 None，由调用方给出可读错误）。"""
        return self._items.get(kind)

    @property
    def kinds(self) -> list[str]:
        """已注册的任务类型（排序）。"""
        return sorted(self._items)


def _new_external_id() -> str:
    """生成测试用外部 id。"""
    return "fake-" + uuid.uuid4().hex[:8]


# FakeConnector 状态脚本的标准映射（构造时即挂到实例的 status_map 上，
# 注册时可省略不传；显式传它能避免手写映射时漏项——漏映射会让状态被
# 静默忽略、任务永不终结）
FAKE_STATUS_MAP: dict[str, JobStatus] = {
    "queued": JobStatus.PENDING,
    "doing": JobStatus.RUNNING,
    "done": JobStatus.COMPLETED,
    "failed": JobStatus.FAILED,
    "cancelled": JobStatus.CANCELLED,
}


class FakeConnector:
    """测试/演示用连接器：纯内存，不依赖任何外部系统。

    状态按 script 顺序每次 poll 推进一步，推进到末位后保持不变。

    仅供测试与本地演示——不要在生产的 lifespan 装配里注册它。
    """

    def __init__(self, kind: str, plugin_id: str = "fake",
                 script: list[str] | None = None,
                 fail_submit: str = "") -> None:
        """初始化。

        Args:
            kind: 任务类型。
            plugin_id: 归属插件 id。
            script: 每次 poll 依次返回的状态原文；默认立即 done。
            fail_submit: 非空时 submit 抛 JobSubmitFailed（附该文案）。
        """
        self.kind = kind
        self.plugin_id = plugin_id
        # 自带状态词汇表（与 FAKE_STATUS_MAP 一致；注册时也可显式覆盖）
        self.status_map = dict(FAKE_STATUS_MAP)
        self._script = list(script or ["done"])
        self._fail_submit = fail_submit
        self._cursor: dict[str, int] = {}
        self._cancelled: set[str] = set()
        self.submitted: list[dict] = []  # 断言用：记录收到的提交参数

    async def submit(self, params: dict, ctx: dict) -> str:
        """记录参数并返回假 external_id。"""
        if self._fail_submit:
            raise JobSubmitFailed(self._fail_submit)
        external_id = _new_external_id()
        self._cursor[external_id] = 0
        self.submitted.append({"params": dict(params), "ctx": dict(ctx)})
        return external_id

    async def poll(self, external_id: str, ctx: dict) -> str:
        """按下标推进并返回当前状态原文。

        Note:
            未提交过的 external_id 一律返回脚本末态（进程重启后游标为空，
            属于可接受的降级）；正常路径不应传入未知 id。
        """
        del ctx  # 测试连接器不读上下文
        if external_id in self._cancelled:
            return "cancelled"
        index = self._cursor.get(external_id, len(self._script) - 1)
        raw = self._script[min(index, len(self._script) - 1)]
        self._cursor[external_id] = index + 1
        return raw

    async def cancel(self, external_id: str, ctx: dict) -> bool:
        """标记取消（poll 随即返回 cancelled）。"""
        del ctx
        self._cancelled.add(external_id)
        return True


def make_fake_connector(kind: str, plugin_id: str = "fake",
                        script: list[str] | None = None,
                        fail_submit: str = "") -> FakeConnector:
    """构造测试用连接器（供测试与本地演示）。

    Args:
        kind: 任务类型。
        plugin_id: 归属插件 id。
        script: poll 状态脚本。
        fail_submit: 非空时提交必失败。

    Returns:
        FakeConnector 实例。
    """
    return FakeConnector(kind, plugin_id=plugin_id, script=script,
                         fail_submit=fail_submit)
