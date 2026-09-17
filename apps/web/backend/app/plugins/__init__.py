"""插件框架：配置存储与安装编排。

宿主只认「插件包目录 + plugin.json」这一通用约定，不为任何单个插件写分支
代码；新增子平台 = 新增一个插件目录，本包与 harness 均不改（参考 jiuwen
的 manifest 目录扫描与 DSH 的插件即插即用）。
插件包的扫描与工具加载统一在 `app.catalog.loader`（内容目录的一份发现逻辑）。
"""
from app.plugins.contracts import (
    JobConnector,
    JobConnectorError,
    JobConnectorRegistry,
    JobPollFailed,
    JobSubmitFailed,
    RegisteredConnector,
)

__all__ = [
    "JobConnector",
    "JobConnectorError",
    "JobConnectorRegistry",
    "JobPollFailed",
    "JobSubmitFailed",
    "RegisteredConnector",
]
