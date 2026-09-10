"""SynlysAgent Web 后端入口。"""
from fastapi import FastAPI


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="SynlysAgent", version="0.1.0")

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    return app


app = create_app()
