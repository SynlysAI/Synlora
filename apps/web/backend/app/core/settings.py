"""应用配置（pydantic-settings，env 与 .env 加载）。"""
from pathlib import Path
import hashlib
import math

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 通过模块位置推导仓库根，避免相对路径受启动 cwd 影响。
PROJECT_ROOT = Path(__file__).resolve().parents[5]
RUNTIME_DATA_DIR = PROJECT_ROOT / ".runtime" / "data"


class Settings(BaseSettings):
    """全局配置项。

    Attributes:
        storage_backend: 存储后端（mongodb|sqlite）。
        mongodb_uri: Mongo 连接串（backend=mongodb 时必填）。
        mongodb_db: 业务库名。
        sqlite_path: SQLite 文件路径，默认在项目根 `.runtime/data/synlys_agent.db`。
        auth_secret: 与 AI4MS 共享的 HMAC secret（为空时按门户规则派生）。
        auth_enabled: 关闭后匿名放行（仅开发）。
        dev_auth_token: sqlite 开发模式的固定 token。
        host/port: 监听地址。
        data_dir: 运行数据根（users/<uid>/workspaces、skills 等），默认为项目根
            `.runtime/data`；可在 .env 显式覆盖。
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
        sandbox_docker_user: 容器内运行用户 "uid[:gid]"（空 = Linux 下动态对齐
            宿主工作区属主，保证容器内可写 /workspace；Windows 或宿主属主为
            root 时用镜像默认非 root 用户——后端以 root 运行的部署建议显式
            配置本项或改后端非 root 运行，否则容器内写工作区会被权限拒绝）。
        sandbox_docker_network: docker 模式是否放开容器网络（默认 False =
            断网）。开启后容器走 Docker 默认网络，可 pip 安装依赖、访问外网，
            但也因此能到达宿主可达的一切（内网服务、同网桥容器、云元数据
            地址）——仅在可信内网部署开启；公网/多租户部署应改用 egress
            代理或专用网络方案。
        sandbox_job_default_timeout_s: 后台沙箱任务默认超时秒数。
        sandbox_job_max_timeout_s: 后台沙箱任务最大超时秒数。
        sandbox_deployment_id: Docker 执行资源所属部署标识；空时按 data_root 派生。
        job_wakeup_enabled: 后台任务完成（成功/失败）后自动唤醒所属会话续跑
            （活跃轮走插话注入、空闲轮自动起新一轮；取消不唤醒；连续无用户
            输入的唤醒轮有上限防自激）。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    storage_backend: str = "sqlite"
    mongodb_uri: str = ""
    mongodb_db: str = "synlys_agent"
    sqlite_path: str = str(RUNTIME_DATA_DIR / "synlys_agent.db")
    auth_secret: str = ""
    auth_enabled: bool = True
    dev_auth_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8005
    data_dir: str = str(RUNTIME_DATA_DIR)
    http_allowed_hosts: str = ""
    fernet_key: str = ""
    user_quota_bytes: int = 1_073_741_824
    weknora_base_url: str = ""
    weknora_api_key: str = ""
    plane_base_url: str = ""
    plane_api_token: str = ""
    plane_service_token: str = ""
    plane_request_timeout_seconds: float = 3.0
    assistant_web_search_endpoint: str = ""
    assistant_web_search_api_key: str = ""
    sandbox_mode: str = "local"
    sandbox_docker_image: str = "synlora-sandbox:latest"
    sandbox_strict: bool = False
    sandbox_mem_limit: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_docker_user: str = ""
    sandbox_docker_network: bool = False
    sandbox_job_default_timeout_s: float = 1800.0
    sandbox_job_max_timeout_s: float = 7200.0
    sandbox_deployment_id: str = ""
    job_wakeup_enabled: bool = True

    @model_validator(mode="after")
    def validate_sandbox_job_timeouts(self) -> "Settings":
        """校验后台任务默认与最大超时。"""
        default = self.sandbox_job_default_timeout_s
        maximum = self.sandbox_job_max_timeout_s
        if (not math.isfinite(default) or default <= 0
                or not math.isfinite(maximum) or maximum <= 0):
            raise ValueError("后台沙箱任务超时必须是有限正数")
        if default > maximum:
            raise ValueError("SANDBOX_JOB_DEFAULT_TIMEOUT_S 不得大于最大值")
        return self

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

    @property
    def deployment_id(self) -> str:
        """返回显式部署标识或按绝对数据根稳定派生的标识。"""
        if self.sandbox_deployment_id.strip():
            return self.sandbox_deployment_id.strip()
        normalized = str(self.data_root.resolve()).replace("\\", "/").lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
