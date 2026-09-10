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
