# SynlysAgent 待办计划（路线图执行版）

> 对应总设计 §11 演进路线。基线：2026-09-12，V1 + 架构补强已完成
> （事件内核/工具管线/三栏工作台/WeKnora 实接/steering/hooks/上下文压缩/LLM 重试）。
> 用 checkbox 跟踪进度；每项含验收标准，做之前可展开为独立实施计划。

---

## 阶段一：V1.x 打磨（近期，各项 0.5~2 天）

### 1. 沙箱升级（多用户部署的前置条件）✅ 2026-09-14 完成（0.4.0-beta.1）
- [x] 选型：**Docker 容器执行**。结论：DSH/Pi 的 OS 级包装路线（bwrap/Seatbelt）在 Windows 开发机不可用且无网络/内存限制，jiuwenbox 绑定 Linux 内核机制；Docker 双平台通吃且原生覆盖文件系统/网络/CPU/内存/进程数全维度。Windows 受限令牌（参考 DSH sandbox-windows-acl）留作纯本地加固备选，不在本轮范围
- [x] `run_python` 抽象执行器接口：`CodeExecutor` 协议（Pi BashOperations 模式）+ Local/Docker/Failing 三实现，`SANDBOX_MODE` 切换、宿主经 `ctx.extra.code_executor` 注入
- [x] Docker 实现：每次执行临时容器（workspace 单目录挂载 /workspace、断网、mem/cpu/pids 限额、非 root、跑完即删；代码经工作区文件传入不进 argv），镜像 `docker/sandbox/`（分析全家桶 + CJK 字体）
- [x] 回退兼容：探测失败回落 local 并在日志/事件中标记 `sandbox=local-weak`；`SANDBOX_STRICT=true` 时 fail-closed 拒绝执行（云部署）
- [x] 测试：逃逸用例（写工作区外路径、读宿主环境变量、外联 DNS/连接）在 docker 沙箱下全被拦（`test_sandbox_docker.py`，无 daemon/镜像自动 skip）；超时 kill、取消清理、挂载产物、截断均有断言

### 2. 跨会话长期记忆（工作区级）
- [ ] 记忆存储：`memories` 集合，维度 `user_id + project_id`（事实/偏好/实验结论，带来源会话 id）
- [ ] 抽取：turn/end 钩子（on_session_end）→ LLM 抽取候选记忆 → 去重合并入库
- [ ] 注入：会话装配时按 user+project 检索 top-N，进 system prompt（带"参考记忆"标注）
- [ ] 开关：全局配置 `memory_enabled`，**默认关**（照 jiuwen 惯例），管理页可切
- [ ] 遗忘：会话删除不级联删记忆（记忆独立于会话）；提供管理页查看/删除入口
- [ ] 验收：工作区 A 会话里告知的偏好，新会话（同工作区）能遵守；开关关闭时零记忆

### 3. 工具结果 spill（半天内，无外部依赖）
- [ ] post-execute 替换纯截断：超限结果落工作区 `tmp/spill-{call_id}.txt`，content 换成有界预览（前 ~2KB）+ locator（完整结果路径，可经 file.read 分段寻回），参考 DSH `packages/spill/`
- [ ] 验收：python.run 打印超大 DataFrame → 上下文只占预览大小，模型能按 locator 读回任意段落

### 4. 小修（顺手项）
- [ ] 设计文档 §11 同步实际进度（WeKnora 划掉、补 steering/压缩/重试/审批条目）

### 能力市场与用户可见性 ✅ 2026-09-15（0.6.0-beta.1）
- [x] 后台左导航 + 「常规」页占位：管理后台由顶部页签改为**左侧导航列表**（188px，滚动只在右侧），页签为常规 / 模型服务 / 助手管理 / 技能管理 / 插件；新增「常规」页（外观与界面语言，占位）
- [x] 三层模型：内置目录（随仓库只读，`app/catalog/` 枚举专家/技能/插件）→ 管理员策略（`catalog_policy`）→ 用户安装（`user_capabilities`）
- [x] 管理员策略：`catalog_policy`（`_id = f"{kind}:{item_id}"`，`visibility ∈ {public, hidden}` + `default_enabled`；缺省 = public + 默认启用，升级后行为不变）
- [x] 用户安装记录：`user_capabilities`（`_id = f"{uid}:{kind}:{item_id}"`；**安装 = 只写记录，不复制文件**；安装/卸载幂等）
- [x] `CapabilityService`：按用户算可见集（hidden 不可见 / public+默认 全员可见 / public+非默认 需安装），唯一过滤入口
- [x] 运行期与列表落地：插件工具过滤、技能索引过滤（黑名单口径 `hidden_skill_names`，公共技能目录管理员自建技能始终可见）、专家列表过滤、`ctx.extra["plugins"]` 只注入可见插件（内置工具不受影响）；插件配置公共/个人两层（个人优先、公共打底）
- [x] 用户侧能力中心：左栏底部用户菜单 → 独立整页 `/capabilities`（专家/技能/插件三分组，安装/卸载，插件可填个人配置）；管理页三处加「可见性 / 默认启用」开关
- [ ] **未做**：用户自建 / 导入技能与插件（用户私有目录 `{data_dir}/users/{uid}/` 仅设计预留、无写入路径）、按角色 / 按用户白名单的细粒度可见性、插件市场远程下载

---

## 阶段二：V2 科研能力（中期，按价值排序）

### 5. AI⁴MS 真实工具接入（最高优先：项目立身之本）
- [ ] 统一 Job 注册表（第一个子任务，Spec_Agent 接入的地基）：`jobs` 存储（稳定 id + 所属会话）+ 统一状态机（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED，Connector 映射各系统内部状态，见集成设计稿 §12/13）+ 完成通知唤醒 agent（免轮询，参考 DSH `packages/jobs/`）
- [ ] 「Spec_Agent 异步任务工具化」**（部分完成）**：首期**同步**核磁三件套 ✅ 0.5.0（经插件机制接入：宿主通用插件框架 + `spec_agent` 插件包 + 管理页配置）；异步部分仍待办——`/api/v1/tasks/{nmr,gpc,...}` 提交 → 建在**统一 Job 注册表**上（提交即返回 job_id，避免长阻塞占 step），5 种谱图异步任务建在其上
- [x] 凭证与白名单：AI⁴MS 网关地址/凭证经**插件配置**（管理页填写、落库加密、运行期按 `ctx.extra["plugins"]` 注入），不进 settings/.env
- [ ] SpecLabOS 设备/工作流接入（排 Spec_Agent 后；工具声明 `Permission.ASK_USER`，管线已支持强制审批）
- [ ] 验收：对话提交一个 NMR 任务，agent 自行跟踪并在完成后整合结果

### 6. MCP adapter（一次投入换工具生态）
- [ ] Tool Registry 加 MCP 来源：stdio/HTTP transport，启动时拉工具 schema 注册
- [ ] MCP 工具命名空间（`mcp.<server>.<tool>`）与助手白名单兼容
- [ ] 凭证/启停管理进管理页
- [ ] 验收：接入任一现成 MCP server（如 filesystem），工具卡片正常展示与执行

### 7. 多 Agent / SwarmFlow（触发条件：出现并行科研场景需求）
- [ ] 前置：assistants 升级为 teammate 定义原料（已具备：persona/工具白名单/模型/知识库绑定）
- [ ] harness 加 subagent provider（fork 事件会话 + 工具面收窄）
- [ ] 声明式 team 装配（借鉴 jiuwen `agents/swarm/` spec → manifest 折叠）
- [ ] 前端团队消息展示（借鉴 TeamEventGroupDisplay）+ 轨迹面板
- [ ] 验收：一个 leader + 2 专家（文献综述/实验设计）协作完成综合报告

### 8. 动态技能市场 / 双进程 RPC（用量驱动，暂缓）
- [ ] 技能安装/卸载/可见性（jiuwen skill_manager 模式）
- [ ] harness RPC server → headless 运行时（用户量/稳定性需求出现时）

---

## 决策触发点（什么时候启动什么）

| 信号 | 启动项 |
|---|---|
| 要开放多用户 / 公网部署 | 1 沙箱升级（必须先做）+ run 崩溃恢复（steering 队列/Inbox 持久化 + session repair，参考 DSH inbox/repair；单机部署默认不做，崩溃重发可接受） |
| 用户反馈"换会话就失忆" | 2 长期记忆 |
| Spec_Agent 接口可用 | 5 立即接入（Job 注册表先行） |
| 出现"多专家并行协作"真实需求 | 7 多 Agent |
| 需要接入第三方工具生态 | 6 MCP |

---

## 已完成基线（2026-09-12）

- V1：事件会话内核（derive 投影/turn-step 循环/取消）、四段工具管线、10 个内置工具、三栏工作台、工作区/专家/技能、AI⁴MS 兼容认证、双后端存储、SSE 断连续传
- 架构补强：steering 插话全链路、ExtensionHooks 宿主挂载、上下文自动压缩（pi 式阈值摘要 + 持久化标记）、LLM 瞬时错误退避重试、WeKnora 实接（hybrid-search + 助手绑定硬边界 + knowledge.list）、运行中插话 UI、工具行执行中动画、自定义弹窗

## 已完成（2026-09-14）

- Office 文档生成：python-docx/python-pptx/openpyxl/pandas + 内置技能 `office-doc`（产物落工作区 output/）
- 管线强制审批（Permission.ASK_USER）：pre-execute 打断 + approval_handler 复用 ask/user 事件与 answer 回路（fail-closed）+ 前端审批卡（允许/拒绝）；内置工具暂均 ALLOW，SpecLabOS 接入时声明即生效
- 沙箱升级（0.4.0-beta.1）：python.run 执行器抽象（local/docker/fail-closed 三态，`SANDBOX_MODE`/`SANDBOX_STRICT` 切换）+ 临时容器强隔离（断网/资源限额/非 root/单目录挂载）+ 沙箱镜像 `docker/sandbox/` + 逃逸集成测试（详见阶段一·1）
- 插件机制（0.5.0-beta.1，一切皆插件）：宿主通用插件框架（`plugin.json` manifest 扫描 + 配置加密落库 + 安装即注册工具/挂技能/播种专家 + 管理 API）+ 管理页「插件」页签（按 schema 动态渲染）+ 首个插件 `spec_agent`（Spec_Agent 核磁三件套）；插件配置由管理页填写落库加密，不进 settings/.env（详见 `2026-09-14-synlysagent-07-ai4ms-specagent-integration.md`）

## 已完成（2026-09-15）

- 能力目录（市场）+ 后台导航重构（0.6.0-beta.1）：管理后台由顶部页签改为**左侧导航列表**并新增「常规」页占位；「专家/技能/插件」改为三层可见性模型（内置只读目录 → 管理员 `catalog_policy` 策略 → 用户 `user_capabilities` 安装记录，**安装 = 只写记录不复制文件**）；运行期由 `CapabilityService` 按用户算可见集统一过滤（插件工具/技能索引/专家列表/插件配置，内置工具不受影响），技能过滤用黑名单口径保住公共技能目录里管理员自建的技能；用户侧新增独立整页「能力中心」`/capabilities`（详见 `2026-09-15-synlysagent-08-capability-catalog-and-admin-nav.md`）
