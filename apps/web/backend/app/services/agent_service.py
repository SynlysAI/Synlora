"""AgentService：harness 组装、事件持久化与 SSE 内存队列。

事件事实源：DB 事件为查询/回放事实源（GET events、seed 历史均读 DB），
JSONL 为审计副本（当前无人消费，漂移可接受）。

瞬态事件不落盘（流式性能）：llm/delta、reasoning/delta 每 token 一条，
逐条落 JSONL/DB 等于每 token 一次 fsync，拖慢流式；故两类 delta 仅推
SSE，不写 JSONL/DB（seq 仍由 EventLog 分配，持久层出现 seq 洞，回放由
assistant/message、assistant/reasoning 等结构事件承载全文）。

遵守 Plan 2 harness 接入契约：
- sink 不得抛异常（宿主 sink 全包 try/except，持久化失败不杀对话）；
- SSE 推送只 put 内存队列，绝不等待消费者（慢客户端不拖 LLM 流）；
- 消费 run() 用 contextlib.aclosing 包裹；
- SSE 断连不 cancel：run 由 _drive 独立 asyncio task 后台执行完落盘，
  重连经 GET /sessions/{id}/events?after_seq=N 补齐；cancel 仅用户显式触发。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from synlys_harness import (
    AgentConfig,
    CodeExecutor,
    EventLog,
    EventType,
    ExtensionHooks,
    ModelProviderConfig,
    OpenAICompatibleBackend,
    RunSession,
    SessionEvent,
    ToolDefinition,
    ToolPipeline,
    ToolRegistry,
    ToolResult,
    resolve_executor,
)

from app.runtime.prompts import build_system_prompt
from app.runtime.assembly import (
    make_skill_resource_reader,
    prepare_skills,
    select_runtime_skills,
    select_runtime_tools,
)
from app.runtime.event_bridge import make_event_sinks
from app.runtime.interactions import make_approval_handler
from app.services.job_access import JobSubmissionScope
from app.services import workspace
from app.services.session_runtime import TooManyRuns
from app.services.skill_service import SkillService
from app.services.tool_registry import PIPELINE as _PIPELINE, REGISTRY as _REGISTRY

MAX_RUNS_PER_USER = 2  # 每用户并发运行上限（超出 API 层转 429）

# 抢占后等待 run 收尾的上限（秒）：解掉 future + 置旗标后正常应在毫秒级收尾，
# 给 5s 只是防"收尾路径自身有问题"时把抢占方一起拖住
PREEMPT_TIMEOUT_S = 5.0

# ask_user 等待回答的上限（秒）：没有上限时，一个停在 ask 上的 run 会永久
# 占住会话（用户关掉页面就再也没人来回答它）
ASK_TIMEOUT_S = 300.0

_LOGGER = logging.getLogger(__name__)

# 工具注册表收口在 app.services.tool_registry（与 assistants_api 共用同一实例）


class ActiveRun:
    """一次进行中的对话运行（queue 供 SSE 消费，done 标记收尾完成）。"""

    def __init__(self) -> None:
        """初始化队列与事件。"""
        self.queue: asyncio.Queue = asyncio.Queue()
        self.done = asyncio.Event()
        self.session: RunSession | None = None
        # ask_user 等待中的回答 future（None = 当前无待回答问题）
        self.ask_future: asyncio.Future | None = None


class AgentService:
    """对话运行编排（单例，挂 app.state.agent_service）。"""

    def __init__(self, store: Any, settings: Any, event_repo: Any,
                 skill_service: SkillService, file_repo: Any = None,
                 plugin_service: Any = None, capability_service: Any = None,
                 plugin_config_store: Any = None, ai4ms_identity: Any = None,
                 mcp_service: Any = None) -> None:
        """保存依赖。

        Args:
            store: DocumentStore（runs 直查直写）。
            settings: 应用配置（数据根/白名单）。
            event_repo: 会话事件 repo（DB 副本写入与回放）。
            skill_service: 技能服务（磁盘扫描目录 + 按名取正文）。
            file_repo: 文件 repo（file.send 登记产物供下载；None 时该工具报不支持）。
            plugin_service: 插件服务（提供已安装插件的解密配置；None = 无插件注入）。
            capability_service: 能力目录可见性服务（None = 不过滤，保持旧行为）。
            plugin_config_store: 插件配置存储（按用户维度解析插件配置）；None 时
                fail-closed 不注入任何插件配置（工具报"未配置"）。
            ai4ms_identity: AI⁴MS 身份代签服务（按登录用户代签子平台凭证）；
                None 时不注入 ai4ms_token（插件回落自身配置的服务 token）。
            mcp_service: 用户 MCP 服务；None 表示不装配远程 MCP 工具。
        """
        self._store = store
        self._settings = settings
        self._event_repo = event_repo
        self._skill_service = skill_service
        self._file_repo = file_repo
        self._plugin_service = plugin_service
        self._capability_service = capability_service
        self._plugin_config_store = plugin_config_store
        self._ai4ms_identity = ai4ms_identity
        self._mcp_service = mcp_service
        self._runs: dict[str, ActiveRun] = {}
        # 会话级互斥：session_id → 活跃 run_id 集合（同会话同时只允许一个 run）
        self._active_by_session: dict[str, set[str]] = {}
        # 后台驱动 task 的强引用（事件循环仅持弱引用，防 task 被 GC 中断）
        self._bg: set[asyncio.Task] = set()
        # python.run 沙箱执行器（首次使用时解析一次：local 直返；docker 探测
        # daemon+镜像，失败按 strict 拒绝或弱回退 local-weak 并告警）
        self._executor: CodeExecutor | None = None
        # 后台任务服务（装配阶段经 set_job_service 注入；None 时 job.* 工具报不可用）
        self._job_service: Any = None

    async def _code_executor(self) -> CodeExecutor:
        """解析（一次）沙箱执行器并缓存。

        Returns:
            部署级 CodeExecutor；docker 模式探测失败时为弱回退本机或
            fail-closed 拒绝执行器（取决于 sandbox_strict）。
        """
        if self._executor is None:
            executor, note = await asyncio.to_thread(
                resolve_executor, self._settings.sandbox_mode,
                image=self._settings.sandbox_docker_image,
                strict=self._settings.sandbox_strict,
                mem_limit=self._settings.sandbox_mem_limit,
                cpus=self._settings.sandbox_cpus,
                pids_limit=self._settings.sandbox_pids_limit,
                container_user=self._settings.sandbox_docker_user,
                deployment_id=self._settings.deployment_id,
            )
            if executor.sandbox != "docker":
                _LOGGER.warning("python.run 沙箱: %s", note)
            else:
                _LOGGER.info("python.run 沙箱: %s", note)
            self._executor = executor
        return self._executor

    async def _visible_plugin_configs(
            self, user_id: str, enabled_plugins: list[str] | None = None) -> dict[str, dict]:
        """该用户可见插件的配置（公共打底、个人覆盖），按会话级开关收窄。

        Args:
            user_id: 用户 sub。
            enabled_plugins: 会话级插件开关（默认全关：None 与空列表同为
                "未启用任何插件"；勾选不能放大可见性，取交集）。

        Returns:
            {插件 id: 扁平配置}；单条解密失败只跳过该插件（工具会报未配置），
            不打挂整轮对话。
        """
        store = self._plugin_config_store
        if store is None:
            # 无用户维度存储即无法按用户解析：fail-closed 返回空（工具报"未配置"），
            # 绝不回落公共配置注入——那会绕过可见性与用户维度，把公共凭证给所有人
            _LOGGER.warning("插件配置存储未注入，本轮不注入插件配置（fail-closed）")
            return {}
        if self._capability_service is None:
            # 无能力服务则无从判定该用户可见哪些插件：同 fail-closed
            _LOGGER.warning("能力服务未注入，无法按用户解析插件配置（fail-closed）")
            return {}
        plugin_ids = await self._capability_service.visible_ids(user_id, "plugin")
        plugin_ids &= set(enabled_plugins or [])
        out: dict[str, dict] = {}
        for plugin_id in sorted(plugin_ids):
            try:
                out[plugin_id] = await store.resolved_for_user(user_id, plugin_id)
            except RuntimeError:
                # 解密失败：跳过该插件（工具会报未配置），不打挂整轮
                continue
        return out

    async def _ai4ms_token_extra(self, user: dict) -> dict:
        """当前用户的 AI⁴MS 代签凭证（取不到则空 dict，由插件回落到服务 token）。

        Args:
            user: 当前登录用户的 token payload。

        Returns:
            {"ai4ms_token": "<token>"} 或 {}。
        """
        if self._ai4ms_identity is None:
            return {}
        token = await self._ai4ms_identity.token_for(user)
        return {"ai4ms_token": token} if token else {}

    def _jsonl_path(self, session_id: str, user_id: str) -> Path:
        """会话事件文件路径（父目录自动创建）。

        Args:
            session_id: 会话 id。
            user_id: 用户 sub（事件随会话归入该用户目录）。

        Returns:
            {data_root}/users/{user_id}/sessions/{session_id}/events.jsonl。
        """
        # 目录口径统一由 workspace 提供（与删除侧 sessions_api.delete_session 同源）
        p = (workspace.user_sessions_root(self._settings.data_root, user_id)
             / session_id / "events.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_active(self, run_id: str) -> ActiveRun | None:
        """取运行中的 ActiveRun（SSE 生成器消费其 queue）。

        Args:
            run_id: 运行 id。

        Returns:
            ActiveRun；run 已结束被摘除时返回 None。
        """
        return self._runs.get(run_id)

    async def chat(self, session_id: str, user: dict, assistant: dict | None,
                   provider_cfg: ModelProviderConfig, text: str,
                   workspace_root: Path,
                   requested_skills: list[str] | None = None,
                   attachments: list[dict] | None = None,
                   file_ownership: dict | None = None,
                   enabled_plugins: list[str] | None = None,
                   research_context: dict | None = None) -> str:
        """启动一轮对话运行，返回 run_id（事件经 ActiveRun.queue 流出）。

        装配收口：平台默认段 + 专家 persona（可选）+ 技能渐进披露（索引进
        提示词，正文只经 skill.read 工具按需取）。

        Args:
            session_id: 会话 id。
            user: 当前用户 payload（sub）。
            assistant: 助手文档（system_prompt/tool_whitelist）；None 或空 dict
                表示未选专家——无 persona、工具放开全部内置工具。
            provider_cfg: 已解密的模型服务配置。
            text: 用户消息文本。
            workspace_root: 工作区根目录（会话绑定项目 = 项目目录；无绑定 =
                sessions/{sid} 会话目录，由调用方解析后必传）。
                必填而非回落用户目录：文件按工作根作用域化后，用户目录下不会
                再有 files/，静默回落等于把 run 跑在错误目录。
            requested_skills: 本会话选中的技能名列表；None 或空列表表示全部可用。
            attachments: 随消息发送的附件元数据（[{file_id, filename, path}]，
                path 为相对 workspace_root 的路径；调用方保证文件已在该根内）。
                None/空 = 无附件。
            file_ownership: 交付/复制产生的文件记录归属字段（{"project_id": pid}
                或 {"session_id": sid}，与 workspace_root 对应）；None = 不带归属
                （历史口径，下载按磁盘位置解析）。
            enabled_plugins: 会话级插件开关（**默认全关**）：只有列表内的插件
                在本轮生效——工具、配置注入、技能索引按它收窄，未启用插件的
                播种专家按未选处理。None 与空列表同为"未启用任何插件"；
                内置工具不受影响。
            research_context: Plane 签发的只读科研范围（会话创建时已校验），
                经 ctx.extra 透传给工具；None 表示普通 Synlora 会话。
        Returns:
            run_id。

        Raises:
            TooManyRuns: 该会话已有进行中的消息，或该用户运行中的对话已达上限。

        """
        # 用户身份：一次取值（取不到即 KeyError，与函数内其余用法口径一致），
        # 绝不兜底成字面量目录名——那会让多个用户共用 users/anonymous/ 且与
        # DB 里记录的 user_id 不一致
        user_sub = str(user["sub"])
        # 会话级互斥：同会话两个并发 run 会各自 seed 同一份历史快照、从相同
        # seq 起号，DB _id=f"{sid}:{seq}" 碰撞写入被 db_sink 静默吞掉 → 事件
        # 拼接错乱/丢失，必须前置拒绝。但停在 ask 上的轮可先让位给新的用户
        # 消息——用户改用文本表达意愿时，不应被旧问答卡挡住。
        await self._admit_run(session_id)
        run_id = uuid.uuid4().hex[:12]
        session_runs = self._active_by_session.setdefault(session_id, set())
        session_runs.add(run_id)
        try:
            running = await self._store.list("runs", filters={
                "user_id": user["sub"], "status": "running"})
            if len(running) >= MAX_RUNS_PER_USER:
                raise TooManyRuns(f"该用户已有 {MAX_RUNS_PER_USER} 个运行中的对话")
            active = ActiveRun()
            self._runs[run_id] = active

            log = EventLog(sinks=make_event_sinks(
                session_id=session_id,
                user_id=user_sub,
                event_repo=self._event_repo,
                jsonl_path=self._jsonl_path(session_id, user_sub),
                queue=active.queue,
            ))

            # 扩展钩子挂载（可观测性：turn 生命周期 + 工具事件审计日志）
            async def _on_session_start(_session: RunSession) -> None:
                _LOGGER.info("turn 开始 run_id=%s session=%s", run_id, session_id)

            async def _on_session_end(_session: RunSession) -> None:
                _LOGGER.info("turn 结束 run_id=%s session=%s", run_id, session_id)

            async def _on_tool_event(ev: SessionEvent) -> None:
                _LOGGER.info("工具事件 %s name=%s run_id=%s",
                             ev.type.value, (ev.payload or {}).get("name", ""), run_id)

            hooks = ExtensionHooks(
                on_session_start=_on_session_start,
                on_session_end=_on_session_end,
                on_tool_event=_on_tool_event,
            )
            # 会话级 seq 续号依赖 seed 恢复：用 DB 历史事件预填充本轮日志，
            # 使 seq 跨轮续号（DB _id=f"{sid}:{seq}" 不碰撞，append 取
            # last.seq+1 不撞洞）、derive_messages 能投影出前几轮消息
            # （LLM 对话记忆）；历史因瞬态过滤带 seq 洞，seed 容忍严格递增；
            # 首轮会话历史为空跳过。
            history = await self._event_repo.list_events(session_id)
            if history:
                log.seed(history)
            backend = OpenAICompatibleBackend(provider_cfg)
            # 工作区根由调用方（sessions_api）按会话所属项目解析后传入；服务自身不拼路径
            workspace_root.mkdir(parents=True, exist_ok=True)
            # 技能：用户自建根 + 公共层 + 只读根（磁盘扫描，按登录用户并入自建根）
            # + 本会话选中项（None/空 = 全部可用）；索引进提示词，正文只入
            # context_extra（渐进披露，由 skill.read 按需取）
            # 会话级插件开关（默认关）：未启用的插件其技能也不进索引——技能描述
            # 是提示词的一部分，"工具被挡但技能还暴露"等于半开状态
            active_plugins = set(enabled_plugins or [])
            all_skills = self._skill_service.resolve_skills(user_id=user_sub)
            effective_plugins = set(active_plugins)
            hidden_skills: set[str] = set()
            if self._capability_service is not None:
                # 技能可见性（黑名单口径）：内置技能按策略、插件技能跟随其插件
                # 可见性；公共目录里管理员自建/导入的技能不在黑名单里（始终可见）
                hidden_skills = await self._capability_service.hidden_skill_names(
                    user["sub"])
                caps = self._capability_service
                visible_plugins = await caps.visible_ids(user["sub"], "plugin")
                effective_plugins &= visible_plugins
                for pid in visible_plugins:
                    if pid in active_plugins:
                        continue
                    pkg = caps.catalog.plugins.get(pid)
                    if pkg is not None:
                        hidden_skills |= set(pkg.skills)
            expert_skill_refs = [
                str(name) for name in ((assistant or {}).get("skill_refs") or []) if name
            ]
            requested = list(dict.fromkeys([
                *expert_skill_refs,
                *[str(name) for name in (requested_skills or []) if name],
            ]))
            active_skills = select_runtime_skills(
                all_skills,
                hidden_names=hidden_skills,
                active_plugin_ids=effective_plugins,
                requested_names=(requested if requested else None),
            )
            index = [(skill.name, skill.description) for skill in active_skills]
            bodies = {skill.name: skill.body for skill in active_skills}
            # 会话级插件开关对专家的影响：绑定的专家若来自未启用插件（默认全关），
            # 按"未选专家"处理——persona 不注入（插件的人设也是提示词，半暴露与
            # 技能同理不可接受），工具放开全部内置工具；会话文档不动，重新开启
            # 插件后下轮自动恢复
            if assistant and str(assistant.get("_id", "")).startswith("asst-plugin-"):
                owner_pid = str(assistant["_id"])[len("asst-plugin-"):]
                if owner_pid not in active_plugins:
                    assistant = None
            persona = str((assistant or {}).get("system_prompt") or "").strip()
            whitelist = list((assistant or {}).get("tool_whitelist") or [])
            run_registry = ToolRegistry()
            for name in _REGISTRY.names:
                run_registry.register(_REGISTRY.get(name))
            mcp_tool_names: list[str] = []
            if self._mcp_service is not None and assistant:
                connections = await self._mcp_service.runtime_connections(
                    user_sub,
                    [str(item) for item in (assistant.get("mcp_refs") or []) if item],
                )
                for connection in connections:
                    mcp_id = str(connection["id"])
                    for remote_tool in connection.get("tools") or []:
                        remote_name = str(remote_tool.get("name") or "")
                        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", remote_name)
                        local_name = f"mcp.{mcp_id}.{safe_name}"
                        if not remote_name or run_registry.find(local_name) is not None:
                            continue

                        async def execute_mcp(_ctx, args, *, connection_id=mcp_id,
                                              tool_name=remote_name):
                            result = await self._mcp_service.call_tool(
                                user_sub, connection_id, tool_name, args)
                            parts = result.get("content") or []
                            text = "\n".join(
                                str(part.get("text") or "")
                                for part in parts
                                if isinstance(part, dict) and part.get("type") == "text"
                            ).strip()
                            return ToolResult(
                                ok=result.get("isError") is not True,
                                content=text or json.dumps(result, ensure_ascii=False),
                                data={"mcp_id": connection_id, "result": result},
                                error=("mcp_tool_error"
                                       if result.get("isError") is True else None),
                            )

                        run_registry.register(ToolDefinition(
                            name=local_name,
                            description=str(remote_tool.get("description") or remote_name),
                            parameters=(remote_tool.get("input_schema")
                                        or {"type": "object", "properties": {}}),
                            execute=execute_mcp,
                            timeout_s=60.0,
                        ))
                        mcp_tool_names.append(local_name)
            run_pipeline = ToolPipeline(run_registry)
            # 执行器就在这里解析（后面 ctx.extra 还要用同一个），拿它的 sandbox 标记
            # 写进提示词：模型必须知道代码执行的能力边界（docker 断网、跑完即删；
            # local 无强隔离），否则会去 pip install / 抓外网白烧几步
            executor = await self._code_executor()
            prepared_skills = prepare_skills(active_skills, sandbox=executor.sandbox)
            system_prompt = build_system_prompt(
                persona=persona,
                workspace=(Path("/workspace")
                           if executor.sandbox == "docker" else workspace_root),
                skills=index,
                sandbox=executor.sandbox,
                shell_available=executor.sandbox == "docker",
            )
            # 有白名单时严格使用显式授权；无专家或未限制时放开注册表工具。
            # 技能和任务工具不再自动追加，避免升级代码静默扩大旧专家权限。
            all_plugin_tools: set[str] = set()
            visible_plugin_tools: set[str] = set()
            # 能力目录可见性：插件贡献的工具必须对该用户可见才保留（内置工具不受影响）
            if self._capability_service is not None:
                caps = self._capability_service
                all_plugin_tools = {
                    t for names in caps.tool_names_by_plugin.values() for t in names
                }
                if all_plugin_tools:
                    visible_tools = await caps.visible_tool_names(user["sub"])
                    # 会话级插件开关（默认全关）：插件工具只保留启用插件的；
                    # None 与空列表同为"未启用任何插件"；内置工具不受影响，
                    # 且勾选不能放大可见性——与用户级可见集取交集
                    keep = {
                        t for pid in active_plugins
                        for t in caps.tool_names_by_plugin.get(pid, ())
                    }
                    visible_plugin_tools = visible_tools & keep
            tool_names = select_runtime_tools(
                list(run_registry.names),
                whitelist=list(dict.fromkeys([*whitelist, *mcp_tool_names])),
                sandbox=executor.sandbox,
                all_plugin_tools=all_plugin_tools,
                visible_plugin_tools=visible_plugin_tools,
            )
            # 图片阅读双重门控：模型多模态 +（助手未限白名单或白名单显式包含）
            if provider_cfg.multimodal and (not whitelist or "file.read_image" in whitelist):
                tool_names = list(dict.fromkeys([*tool_names, "file.read_image"]))
            else:
                tool_names = [t for t in tool_names if t != "file.read_image"]

            # ask_user：发 ask/user 事件（落盘+SSE）并等待前端回答 future
            async def ask_handler(payload: dict) -> str:
                future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                active.ask_future = future
                try:
                    await log.append(EventType.ASK_USER, payload)
                    return await self._wait_for_answer(active, ASK_TIMEOUT_S)
                finally:
                    active.ask_future = None

            # 管线强制审批（Permission.ASK_USER）：复用 ask_user 的 future 回路，
            # payload 加 kind=approval 供前端渲染审批卡（允许/拒绝按钮）；用户
            # 答复仍走同一 answer API（固定文案"允许"/"拒绝"，管线按文本判定）
            async def ask_approval(payload: dict) -> str:
                tool = str(payload.get("tool", ""))
                preview = json.dumps(payload.get("args", {}), ensure_ascii=False)
                if len(preview) > 600:
                    preview = preview[:600] + "…"
                return await ask_handler({
                    "kind": "approval",
                    "tool_call_id": str(payload.get("tool_call_id", "")),
                    "tool": tool,
                    "query": f"请求执行工具 {tool}，是否允许？",
                    "args_preview": preview,
                })

            approval_handler = make_approval_handler(ask_approval)

            # file.send：复制产物进工作根 files/ 沙箱 → 登记 files 集合 → 发事件
            async def send_file_handler(payload: dict) -> ToolResult:
                return await self._deliver_file(active, log, workspace_root,
                                                user["sub"], payload,
                                                ownership=file_ownership)

            # 工具上下文快照：先落成局部变量，job_handler 需要引用它（把本轮
            # 的插件配置与代签凭证原样交给任务链路，避免二次解析配置）
            ctx_extra_snapshot: dict = {
                # 工作区根：插件连接器读取用户文件（如谱图上传）时用
                "workspace_root": str(workspace_root),
                # Plane 签发的只读科研范围（会话创建时已校验落库）；经 extra
                # 通道透传给工具，harness 不感知业务形状
                "research_context": research_context,
                "http_allowed_hosts": self._settings.allowed_hosts,
                # python.run 执行器（部署级注入，缺省工具回落本机执行；已在上文解析）
                "code_executor": executor,
                "execution_resources": prepared_skills.resources,
                "skills": bodies,
                "skill_meta": {
                    skill.name: skill.description for skill in active_skills
                },
                "skill_resource_roots": prepared_skills.resource_roots,
                "skill_resource_reader": make_skill_resource_reader(
                    prepared_skills.items
                ),
                # WeKnora 知识检索：连接配置 + 助手绑定的知识库范围（None/空 =
                # 未绑定，knowledge.search 工具会给出明确报错）
                "weknora_base_url": self._settings.weknora_base_url,
                "weknora_api_key": self._settings.weknora_api_key,
                "knowledge_base_ids": list(
                    (assistant or {}).get("knowledge_base_ids") or []),
                # 联网搜索（SearXNG）：地址空 = 未启用，web.search 工具报明确错误
                "web_search_endpoint": self._settings.assistant_web_search_endpoint,
                "web_search_api_key": self._settings.assistant_web_search_api_key,
                # 用户交互工具的宿主回调（ask_user / file.send）与管线强制审批
                "ask_user_handler": ask_handler,
                "send_file_handler": send_file_handler,
                "approval_handler": approval_handler,
                # 插件配置命名空间（仅注入该用户可见且未被会话级开关排除的
                # 插件，个人配置优先；核心不认识任何插件专属字段）
                "plugins": (await self._visible_plugin_configs(
                                user["sub"], enabled_plugins)
                            if self._plugin_service is not None else {}),
                # 按登录用户代签的 AI⁴MS 身份凭证（插件优先用它，取不到则用插件配置里的服务 token）
                **await self._ai4ms_token_extra(user),
            }
            ctx_extra_snapshot["job_submission_scope"] = JobSubmissionScope(
                allowed_tools=frozenset(tool_names),
                allowed_plugins=frozenset(effective_plugins),
                skills=prepared_skills.items,
                resources=prepared_skills.resources,
                workspace_root=workspace_root.resolve(),
                ownership={
                    str(key): str(value)
                    for key, value in (file_ownership or {}).items()
                    if value is not None
                },
            )

            async def job_handler(payload: dict) -> ToolResult:
                """job.* 工具的宿主实现（提交/查询/取消后台任务）。

                Args:
                    payload: 工具传来的负载（action/kind/params/job_id...）。

                Returns:
                    工具结果；未注入任务服务时 fail-closed。
                """
                if self._job_service is None:
                    return ToolResult(ok=False, content="后台任务未启用",
                                      error="no_handler")
                return await self._job_service.handle(
                    payload, user=user, session_id=session_id,
                    ctx_extra=ctx_extra_snapshot)

            ctx_extra_snapshot["job_handler"] = job_handler

            session = RunSession(
                config=AgentConfig(
                    system_prompt=system_prompt,
                    tool_names=tool_names,
                    max_steps=(assistant or {}).get("max_steps", 25),
                ),
                registry=run_registry, pipeline=run_pipeline, backend=backend,
                event_log=log, user_id=user["sub"], run_id=run_id,
                hooks=hooks,
                workspace_root=workspace_root,
                context_extra=ctx_extra_snapshot,
            )
            active.session = session
            t = asyncio.create_task(
                self._drive(run_id, session, text, user["sub"], session_id,
                            attachments=attachments))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        except Exception:
            # 初始化任何一步失败（用户超限/DB 历史空洞/组装异常）都回滚注册，
            # 防止 _runs/_active_by_session 残留失败 run（泄漏句柄 + 会话被
            # 永久卡 429 + SSE 哨兵永不投递）
            self._runs.pop(run_id, None)
            session_runs.discard(run_id)
            raise
        return run_id

    async def _drive(self, run_id: str, session: RunSession, text: str,
                      user_id: str, session_id: str,
                      attachments: list[dict] | None = None) -> None:
        """后台驱动 run 至完成并落盘终态（独立于 SSE 消费者，断连不中断）。

        Args:
            run_id: 运行 id。
            session: harness 运行会话。
            text: 用户消息文本。
            user_id: 用户 sub。
            session_id: 会话 id。
            attachments: 随消息发送的附件元数据（None/空 = 无附件）。
        """
        try:
            await self._store.insert("runs", {
                "_id": run_id, "session_id": session_id, "user_id": user_id,
                "status": "running", "started_at": time.time(),
            })
        except Exception:
            pass  # run 记录失败不阻断对话流（并发限制会暂时失效，属存储故障降级）
        final_status = "completed"
        try:
            pending_text, pending_attachments = text, attachments
            while True:
                stream = session.run(
                    pending_text,
                    attachments=pending_attachments,
                )
                async with contextlib.aclosing(stream):
                    async for _event in stream:
                        pass  # 事件已由 sinks 持久化并入队
                # 插话兜底：turn 正常结束但队列残留插话（模型收尾窗口入队、
                # 无下一个 step 可消费）→ 转为下一轮用户输入自动续跑，防静默
                # 丢失；取消/LLM 失败路径 take_queued_turn 内部已丢弃返回 None
                pending_text = session.take_queued_turn()
                pending_attachments = None
                if pending_text is None:
                    break
        except Exception:
            final_status = "failed"
        # harness 取消旗标（未暴露公共 API，接入契约确认可读）：用户显式 cancel 优先于异常归类
        if session._cancel.is_set():  # noqa: SLF001
            final_status = "aborted"
        try:
            await self._store.update("runs", run_id, {
                "status": final_status, "ended_at": time.time()})
        except Exception:
            # 终态落盘失败仅记录（run 记录停留 running，属存储故障降级），
            # 不向上抛——抛出会杀掉本 task 且触发未处理异常告警
            _LOGGER.warning("run 终态落盘失败: run_id=%s status=%s",
                            run_id, final_status, exc_info=True)
        finally:
            active = self._runs.pop(run_id, None)
            if active:
                active.queue.put_nowait(None)  # SSE 结束哨兵（任何路径都必须放，防 SSE 挂死）
                active.done.set()
            session_runs = self._active_by_session.get(session_id)
            if session_runs is not None:
                session_runs.discard(run_id)  # 释放会话占位（空集合保留，量级=会话数）

    def set_job_service(self, service: Any) -> None:
        """注入后台任务服务（job.* 工具的宿主实现）。

        Args:
            service: JobService 实例（提供 handle() 分发）。
        """
        self._job_service = service

    def _yieldable(self, run_id: str) -> bool:
        """该 run 是否可让位给新的用户消息。

        Args:
            run_id: 运行 id。
        Returns:
            停在 ask 上等回答的轮可让位；正在执行的前台轮不可让位。
        """
        active = self._runs.get(run_id)
        return bool(active is not None
                    and active.ask_future is not None
                    and not active.ask_future.done())

    async def _preempt_runs(self, run_ids: list[str]) -> None:
        """抢占指定 run（让位给用户新消息），等其收尾。

        Args:
            run_ids: 待抢占的 run id 列表（调用方传入快照——本方法会触发
                注册表变更，边迭代边改会漏项）。

        Note:
            **必须先解掉停在 ask 上的 future**：harness 的工具执行处在
            `await self._pipeline.run(...)` 里等这个 future，沿途没有取消
            检查点（`_cancel` 只在 step 边界与 LLM 流内检查）。只置取消旗标
            的话 run 卡在那个 await 上永不收尾，本方法 `await done` 会一起挂死。
            解掉 future 后工具返回，循环走到下一个 step 边界即按取消退出。
        """
        actives = [self._runs.get(rid) for rid in run_ids]
        for active in actives:
            if active is None:
                continue
            if active.ask_future is not None and not active.ask_future.done():
                active.ask_future.set_result("（本轮已被新的用户消息中止）")
            if active.session is not None:
                active.session.cancel()
        # 等待收尾用**总预算**而非每 run 一份：N 个 run 各等 PREEMPT_TIMEOUT_S
        # 会把用户消息拖成 N×5s 的阻塞（实测踩过：请求被阻塞 25s）
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PREEMPT_TIMEOUT_S
        for active in actives:
            if active is None:
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(active.done.wait(), remaining)
            except TimeoutError:
                _LOGGER.warning("抢占 run 收尾超时（超过 %.1fs 预算）",
                                PREEMPT_TIMEOUT_S)

    async def _admit_run(self, session_id: str) -> None:
        """会话准入：有占位时先尝试让位，让不掉才拒绝。

        Args:
            session_id: 会话 id。
        Raises:
            TooManyRuns: 会话已有不可让位的进行中 run，或可让位者未能在
                预算内收尾。

        Note:
            **调用方必须在返回后同步完成占位**（`session_runs.add(run_id)`，
            中间不得插入 await）——本方法有 await（抢占），占位的原子性由
            "检查与占位之间无 await"保证（原实现的前提，不能因引入抢占而丢）。

            **只抢占一次、不循环重试**：取消是协作式的（run 若正卡在模型的
            网络调用里，要等下一个检查点才注意到旗标），循环重试会把用户消息
            变成一段一段 5s 的无限阻塞。超预算就直接拒绝，让用户稍候重试。
        """
        session_runs = self._active_by_session.setdefault(session_id, set())
        if not session_runs:
            return
        if not all(self._yieldable(rid) for rid in session_runs):
            raise TooManyRuns("该会话已有进行中的消息")
        await self._preempt_runs(list(session_runs))
        if self._active_by_session.get(session_id):
            raise TooManyRuns("会话正忙，请稍候重试")

    async def cancel(self, run_id: str) -> bool:
        """取消运行（仅用户显式停止；SSE 断连不走此路径）。

        Args:
            run_id: 运行 id。

        Returns:
            是否找到仍在内存注册表中的运行并发出取消。
        """
        active = self._runs.get(run_id)
        if active and active.session is not None:
            active.session.cancel()
            return True
        return False

    async def steer(self, run_id: str, text: str) -> bool:
        """运行中插话（harness steering：下一个 step 边界注入为 user 消息）。

        插话经 session.steer 入队后由 loop 落成带 steering 标记的
        user/message 事件，随事件流持久化——所以队列本身无需单独持久化。

        Args:
            run_id: 运行 id。
            text: 插话文本（非空由 API 层校验）。

        Returns:
            是否找到仍在内存注册表中的运行并完成入队。
        """
        active = self._runs.get(run_id)
        if active and active.session is not None:
            active.session.steer(text)
            return True
        return False

    async def answer(self, run_id: str, text: str) -> bool:
        """回答运行中 ask_user 的待答问题（resolve future，工具随即返回）。

        Args:
            run_id: 运行 id。
            text: 用户回答文本。

        Returns:
            是否找到等待中的问题并完成投递。
        """
        active = self._runs.get(run_id)
        if active and active.ask_future is not None and not active.ask_future.done():
            active.ask_future.set_result(text)
            return True
        return False

    async def _wait_for_answer(self, active: "ActiveRun", timeout_s: float) -> str:
        """等待 ask_user 的回答，超时返回可继续的兜底文本。

        Args:
            active: 该 run 的 ActiveRun（调用前 ask_future 已置位）。
            timeout_s: 等待上限（秒）。

        Returns:
            用户回答文本；超时返回提示文本，让模型按已知信息继续——没有上限
            时，一个停在 ask 上的 run 会永久占住会话（用户关掉页面就再没人
            来回答它，只能靠重启后端）。

        Note:
            超时由 `asyncio.wait_for` 取消 future；`ask_handler` 的 finally
            会把 `active.ask_future` 置 None，`answer()` 也有 `not done()` 守卫，
            故随后到达的迟到回答不会撞上已取消的 future。
        """
        future = active.ask_future
        if future is None:
            return ""
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            _LOGGER.warning("ask_user 等待回答超时（%.0fs），本轮按跳过继续", timeout_s)
            return "（用户长时间未回答，已跳过此问题，请按已知信息继续）"

    async def _deliver_file(self, active: "ActiveRun", log: EventLog,
                            workspace_root: Path, user_id: str,
                            payload: dict,
                            ownership: dict | None = None) -> ToolResult:
        """file.send 宿主侧：产物复制进工作根 files/ → 登记 files 集合 → 发 file/send 事件。

        复制而非登记原路径：下载端点的 stored_path 安全校验要求文件落在
        files/ 沙箱内（与上传同一约束），登记任意路径会被 404 拒绝。

        Args:
            active: 所属运行（未用，签名对称保留）。
            log: 本轮事件日志（事件落盘 + SSE）。
            workspace_root: 工作区根。
            user_id: 用户 sub。
            payload: 工具入参（path/note/tool_call_id）。
            ownership: 文件记录归属字段（project_id/session_id）；None = 不带
                归属（历史口径，下载按磁盘位置解析）。

        Returns:
            工具结果（成功 content 为给 LLM 的确认文本）。
        """
        del active  # 未用
        rel = str(payload.get("path", "")).strip()
        try:
            src = (workspace_root / rel).resolve()
            if workspace_root.resolve() not in src.parents:
                return ToolResult(ok=False, content="路径越界", error="path_escape")
        except OSError:
            return ToolResult(ok=False, content=f"路径非法: {rel}", error="path_escape")
        if not src.is_file():
            return ToolResult(ok=False, content=f"文件不存在: {rel}", error="not_found")
        if self._file_repo is None:
            return ToolResult(ok=False, content="文件交付未配置（缺 file repo）", error="no_handler")
        files_dir = workspace_root / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / src.name
        stem, suffix = target.stem, target.suffix
        n = 1
        while target.exists():
            target = files_dir / f"{stem}-{n}{suffix}"
            n += 1
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            return ToolResult(ok=False, content=f"复制失败: {exc}", error="io_error")
        size = target.stat().st_size
        doc = await self._file_repo.create({
            "user_id": user_id,
            **(ownership or {}),
            "filename": target.name,
            "stored_path": target.relative_to(workspace_root).as_posix(),
            "size": size,
            "mime": "application/octet-stream",
        })
        await log.append(EventType.FILE_SEND, {
            "tool_call_id": payload.get("tool_call_id", ""),
            "file_id": doc["_id"],
            "filename": target.name,
            "size": size,
            "path": rel,
            "note": payload.get("note", ""),
        })
        return ToolResult(ok=True, content=f"已把文件 {target.name}（{size // 1024}KB）发送给用户")

    async def events_after(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        """取 seq 大于 after_seq 的会话事件（SSE 断连重连增量补齐用）。

        Args:
            session_id: 会话 id。
            after_seq: 客户端已收到的最大 seq。

        Returns:
            seq 升序的事件列表。
        """
        events = await self._event_repo.list_events(session_id)
        return [e for e in events if e.seq > after_seq]
