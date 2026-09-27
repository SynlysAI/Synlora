"""Synlora Web 后端入口。"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.assistants_api import router as assistants_router
from app.api.auth_api import router as auth_router
from app.api.deps import Repos
from app.api.files_api import project_router as project_files_router
from app.api.files_api import router as files_router
from app.api.files_api import session_router as session_files_router
from app.api.jobs_api import router as jobs_router
from app.api.knowledge_api import router as knowledge_router
from app.api.me_api import router as me_router
from app.api.mcp_api import router as mcp_router
from app.api.models_api import router as models_router
from app.api.projects_api import router as projects_router
from app.api.research_api import router as research_router
from app.api.sessions_api import router as sessions_router
from app.api.skills_api import router as skills_router
from app.api.tools_api import router as tools_router
from app.catalog.api import router as catalog_router
from app.catalog.items import CatalogService
from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.policy import CatalogPolicyRepo
from app.catalog.seed import seed_experts, seed_skill_policies
from app.catalog.service import CapabilityService
from app.catalog.user_caps import UserCapabilityRepo
from app.core.settings import Settings
from app.db.repos import (
    AssistantRepo,
    EventRepo,
    FileRepo,
    JobRepo,
    ProviderRepo,
    RunRepo,
    SessionRepo,
)
from app.db.store import create_store
from app.plugins.config_store import PluginConfigStore
from app.plugins.service import PluginService
from app.plugins.api import router as plugins_router
from app.services.agent_service import AgentService
from app.services.ai4ms_identity import Ai4msIdentityService
from app.services.expert_service import UserExpertService
from app.plugins.contracts import JobConnectorRegistry
from app.services.job_poller import JobPoller
from app.services.job_access import WorkspaceJobGuard
from app.services.sandbox_job_runner import SandboxJobRunner
from app.services.job_service import JobService
from app.services.wakeup import make_run_context_resolver
from app.services.mcp_service import McpService
from app.services.project_service import ProjectService
from app.services.research_context import PlaneResearchClient, ResearchContextAdapter
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
    # 进程重启后 run 记录会残留 running（run 是进程内 task，重启即死），
    # 而并发上限按 DB 计数判——不清理会让用户被"运行中的对话"永久封顶
    stale = await app.state.run_repo.abort_stale(time.time())
    if stale:
        logger.info("已清理重启前残留的进行中 run: %d 条", stale)
    app.state.file_repo = FileRepo(store)
    app.state.event_repo = EventRepo(store)
    app.state.project_service = ProjectService(store, settings.data_root)
    # 用户自建专家：文件为事实源，列「我的专家」时幂等实例化进 assistants
    app.state.expert_service = UserExpertService(settings.data_root)
    # 一次性清理：历史版本把用户专家实例化进 assistants 集合（带 owner），
    # 专家改为纯文件事实源后这些记录已无事实源身份，启动时整批移除
    legacy = await UserExpertService.purge_legacy_records(store)
    if legacy:
        logging.info("已清理历史用户专家 assistants 记录 %s 条", legacy)
    app.state.mcp_service = McpService(store, settings.fernet_key)
    # 插件框架：扫描内置内容（专家/技能/插件） → 建技能服务 →
    # 注册已安装插件的工具与技能根（插件技能根由 plugin_service.startup()
    # 挂载，先于 AgentService 构造完成）
    index = scan_catalog(catalog_roots(settings))
    # fail-loud：catalog/ 是数据目录（非 Python 包），非 editable 部署（wheel 式安装、
    # 只拷 app/ 等）会静默丢掉全部内置内容——此处把"静默为空"变成显眼的启动告警
    if not (index.experts or index.skills or index.plugins):
        logger.warning(
            "未发现任何内置内容（catalog/ 缺失或为空）：内置专家/技能/插件均不可用。"
            "检查部署是否遗漏 %s", catalog_roots(settings)[0])
    plugin_config_store = PluginConfigStore(store, settings.fernet_key)
    # 内置技能：catalog/skills 作为只读根直接提供（与插件技能根同一模式），
    # {data_dir}/public/skills 是公共层（管理员自建/导入，始终可见）
    app.state.skill_service = SkillService(settings.data_root)
    app.state.skill_service.set_catalog_skills(index.skills)
    repo_skills_root = catalog_roots(settings)[0] / "skills"
    removed = app.state.skill_service.migrate_legacy_builtin_copies(repo_skills_root)
    if removed:
        logger.info("已清理迁移前的内置技能旧副本: %s", removed)
    app.state.weknora_service = WeKnoraService(
        settings.weknora_base_url, settings.weknora_api_key)
    # 任务连接器注册表：必须先于 PluginService（插件在其挂载时注册连接器）
    app.state.job_connectors = JobConnectorRegistry()
    app.state.plugin_service = PluginService(
        registry=REGISTRY, config_store=plugin_config_store, packages=index.plugins,
        skill_service=app.state.skill_service,
        assistant_repo=app.state.assistant_repo,
        job_connectors=app.state.job_connectors,
    )
    # 挂载范围除公共安装外，还含"有用户个人安装记录"的插件：注册是能力可用性
    # （进程级，谁装都该挂），可见性过滤由 CapabilityService 另行按用户计算
    await app.state.plugin_service.startup(
        extra_plugin_ids=await UserCapabilityRepo(store).installed_plugin_ids())
    # 能力目录可见性服务：须在 plugin_service.startup() 之后构造。工具映射传
    # PluginService 的方法本身（活引用）——插件可在运行期安装，快照会漏掉启动后
    # 新挂载的工具（可见性算不出 → 被误当不可见而过滤）。
    app.state.capability_service = CapabilityService(
        catalog=CatalogService(index=index),
        policy=CatalogPolicyRepo(store),
        installs=UserCapabilityRepo(store),
        tool_names_by_plugin=app.state.plugin_service.tool_names_by_plugin,
    )
    # 公共 MCP 目录：经能力服务的 catalog 实时取（管理端写入后的热重载也走它）
    app.state.mcp_service.set_catalog_provider(
        lambda: app.state.capability_service.catalog.mcps)
    # 暴露给目录 API 与运行期（插件配置校验取 schema、用户维度解析配置）
    app.state.plugin_config_store = plugin_config_store
    app.state.plugin_packages = index.plugins
    # AI⁴MS 身份代签：按登录用户为子平台（Spec_Agent 等）代签短效凭证，
    # 解析不到身份（sqlite 本地用户/匿名）时插件回落自身配置的服务 token
    app.state.ai4ms_identity = Ai4msIdentityService(settings)
    app.state.agent_service = AgentService(
        store, settings, app.state.event_repo, app.state.skill_service,
        file_repo=app.state.file_repo, plugin_service=app.state.plugin_service,
        capability_service=app.state.capability_service,
        plugin_config_store=plugin_config_store,
        ai4ms_identity=app.state.ai4ms_identity,
        mcp_service=app.state.mcp_service)
    # 后台任务：任务服务 → 轮询器。连接器注册表在 PluginService 之前已建
    # （插件在其挂载时注册连接器）。任务终态写 Job 文档供运行信息面板与
    # job.status/job.list 读取；job_wakeup_enabled 时终态（成功/失败）额外
    # 唤醒所属会话续跑（见 agent_service.notify_job_finished）。
    # repo 聚合：唯一构造点，deps.get_repos 复用本对象。
    app.state.repos = Repos(
        provider=app.state.provider_repo, assistant=app.state.assistant_repo,
        session=app.state.session_repo, run=app.state.run_repo,
        file=app.state.file_repo, event=app.state.event_repo)
    job_repo = JobRepo(store)
    app.state.workspace_job_guard = WorkspaceJobGuard(job_repo)
    app.state.job_service = JobService(
        repo=job_repo, connectors=app.state.job_connectors,
        plugin_config_store=plugin_config_store,
        ai4ms_identity=app.state.ai4ms_identity,
        settings=settings,
        workspace_guard=app.state.workspace_job_guard)
    executor = await app.state.agent_service._code_executor()  # noqa: SLF001
    if executor.sandbox == "docker":
        await app.state.job_service.recover_sandbox_jobs(executor)
        app.state.sandbox_job_runner = SandboxJobRunner(
            executor,
            app.state.job_service.mark_sandbox_running,
            app.state.job_service.finish_sandbox,
        )
        app.state.job_service.set_sandbox_runner(app.state.sandbox_job_runner)
    else:
        app.state.sandbox_job_runner = None
    app.state.agent_service.set_job_service(app.state.job_service)
    # 任务完成唤醒：终态（成功/失败）回调 + 运行装配解析（settings 可整体关闭）
    if settings.job_wakeup_enabled:
        app.state.job_service.set_wakeup_hook(
            app.state.agent_service.notify_job_finished)
        app.state.agent_service.set_run_context_resolver(make_run_context_resolver(
            settings, app.state.project_service, app.state.repos,
            expert_service=app.state.expert_service))
    app.state.job_poller = JobPoller(app.state.job_service)
    await app.state.job_poller.start()
    await seed_experts(store, index.experts)
    await seed_skill_policies(store, index.skills)
    yield
    await app.state.job_poller.stop()
    if app.state.sandbox_job_runner is not None:
        await app.state.sandbox_job_runner.shutdown()
    # 公共 stdio MCP 子进程统一清理（无则空操作）
    if getattr(app.state, "mcp_service", None) is not None:
        await app.state.mcp_service.aclose()
    await store.close()


def create_app() -> FastAPI:
    """构建 FastAPI 应用（任务逐步扩展路由）。"""
    app = FastAPI(title="Synlora", version=APP_VERSION, lifespan=lifespan)
    app.state.settings = Settings()
    app.state.research_context_adapter = ResearchContextAdapter(
        app.state.settings.plane_base_url,
        app.state.settings.plane_request_timeout_seconds,
    )
    app.state.plane_research_client = PlaneResearchClient(
        app.state.settings.plane_base_url,
        app.state.settings.plane_api_token,
        app.state.settings.plane_request_timeout_seconds,
    )
    app.state.store = None  # lifespan 启动时初始化（未就绪时依赖层 503）
    app.include_router(auth_router)
    app.include_router(models_router)
    app.include_router(assistants_router)
    app.include_router(sessions_router)
    app.include_router(files_router)
    app.include_router(project_files_router)
    app.include_router(session_files_router)
    app.include_router(projects_router)
    app.include_router(research_router)
    app.include_router(skills_router)
    app.include_router(tools_router)
    app.include_router(knowledge_router)
    app.include_router(plugins_router)
    app.include_router(catalog_router)
    app.include_router(me_router)
    app.include_router(mcp_router)
    app.include_router(jobs_router)

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
