"""后台任务连接器：宿主与各子平台（Spec_Agent 等）之间的适配协议。

分工：宿主 JobService 管状态机与持久化；连接器只管"怎么跟某个外部系统
说话"——提交、查状态、取消，以及把外部状态原文翻译成 harness 的统一状态。
插件在自己的包内实现连接器并在启动时注册（见 PluginService 扩展点）。
"""
from __future__ import annotations

import uuid
from synlys_harness import JobStatus

from app.plugins.contracts import JobPollFailed, JobSubmitFailed


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
