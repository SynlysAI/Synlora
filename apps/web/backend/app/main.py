"""SynlysAgent Web 后端入口。"""
from fastapi import FastAPI

from app.api.auth_api import router as auth_router
from app.core.settings import Settings


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="SynlysAgent", version="0.1.0")
    app.state.settings = Settings()
    app.state.store = None  # 占位：Task 5 接 store 时统一在 startup 初始化
    app.include_router(auth_router)

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    return app


app = create_app()
