"""应用版本信息（全项目唯一口径）。

前端经 GET /api/health 读取展示，package.json 的 version 需与此保持一致；
语义化版本：主版本.次版本.补丁号[-预发布标识]，内测期使用 beta 预发布段。
"""

APP_VERSION = "0.10.0-beta.1"
APP_VERSION_LABEL = "内测版"
