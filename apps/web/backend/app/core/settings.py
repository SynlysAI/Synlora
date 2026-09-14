"""应用配置（pydantic-settings，env 与 .env 加载）。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。

    Attributes:
        storage_backend: 存储后端（mongodb|sqlite）。
        mongodb_uri: Mongo 连接串（backend=mongodb 时必填）。
        mongodb_db: 业务库名。
        sqlite_path: SQLite 文件路径。
        auth_secret: 与 AI4MS 共享的 HMAC secret（为空时按门户规则派生）。
        auth_enabled: 关闭后匿名放行（仅开发）。
        dev_auth_token: sqlite 开发模式的固定 token。
        host/port: 监听地址。
        data_dir: 运行数据根（workspaces/sessions）。
        http_allowed_hosts: http.request 工具白名单（逗号分隔）。
        fernet_key: provider api_key 加密 key。
        user_quota_bytes: 每用户工作区配额。
        weknora_base_url: WeKnora 知识库服务地址（含 /api/v1，空 = 未接入）。
        weknora_api_key: WeKnora API Key（X-API-Key 头）。
        assistant_web_search_endpoint: SearXNG 搜索服务地址（空 = 未启用联网搜索）。
        assistant_web_search_api_key: SearXNG API Key（实例配了才需要，默认空）。
        sandbox_mode: python.run 执行形态（local|docker，默认 local；docker 需
            预构建 sandbox_docker_image 镜像，见 docker/sandbox/）。
        sandbox_docker_image: docker 模式镜像名。
        sandbox_strict: docker 模式不可用时拒绝执行（fail-closed）而非回退本机
            （回退时事件带 sandbox=local-weak 标记）。多用户/公网部署建议开启。
        sandbox_mem_limit / sandbox_cpus / sandbox_pids_limit: 单容器资源限额。
        sandbox_docker_user: 容器内运行用户（空 = 镜像默认非 root 用户）。
        ai4ms_providers: 启用的 AI⁴MS 子平台 provider 名单（逗号分隔，默认空 =
            全关）。已支持：spec_agent（Spec_Agent 核磁预测三件套）。
        spec_agent_base_url: Spec_Agent 服务地址（如 http://10.26.15.93:8001，
            空 = 工具报未配置）。
        spec_agent_token: 调用 Spec_Agent 的 Bearer token（空 = 不发认证头，
            适配 Spec_Agent 侧 AUTH_ENABLED=false）。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    storage_backend: str = "sqlite"
    mongodb_uri: str = ""
    mongodb_db: str = "synlys_agent"
    sqlite_path: str = "../data/synlys_agent.db"
    auth_secret: str = ""
    auth_enabled: bool = True
    dev_auth_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8005
    data_dir: str = "../data"
    http_allowed_hosts: str = ""
    fernet_key: str = ""
    user_quota_bytes: int = 1_073_741_824
    weknora_base_url: str = ""
    weknora_api_key: str = ""
    assistant_web_search_endpoint: str = ""
    assistant_web_search_api_key: str = ""
    sandbox_mode: str = "local"
    sandbox_docker_image: str = "synlora-sandbox:latest"
    sandbox_strict: bool = False
    sandbox_mem_limit: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_docker_user: str = ""
    ai4ms_providers: str = ""
    spec_agent_base_url: str = ""
    spec_agent_token: str = ""

    @property
    def data_root(self) -> Path:
        """数据根目录（自动创建）。"""
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allowed_hosts(self) -> list[str]:
        """http 白名单列表。"""
        return [h.strip() for h in self.http_allowed_hosts.split(",") if h.strip()]
