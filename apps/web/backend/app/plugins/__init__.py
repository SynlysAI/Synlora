"""插件框架：通用插件包扫描、配置存储与安装编排。

宿主只认「插件包目录 + plugin.json」这一通用约定，不为任何单个插件写分支
代码；新增子平台 = 新增一个插件目录，本包与 harness 均不改（参考 jiuwen
的 manifest 目录扫描与 DSH 的插件即插即用）。
"""
from app.plugins.config_store import PluginConfigStore
from app.plugins.loader import PluginPackage, load_plugin_tools, plugin_roots, scan_plugins
from app.plugins.service import PluginService

__all__ = [
    "PluginConfigStore", "PluginPackage", "PluginService",
    "load_plugin_tools", "plugin_roots", "scan_plugins",
]
