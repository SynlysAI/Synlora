"""SynlysAgent Web 后端入口。"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.assistants_api import router as assistants_router
from app.api.auth_api import router as auth_router
from app.api.files_api import project_router as project_files_router
from app.api.files_api import router as files_router
from app.api.knowledge_api import router as knowledge_router
from app.api.models_api import router as models_router
from app.api.projects_api import router as projects_router
from app.api.sessions_api import router as sessions_router
from app.api.skills_api import router as skills_router
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
from app.services.skill_service import SkillService
from app.services.weknora_service import WeKnoraService

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
    app.state.project_service = ProjectService(store, settings.data_root)
    app.state.skill_service = SkillService(settings.data_root)
    app.state.skill_service.seed_builtins()  # 幂等：内置技能是列表能列出它们的前提
    app.state.weknora_service = WeKnoraService(
        settings.weknora_base_url, settings.weknora_api_key)
    app.state.agent_service = AgentService(
        store, settings, app.state.event_repo, app.state.skill_service,
        file_repo=app.state.file_repo)
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
    app.include_router(project_files_router)
    app.include_router(projects_router)
    app.include_router(skills_router)
    app.include_router(knowledge_router)

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    # 单端口部署：dist 存在时托管前端 SPA（必须在所有 API 路由之后注册，否则会吞掉 API 请求）。
    # /chat/<id>、/admin/* 等前端路径刷新时会直接打到后端，StaticFiles(html=True) 只回
    # 404，因此用「静态资源挂载 + SPA catch-all 回 index.html」的标准做法（等价于
    # vite/nginx 的 historyApiFallback）。
    if (FRONTEND_DIST / "index.html").is_file():
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA 回退：dist 根下的真实文件（favicon/icons 等）直接返回，其余路径回 index.html。

            Args:
                full_path: 去掉前导斜杠后的请求路径。

            Returns:
                命中的静态文件；未命中（前端路由）时为 index.html。
            """
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (FRONTEND_DIST / full_path).resolve() if full_path else None
            if (candidate is not None
                    and candidate.is_file()
                    and candidate.is_relative_to(FRONTEND_DIST.resolve())):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        logger.warning("未找到前端构建产物 %s（先在 apps/web/frontend 执行 npm run build），当前仅 API 模式", FRONTEND_DIST)

    return app


app = create_app()
