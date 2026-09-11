"""SynlysAgent Web 后端入口。"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.assistants_api import router as assistants_router
from app.api.auth_api import router as auth_router
from app.api.files_api import router as files_router
from app.api.models_api import router as models_router
from app.api.projects_api import router as projects_router
from app.api.sessions_api import router as sessions_router
from app.core.settings import Settings
from app.db.repos import (
    AssistantRepo,
    EventRepo,
    FileRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
    seed_assistants,
)
from app.db.store import create_store
from app.services.agent_service import AgentService
from app.services.project_service import ProjectService

logger = logging.getLogger("synlys.web")

# 前端构建产物目录（apps/web/frontend/dist，npm run build 生成）
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动建 store/repos/种子助手，关闭断开。"""
    settings = app.state.settings
    store = create_store(settings.storage_backend,
                         sqlite_path=settings.sqlite_path,
                         mongodb_uri=settings.mongodb_uri,
                         mongodb_db=settings.mongodb_db)
    await store.init()
    app.state.store = store
    app.state.provider_repo = ProviderRepo(store, fernet_key=settings.fernet_key)
    app.state.assistant_repo = AssistantRepo(store)
    app.state.session_repo = SessionRepo(store)
    app.state.run_repo = RunRepo(store)
    app.state.file_repo = FileRepo(store)
    app.state.event_repo = EventRepo(store)
    app.state.agent_service = AgentService(store, settings, app.state.event_repo)
    app.state.project_service = ProjectService(store, settings.data_root)
    await seed_assistants(store)
    yield
    await store.close()


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="SynlysAgent", version="0.1.0", lifespan=lifespan)
    app.state.settings = Settings()
    app.state.store = None  # lifespan 启动时初始化（未就绪时依赖层 503）
    app.include_router(auth_router)
    app.include_router(models_router)
    app.include_router(assistants_router)
    app.include_router(sessions_router)
    app.include_router(files_router)
    app.include_router(projects_router)

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    # 单端口部署：dist 存在时托管前端（必须在所有 API 路由之后挂 "/"，否则会吞掉 API 请求）
    if (FRONTEND_DIST / "index.html").is_file():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    else:
        logger.warning("未找到前端构建产物 %s（先在 apps/web/frontend 执行 npm run build），当前仅 API 模式", FRONTEND_DIST)

    return app


app = create_app()
