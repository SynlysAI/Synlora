"""uvicorn 启动入口（开发/PM2 共用）：监听地址与端口取自 Settings（.env）。

用法：
    python run_uvicorn.py            # 开发直接运行
    pm2 start ecosystem.config.cjs   # 生产经根目录 PM2 配置启动（interpreter 指向 conda python）
"""
import uvicorn

from app.core.settings import Settings


def main() -> None:
    """按应用配置启动 uvicorn 服务。"""
    s = Settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port)


if __name__ == "__main__":
    main()
