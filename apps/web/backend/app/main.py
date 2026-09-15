"""Synlora Web 后端入口。"""
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
from app.catalog.api import router as catalog_router
from app.catalog.items import CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.policy import CatalogPolicyRepo
from app.catalog.service import CapabilityService
from app.catalog.user_caps import UserCapabilityRepo
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
from app.plugins import PluginConfigStore, PluginService
from app.plugins.api import router as plugins_router
from app.services.agent_service import AgentService
from app.services.project_service import ProjectService
from app.services.skill_service import SkillService
from app.services.tool_registry import REGISTRY
from app.services.weknora_service import WeKnoraService
from app.version import APP_VERSION, APP_VERSION_LABEL

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
    # 插件框架：扫描内置内容（专家/技能/插件） → 建技能服务 →
    # 注册已安装插件的工具与技能根（插件技能根由 plugin_service.startup()
    # 挂载，先于 AgentService 构造完成）
    index = scan_catalog(catalog_roots(settings))
    plugin_config_store = PluginConfigStore(store, settings.fernet_key)
    app.state.skill_service = SkillService(settings.data_root)
    app.state.skill_service.seed_builtins()  # 幂等：内置技能是列表能列出它们的前提
    app.state.weknora_service = WeKnoraService(
        settings.weknora_base_url, settings.weknora_api_key)
    app.state.plugin_service = PluginService(
        registry=REGISTRY, config_store=plugin_config_store, packages=index.plugins,
        skill_service=app.state.skill_service,
        assistant_repo=app.state.assistant_repo,
    )
    # 挂载范围除公共安装外，还含"有用户个人安装记录"的插件：注册是能力可用性
    # （进程级，谁装都该挂），可见性过滤由 CapabilityService 另行按用户计算
    await app.state.plugin_service.startup(
        extra_plugin_ids=await UserCapabilityRepo(store).installed_plugin_ids())
    # 能力目录可见性服务：须在 plugin_service.startup() 之后构造。工具映射传
    # PluginService 的方法本身（活引用）——插件可在运行期安装，快照会漏掉启动后
    # 新挂载的工具（可见性算不出 → 被误当不可见而过滤）。
    app.state.capability_service = CapabilityService(
        catalog=CatalogService(settings=settings,
                               skill_service=app.state.skill_service,
                               index=index),
        policy=CatalogPolicyRepo(store),
        installs=UserCapabilityRepo(store),
        tool_names_by_plugin=app.state.plugin_service.tool_names_by_plugin,
    )
    # 暴露给目录 API 与运行期（插件配置校验取 schema、用户维度解析配置）
    app.state.plugin_config_store = plugin_config_store
    app.state.plugin_packages = index.plugins
    app.state.agent_service = AgentService(
        store, settings, app.state.event_repo, app.state.skill_service,
        file_repo=app.state.file_repo, plugin_service=app.state.plugin_service,
        capability_service=app.state.capability_service,
        plugin_config_store=plugin_config_store)
    await seed_assistants(store)
    yield
    await store.close()


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="Synlora", version=APP_VERSION, lifespan=lifespan)
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
    app.include_router(plugins_router)
    app.include_router(catalog_router)

    @app.get("/api/health")
    async def health() -> dict:
        """健康检查（公开端点，同时返回版本信息供前端展示）。"""
        return {
            "status": "ok",
            "version": APP_VERSION,
            "version_label": APP_VERSION_LABEL,
        }

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
