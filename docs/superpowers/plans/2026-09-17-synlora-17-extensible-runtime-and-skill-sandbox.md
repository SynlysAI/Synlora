# Synlora 可扩展科研运行时与技能沙箱实施计划

> **执行模型必读：** 使用 `superpowers:executing-plans` 分批执行本计划，按复选框记录进度。用户未授权子智能体，不得自行委派；不创建工作树、分支，不提交或推送。先读本文，再检查当前工作区和适用的项目指令。本文是代码修改前的交接计划，不代表任何功能已经实现。

**目标：** 保留现有单实例多用户架构，明确内核与产品内容边界，补齐统一命令执行、完整技能资源包、通用后台任务和稳定插件接入接口，使新增科研能力主要通过增加技能或插件目录完成。

**架构：** harness 管通用机制和执行协议；Web 宿主管身份、能力授权、内容选择、运行装配及持久化。产品内置内容仍在 `apps/web/backend/catalog/`。公共技能单份存储，由宿主按本轮权限解析来源，通过只读绑定挂载进入临时 Docker 容器；用户工作区独立读写，不为每个用户复制公共技能。

**技术栈：** Python 3.12、conda `synlysagent`、FastAPI、Pydantic v2、Docker Python SDK、pytest；前端只做工具展示映射与既有任务列表的取消操作，不改整体布局。

**计划日期：** 2026-09-17。

**修订状态：** 2026-09-17 第二版，已纳入用户确认的通用后台任务需求。本文已直接替换旧约束，而不只是追加附录：`job.submit` 同时支持沙箱 Python、Shell、技能脚本与外部插件任务；外部连接器不是所有 Job 的通用协议，不整体下沉 harness。请执行本版完整批次 A、B、C；任务 8a—8c 是本次新增的必做项。

---

## 0. 已确认需求与执行约束

### 0.1 本次包含

1. 保留 `packages/synlys-harness` 与 `apps/web/backend` 的顶层结构。
2. 将产品身份、输出格式约定移出 harness；审批、唤醒采用中立协议。
3. 在宿主提供稳定的插件公共接口，保留外部任务连接器；harness 通过通用 job_handler 接宿主任务服务，不强制所有 Job 绑定插件。
4. 将 AgentService 的能力装配、交互接线、事件桥接按职责收口，运行管理仍留在宿主。
5. 统一进程执行协议，保留 `python.run`，新增 Docker 环境内的 `shell.run`。
6. 支持技能包中的 `scripts/`、`references/`、`assets/`；技能说明和执行脚本必须来自同一目录。
7. 按本轮能力挂载完整技能目录，公共技能不复制到用户目录。
8. 打通部署扩展目录 `public/catalog`，插件贡献项可选，明确管理员部署规则。
9. 更新必要的说明文档、核心回归用例和最小前端工具显示。
10. 扩展 job.submit 支持 sandbox.python、sandbox.shell、sandbox.skill；共用状态、查询、取消、结果和完成唤醒。
11. 单进程内管理后台执行，独立于当前对话轮；补必要的停止、超时、重启中断与工作区删除保护，不承诺故障续跑。

### 0.2 明确不包含

- 多进程、多副本、分布式调度、独立 worker 服务。
- 事件持久化失败策略重做、任务完成通知可靠投递、完整故障恢复；新增沙箱 Job 的基础停机清理及重启标记中断仍在本次范围内。
- 全局容量与计费体系、完整磁盘配额、数据库分页/索引专项优化。
- 持久 shell、PTY、在线 IDE、交互终端、容器内常驻服务。受管理的长时间 Job 在本次范围内，不等同于持久终端。
- 模型自行安装系统依赖、任意网络访问、提权、指定宿主挂载或 Docker 镜像。
- 第三方不可信 Python 插件隔离、插件市场上传接口、插件热重载。
- 多 Agent/Swarm、MCP、长期记忆、工具结果 spill。
- 全量 UI 重构、知识库 ACL 改造、版本治理专项迁移。

### 0.3 不能因用户数量少而省略的边界

- 用户工作区隔离；挂载路径只能由宿主可信代码生成。
- 技能资源只读；只挂本轮允许的具体技能目录，不能挂整个 catalog 或整个用户目录。
- Docker 环境不可用时新增 shell 工具不能降级执行宿主命令。
- 实验设备、带凭证远端服务走插件/任务连接器，不把服务凭证注入通用 shell。
- 用户安装市场能力只改变授权记录，不等于允许用户部署 Python 插件代码。
- 用户自建技能脚本只能在沙箱执行，后端不得将其作为模块导入。
- 超时、取消必须清理本次命令及其子进程/容器，不能遗留常驻任务。
- 后台化不扩大执行权限；平台沙箱 Job 不要求插件 ID，插件 Job 仍遵守对应插件授权和会话开关。
- 后台 Job 生命周期独立于对话：停止当前回答不等于取消计算；明确取消通过 job.cancel 或同一宿主服务的取消 API。

### 0.4 项目执行规则

- 对话、文档、功能及参数说明使用中文；Python 遵循项目注释与 PEP8 规范。
- 修改前读取本计划指定的参考实现；不能只照概念自创实现或样式。
- Python 仅使用现有 `synlysagent` 环境；先 `conda env list` 核实，不新建或改系统环境。
- **新增测试范围需执行前一次确认：** 仅补协议、资源来源、权限、挂载、取消等核心回归；不创建大批外围测试。本轮写计划不运行测试。
- 新增核心测试获准后先写最小失败用例，再实现并执行既有相关用例；未获准则先询问，不默默跳过验证。
- 不运行真实科研服务任务，不调用实验设备，不消耗真实模型额度作验收。
- 前端改动 `npm run build` 通过即可交付；用户自测交互，不跑 Playwright 循环。
- 不提交、不推送，不创建隔离工作树。代码阶段使用 `apply_patch` 修改文件。
- 不因为本计划而把不相关服务、数据和历史测试大规模重排。

### 0.5 执行前唯一需再次确认的事项

执行模型先询问：**是否同意按任务清单补充少量核心回归测试，并在可用时运行本机 Docker 集成测试？**

Docker 镜像不存在或需要拉取/构建时，单独说明并取得用户同意；不能将跳过的容器集成测试写成通过。其余设计按本计划实施，不重复讨论已经确认的目录和挂载方向。

---

## 1. 当前代码事实与不可误判的地方

| 现状 | 证据文件 | 本次对应处理 |
|---|---|---|
| harness 无 FastAPI/业务数据库导入，但含科研身份及前端输出约定 | `packages/synlys-harness/src/synlys_harness/prompts.py` | 产品文本移到宿主，核心保留通用拼装 |
| 审批成功依赖中文字符串“允许” | `packages/synlys-harness/src/synlys_harness/tools/pipeline.py` | 宿主翻译文案，核心消费结构化结果 |
| `job_wake_kind` 与前端 `job_completed` 耦合 | `packages/synlys-harness/src/synlys_harness/jobs.py` | 保持历史事件格式，由宿主设置元数据 |
| 执行协议只接收 Python 源码 | `packages/synlys-harness/src/synlys_harness/tools/sandbox.py` | 增加通用进程执行入口 |
| 容器只挂 workspace，启动命令固定 Python | 同上 | 增加可信只读资源挂载及 shell 入口 |
| Python 已能自行启动子进程，不是“Python 语言安全沙箱” | 同上及 `tools/builtin.py` | 不把禁用 shell 工具误写为禁止一切命令执行 |
| `skill.read` 只读预先注入的正文 | `packages/synlys-harness/src/synlys_harness/tools/builtin.py` | 补资源根与受控文本资源读取 |
| 技能元数据与正文分两次按名称找来源 | `apps/web/backend/app/services/skill_service.py`、`agent_service.py` | 一次解析绑定来源，后续不重新择源 |
| catalog 支持多个根，后面的覆盖前面的 | `apps/web/backend/app/catalog/loader.py` | 保留覆盖规则并落实到运行期 |
| SkillService 初始化只挂第一个 catalog 技能根 | `apps/web/backend/app/main.py` | 消费合并后的有效技能目录，不能仅追加根导致优先级反转 |
| 现有连接器假设外部 ID、状态轮询、插件配置 | `apps/web/backend/app/services/job_connectors.py` | 归入宿主插件公共接口，不能当作所有 Job 的通用协议 |
| job.submit 仅按 kind 查外部连接器 | `apps/web/backend/app/services/job_service.py` | 新增平台沙箱任务分发和独立后台执行管理 |
| 任务取消当前可能只收敛本地状态，外部任务不一定停止 | `apps/web/backend/app/services/job_service.py` | 沙箱 Job 必须确认容器停止才终结；保留并说明外部适配器能力差异 |
| 任务 API 目前只读、面板没有直接取消入口 | `apps/web/backend/app/api/jobs_api.py`、`apps/web/frontend/src/components/rightbar/JobList.tsx` | 增加复用同一取消服务的鉴权 API 与最小按钮 |
| 插件强制提供 tools_module | `apps/web/backend/app/catalog/loader.py` | 支持工具/任务/技能/专家的非空可选组合 |
| 插件 Python 模块直接在后端导入 | 同上 `load_plugin_tools/load_plugin_connectors` | 管理员可信部署，禁止用户脚本走此路径 |
| 默认专家播种“存在则跳过” | `apps/web/backend/app/catalog/seed.py` | 不自动覆盖已有助手或偷偷追加权限 |
| shell 没有前端分类与中文标签 | `apps/web/frontend/src/components/chat/` | 只补标签和摘要，复用现有卡片 |

---

## 2. 参考实现：改哪块先读哪块

| 任务 | 本地参考 |
|---|---|
| 工具与执行器分离 | `E:/agent_projects/deepseek-harness/packages/shell/shell/src/`、`packages/shell/tool-bash/src/index.ts`、`packages/shell/bash-sandbox/src/` |
| 命令执行、超时、子进程清理 | `E:/agent_projects/pi/packages/coding-agent/src/core/tools/bash.ts`、`src/utils/shell.ts` |
| 前后台所有权切换及 Job 句柄 | `E:/agent_projects/deepseek-harness/packages/shell/tool-bash/src/background.ts`、`src/index.ts`；结合当前 JobService/JobPoller，不照搬完整调度系统 |
| 中立运行协议、宿主能力边界 | `E:/agent_projects/pi/packages/agent/src/agent.ts`、`E:/agent_projects/jiuwenswarm/jiuwenswarm/runtime/service.py`、`runtime/session/coordinator.py` |
| 权限与管线 | `E:/agent_projects/deepseek-harness/packages/core/tools/src/index.ts` |
| 技能发现及资源定位 | `E:/agent_projects/pi/packages/coding-agent/src/core/skills.ts`、`E:/agent_projects/deepseek-harness/packages/skill/` |
| 工具展示 | `E:/agent_projects/jiuwenswarm/jiuwenswarm/channels/web/frontend/src/components/ChatPanel/ToolGroupDisplay.tsx`、`toolCategory.ts` |
| 新增 token 或布局确有必要时 | `E:/agent_projects/deepseek-harness/packages/client/` 对应实现；本计划正常不需要改布局 |

参考项目适用的 AGENTS.md 也要先检查。只借鉴对应实现，不照搬进程全局状态、宿主任意权限和完整持久终端系统。

---

## 3. 目标目录与内容归属

```text
packages/synlys-harness/src/synlys_harness/
  agent.py / events.py / session.py / compaction.py / types.py
  prompts.py                  通用分段拼装，不含产品身份
  jobs.py                     通用任务状态，不含前端分类
  tools/
    execution.py              执行请求、只读资源描述及路径校验
    sandbox.py                现有执行器与兼容入口，新增 execute
    execution_tools.py        python.run、shell.run 工具适配
    builtin.py                其他既有工具；保留 python_run 导入兼容
    pipeline.py / registry.py

apps/web/backend/
  app/
    runtime/
      __init__.py
      prompts.py              产品提示词装配
      assembly.py             本轮技能、工具与执行环境装配结果
      interactions.py         ask/approval 回调构造
      event_bridge.py         现有 DB/JSONL/SSE 接线，不改变失败策略
    services/
      agent_service.py        准入、运行驱动、控制、唤醒协调
      skill_service.py        技能管理及确定来源的解析入口
      job_connectors.py       宿主插件公共接口的兼容重导出和测试辅助实现
      job_service.py          持久化、身份、配置、任务业务编排
      sandbox_job_runner.py   单进程后台句柄、执行、取消、完成回调
      job_access.py           按任务来源授权与工作区生命周期门禁
    catalog/                  目录扫描与能力治理
    plugins/                  部署配置和插件装配
      contracts.py            公开外部任务连接器协议、异常、注册表
    tools/web_search.py       SearXNG 具体适配
  catalog/
    prompts/                  产品提示词内容（不作为市场条目扫描）
    experts/
    skills/
    plugins/

{data_dir}/
  public/
    catalog/{experts,skills,plugins}/   管理员部署的扩展包
    skills/                           管理员在线编辑的公共技能
  users/<uid>/
    skills/                           用户自建技能
    experts/                          用户自建专家
    workspaces/                       项目工作区
    sessions/<sid>/workspace/         未绑定项目的会话工作区
```

**不新增用户插件代码目录。** “市场安装”只写 `user_capabilities`。团队后续随产品发布的新增能力仍加入仓库 catalog；部署私有扩展加入 `public/catalog`。插件附属技能留在 `plugins/<id>/skills/`，不复制到独立技能根。

---

## 4. 关键接口契约

以下名称供后续任务统一使用。实现可增加私有辅助函数，不得擅自另建同义接口或让不同任务自行改字段名。

### 4.1 执行请求与资源

新建 `tools/execution.py`，公开以下不可变数据描述；实现时添加中文类说明及函数参数说明。

```python
@dataclass(frozen=True)
class ReadOnlyResource:
    source: Path
    target: PurePosixPath


@dataclass(frozen=True)
class ExecutionRequest:
    argv: tuple[str, ...]
    workspace_root: Path
    cwd: str = "tmp"
    resources: tuple[ReadOnlyResource, ...] = ()
    timeout_s: float = 30.0
    max_output_bytes: int = 65536
    execution_id: str | None = None
```

- `workspace_root` 是宿主真实路径，`cwd` 是相对工作区路径。Docker 中工作区恒为 `/workspace`。
- `execution_id` 由宿主生成，后台任务使用 job_id；执行器据此生成安全容器名称/标签并提供 `cleanup_execution(execution_id: str) -> bool`。模型不能指定该字段；清理仅匹配本应用、当前部署命名空间内的精确 ID，不扫描删除无关容器。
- 宿主新增 `SANDBOX_DEPLOYMENT_ID`：未显式配置时，从规范化绝对 data_root 的 SHA-256 前 16 位派生，构造执行器时传入；容器标签至少包含应用标记、deployment_id、execution_id。重启使用同一算法定位本部署容器，不把这个部署策略写入 harness。更换 data_root 或远程 daemon 前由管理员先处理遗留任务。
- 请求只能由工具适配/宿主可信代码构造；工具 schema 不暴露 source、target、image、user、env、network 等权限参数。
- `argv` 不为空；数值必须有限且大于零。工具层使用既有执行超时和输出常量，不依赖上面展示值覆盖原配置。
- 校验函数统一命名 `validate_execution_request(request: ExecutionRequest) -> ExecutionRequest`，返回规范化请求，非法输入抛 `ValueError`；执行器把它转换为失败 ToolResult。
- source 必须是存在的绝对目录；target 必须是规范化绝对容器路径，不得包含 `..`，不得覆盖 `/`、`/workspace`、`/proc`、`/sys`、`/dev`、`/etc`、`/usr`、`/bin` 等执行基础目录，也不得与其他 target 相同或互为祖先。
- 本项目只由宿主生成 `/skills/<合法技能名>` 目标；执行器不认识专家、用户安装、科研平台等概念。
- 工作区和资源目录不能相同或互相包含，防止同一技能同时存在读写别名。
- `CodeExecutor` 增加 `execute(request: ExecutionRequest) -> ToolResult`，保留原 `run(code, cwd, timeout_s, max_output_bytes)` 方法及 `run_python()` 兼容入口。
- `run()` 成为 execute 的 Python 适配，不移除旧类名 `LocalCodeExecutor/DockerCodeExecutor/FailingExecutor`。
- Python 源码入口继续使用隔离模式及 UTF-8；可以使用 `python -I -X utf8 -c <code>` 避免复制另一套脚本/容器生命周期实现。
- 本机执行器 argv 中的 Python 由 `sys.executable` 决定；Docker 用镜像中的 Python，不可把宿主解释器路径传进容器。
- 既有测试假执行器只有 run 时，无资源的 python.run 仍可走旧方法；需要资源但执行器不支持 execute 时明确返回 `executor_capability_missing`，不得忽略挂载继续执行。

### 4.2 shell 工具契约

```text
名称：shell.run
参数：command: string（必填且非空），cwd: string（可选，默认 tmp）
结果：ToolResult；data 保留 exit_code / timed_out / sandbox
```

- Docker 中显式执行 `("/bin/bash", "--noprofile", "--norc", "-c", command)`；镜像需确保 Bash 存在。
- 当前批次仅 Docker 暴露 shell.run。local、local-weak、unavailable 装配时移除，工具直接调用时也返回明确不可用结果，不能在 Windows 自动回落 PowerShell。
- Local 保留现有 Python 开发能力。Local 不具备挂载隔离，不宣称其技能目录只读或安全；资源路径为宿主实际路径，仅用于可信开发。
- 每次新容器；工作区文件保留，shell 状态不保留。不提供 background、TTY、持续会话和环境安装参数。
- 工具设置 `concurrency_safe=False`；不因当前 loop 串行而忽略未来并发声明。
- 不因新增工具而给所有既有专家白名单自动追加 shell 权限。需要后台执行必须调用 job.submit，不能在前台命令加 `&` 后返回并宣称建立了受管理的 Job。

### 4.3 一次解析的技能结果

在宿主 `skill_service.py` 定义：

```python
@dataclass(frozen=True)
class ResolvedSkill:
    name: str
    description: str
    body: str
    directory: Path
    source: str
    plugin_id: str | None = None
```

公开方法：`resolve_skills(user_id: str | None = None) -> list[ResolvedSkill]`。

- 保持用户层优先于公共层，公共层优先于目录内容的现有规则。
- catalog 内部先由 scan_catalog 合并根，部署扩展同 ID 覆盖仓库版；技能服务接收合并后的胜出目录，不能重新按根顺序挑出仓库旧版。
- 同名 catalog 与插件技能冲突时保留当前“独立 catalog 优先、插件按注册顺序”的有效行为并告警；不新增静默不同口径。
- 后端内部结果保留 source path，但列表 API 不向客户端额外暴露服务器绝对目录。
- `list_skills`、`read_body` 保持现有管理调用兼容；运行装配使用 resolve_skills 的同一结果，不再分别 list/read 二次择源。
- 用户层数据仍按现有语义遮蔽同名项。若用户层同名技能损坏或资源不合法，不得静默执行另一个根的同名脚本；记录告警并在本轮排除该技能。

### 4.4 本轮技能访问及执行环境

在 `runtime/assembly.py` 定义：

```python
@dataclass(frozen=True)
class PreparedSkills:
    items: tuple[ResolvedSkill, ...]
    resources: tuple[ReadOnlyResource, ...]
    resource_roots: dict[str, str]


```

资源装配函数签名固定为 `prepare_skills(skills: list[ResolvedSkill], *, sandbox: str) -> PreparedSkills`，具体装配步骤见任务 7。

- 调用前已按用户能力、会话插件开关、requested_skills 筛选。
- Docker：每个有效技能映射到 `/skills/<name>`，resources 保存只读挂载；Local：不生成 Docker 挂载，resource_roots 使用真实来源路径。
- 当前 turn 解析一次；每次工具启动临时容器复用本轮挂载清单。下一轮重新解析配置。
- 挂载全部本轮允许技能，不要求先调用 skill.read 才挂载；这不表示把全部技能正文注入 system prompt。
- `ctx.extra["execution_resources"]`：tuple[ReadOnlyResource, ...]，只给可信执行工具读取。
- `ctx.extra["skill_resource_roots"]`：dict[str, str]，供 skill.read 返回实际执行路径。
- 既有 `skills`、`skill_meta` 继续作为渐进披露正文和索引入口。
- `ctx.extra["skill_resource_reader"]`：异步回调 `(name: str, path: str) -> ToolResult`，闭包仅持有本轮允许技能映射，不对全局 SkillService 重新查询。

### 4.5 skill.read 扩展

保留 `name` 必填参数，增加可选 `path`，默认 `SKILL.md`。

- 默认返回原技能正文，追加清晰的资源根与只读/输出位置说明；data 添加 `resource_root`，保留原 name/content 兼容。
- `path` 非默认时通过受控回调读取包内 UTF-8 文本参考资料；限制最多 48,000 字符并显式标记截断。不支持二进制内容直接返回给模型。
- 拒绝绝对路径、盘符路径、反斜杠绕过、`..` 和解析后逃逸。不存在/非法来源返回明确错误，不回退到其他同名技能。
- 缺资源回调时原有默认正文读取仍有效；读取额外资源明确返回 `skill_resources_unavailable`。
- 不放宽 `file.read/file.write/file.list` 的工作区边界。读取技能参考文件走 skill.read，执行脚本走 python.run/shell.run。

### 4.6 审批、唤醒及插件协议

- `types.py` 新增冻结的 `ApprovalDecision(approved: bool, reason: str = "")`；管线只接受该类型且 `approved is True` 才放行，错误类型、超时或缺回调均拒绝，不把 truthy 字符串当批准。
- HTTP answer 仍接受现有文本；宿主将精确的“允许”映射为 approved=True，其他答复为 False，前端按钮和历史事件不迁移。
- RunSession.run 新增可选 `input_metadata: dict[str, Any] | None = None`；这是仅供宿主调用的内部接口，不直接接收 HTTP 用户自由字段。包含保留键 `text/attachments` 或不可 JSON 序列化值时，在发出 turn/start 前抛 ValueError；其余元数据复制到当前 user/message，核心不定义产品字段白名单。
- 宿主 wake 路径传 `{"kind": "job_completed", "job_id": job_id}`；普通 chat 不传。自动续跑用户插话不复用唤醒元数据，避免把真人插话标成任务通知。
- 外部任务的 JobConnector、异常、RegisteredConnector、JobConnectorRegistry 迁到宿主公开模块 `app.plugins.contracts`，签名保留 submit/poll/cancel 的 dict 上下文形式。它明确是外部科研服务适配契约，不用于强迫沙箱任务返回 external_id、plugin_id 或实现 poll。
- harness 保留 job.* 工具、JobStatus 和宿主注入 job_handler；不新增 `synlys_harness/connectors.py`，不在核心引入 ai4ms_token 专有字段。
- 正式连接器和宿主服务统一从 `app.plugins.contracts` 导入；`job_connectors.py` 仅保留测试辅助连接器，不提供旧协议兼容入口。
- `app.plugins.__init__` 不得因公开导入触发 PluginService 循环导入或启动副作用；必要时简化导出并同步 main 的导入路径。宿主测试辅助连接器仍留 services，不放入公开协议。

### 4.7 通用后台任务入口与参数

保留 `job.submit(kind, params, label)`、job.status/list/cancel 及现有外部任务 kind。宿主按来源分发，不把“没有插件 ID”视为非法 Job。

| kind | params | 执行方式与授权 |
|---|---|---|
| sandbox.python | code: 非空字符串；cwd: 可选相对工作区目录；timeout_s: 可选正数 | Docker Python；要求本轮 python.run 执行权限 |
| sandbox.shell | command: 非空字符串；cwd/timeout_s 同上 | Docker Bash；要求本轮 shell.run 执行权限 |
| sandbox.skill | skill: 本轮技能名；script: 包内 scripts/ 下相对路径；args: 字符串数组；cwd/timeout_s 同上 | .py 用 Python、.sh 用 Bash；要求技能可见及对应解释器执行权限 |
| 已注册外部任务 kind | 沿用插件定义 | 外部连接器；检查所属插件的用户授权与会话开关 |

- sandbox.* 是平台保留命名空间，插件注册时拒绝占用；未知 kind 明确报错，不把任意字符串当 shell 命令。
- 首版三种 sandbox Job 都要求 Docker 可用；前台 Python 的 local 开发兼容不代表后台 Job 可降级到宿主执行。
- 技能入口只执行明确脚本，不自动执行 SKILL.md，不解释多步工作流，不把“读取技能”当作执行权限。
- skill/script/args 校验后构造 argv，不用拼接字符串或 shell=True；script 不允许绝对路径、`..`、越界链接，首版仅支持 .py/.sh。额外命令通过已授权 sandbox.shell 明确执行。
- Python code、Shell command 及技能参数不允许声明 image、mounts、env、user、network、privileged 等执行权限字段。
- 新配置 `SANDBOX_JOB_DEFAULT_TIMEOUT_S=1800`、`SANDBOX_JOB_MAX_TIMEOUT_S=7200`；显式 timeout_s 必须是有限正数且不超过上限，超过直接拒绝，不悄悄截断。
- 默认值与最大值在 Settings 中也需校验，默认值不得大于最大值；最终 ExecutionRequest 始终写入解析好的 Job 超时，不能回落请求对象的前台默认值。
- job.submit 的既有提交超时只覆盖校验、登记、交接；不把前台工具 30 秒预算带入后台 execution。
- `job.status` 返回状态、退出码、限长输出及错误；不在正常轮询里等待任务完成，不默认支持实时无限日志流。
- 后台日志不能无界增长：本项目新建 Job 容器设置轮转上限 max-size=10m、max-file=1；后台结果保存末尾 64 KiB 并明确标注尾部截断，读取 SDK 日志时采用分块处理而非先加载全部输出。不改宿主全局 daemon 配置，也不新增完整日志服务。

调用示例：

```json
{"kind":"sandbox.python","params":{"code":"print(sum(range(1000000)))","cwd":"tmp"},"label":"后台计算"}
```

```json
{"kind":"sandbox.shell","params":{"command":"python /skills/data-analysis/scripts/summarize_csv.py --input /workspace/files/data.csv --output /workspace/output/summary.json"},"label":"生成数据摘要"}
```

```json
{"kind":"sandbox.skill","params":{"skill":"data-analysis","script":"scripts/summarize_csv.py","args":["--input","/workspace/files/data.csv","--output","/workspace/output/summary.json"]},"label":"后台运行数据分析技能"}
```

### 4.8 宿主任务分发与后台句柄

在 `app/services/job_access.py` 定义本轮授权快照，不把此对象作为插件必须实现的协议：

```python
@dataclass(frozen=True)
class JobSubmissionScope:
    allowed_tools: frozenset[str]
    allowed_plugins: frozenset[str]
    skills: tuple[ResolvedSkill, ...]
    resources: tuple[ReadOnlyResource, ...]
    workspace_root: Path
    ownership: dict[str, str]
```

- `ctx.extra["job_submission_scope"]` 由宿主从最终工具可见集、有效插件集、PreparedSkills 和工作区归属构造；JobService 在相应分支内检查。**无插件的 scope 也能提交获准的 sandbox Job。**
- 不再要求通用 job_handler 携带 enabled_plugin_ids；核心工具仍只转发 kind/params/label，由宿主回调捕获可信 scope 和 user/session。
- 前台权限与后台同义：禁止 python.run 时不能绕道 sandbox.python；禁止 shell.run 时不能用 sandbox.shell 或 .sh 技能脚本；按当前已有能力模型授权，不虚构 Python 可以阻止所有子进程调用。
- 插件权限只检查外部插件任务分支。已接收的任务继续使用提交时环境，不因后续关掉插件或更换专家自动取消；取消走显式入口。

在 `app/services/sandbox_job_runner.py` 定义：

| 方法 | 约定 |
|---|---|
| `start(job_id: str, request: ExecutionRequest) -> None` | 同步登记 asyncio.Task 强引用并返回；无等待外部完成，不依赖 AgentService._bg |
| `cancel(job_id: str) -> bool` | 异步请求取消并等待受管理执行清理；仅确认停止后返回 True，已结束按现有终态返回 |
| `shutdown() -> None` | 关闭接收入口，取消并收尾全部持有的沙箱任务；关闭数据库之前完成 |
| `cleanup_execution(job_id: str) -> bool` | 由执行器提供精确容器清理，runner 负责在取消/重启时使用 |

- 上表除 start 外均为 async 方法，Docker SDK 阻塞调用在线程中完成；CodeExecutor 的 execute、run、cleanup_execution 同样是 async。明确停止失败为 False，不用 bool 掩盖“已发请求但未停止”。
- runner 的执行 coroutine：通知 JobService 写 running → await executor.execute(request) → 交给 `finish_sandbox(job_id, result, *, cancelled=False)` 收敛终态。
- `finish_sandbox` 在同一个 JobService 任务锁内重读并更新记录，终态无重复转换；释放锁后再调用既有唤醒入口，避免回调内重入死锁。
- `cancel` 请求和自然完成竞争时，以锁内已经确定的终态为准；成功完成不能被迟到取消覆盖。取消路径不得持有终态更新锁等待 worker，否则 worker 无法完成收尾。
- create_task 后立即登记句柄并返回，后台结果不能取消提交者。任务只在登记及数据库 pending 成功后才启动执行。
- DB 登记失败不启动；任务创建失败将已登记记录标为 failed；提交者在交接前取消则不启动，交接完成后即使 SSE 断开也保留 Job。
- Docker 创建/启动在 to_thread 里时，取消 Python await 不代表线程停止；按 execution_id 可追踪 late-created 容器，待启动收敛后清理，不能在容器尚可能生成时报告“已停止”。
- 不将技能、workspace、token 保存为共享执行器的“当前上下文”；每 Job 独立请求。scope 固定来源路径，但只读挂载不是文件内容快照。

### 4.9 任务文档、状态与结果兼容

- 新增 `backend: "sandbox" | "external"`；当前开发内测数据不保留缺少 backend 的旧文档兼容，新任务必须显式写入来源。
- sandbox 记录 `plugin_id=null`、`external_id=null`，不制造虚假插件 ID 或外部任务 ID。
- 继续使用 pending/running/completed/failed/cancelled，不额外加入前端未知的 interrupted 状态。
- sandbox 新增 `exit_code`、`timed_out`、`truncated`、`error_code`、`workspace_owner`（project_id 或 session_id）。result 保持限长文本，兼容现有任务通知格式。
- 超时为 failed/error_code=timeout；用户明确取消成功后为 cancelled；进程重启中断为 failed/error_code=process_interrupted。
- params 可保留代码或参数供本人追溯，但不能记录解密配置、AI⁴MS token、挂载凭证。API 列表不新增这些内部字段，详情需做安全投影，不能顺手暴露 scope 对象或服务器挂载清单。
- 产物写入提交时工作区的 output；任务结果提供文本及相对路径，沿用 Agent 的 file.send 登记交付，不自动遍历整个工作区认领所有文件。
- 为减少前后台任务共享文件冲突，文档建议输出到 `output/<任务用途>/` 等明确位置；本轮不引入文件事务和完整工作区锁。
- JobPoller 只轮询 external；sandbox 由执行回调直接收敛。job.status(refresh=True) 对 sandbox 只读当前状态，不套用外部 poll。
- 完成后复用现有会话忙时排队唤醒；保留其非持久通知的限制，不宣称 exactly-once。
- 完成唤醒使用当前会话运行规则，但结果属于原提交工作区；若会话工作区已切换，通知注明来源且不把原路径当当前根可直接访问，不自动重跑任务或悄悄拷贝其他项目文件。

### 4.10 停止、取消、删除和重启

- `AgentService.cancel` 仅停止本次回答，不遍历取消 Job。job.cancel 取消指定后台任务并按用户归属鉴权。
- 新增 `POST /api/v1/jobs/{job_id}/cancel`，供既有任务面板直接取消，复用 JobService.cancel；没有新增公开 submit API。非本人 404，不能把参数 user_id 当身份来源。
- 对 sandbox，发出取消请求但尚未确认容器停止时，记录 cancel_requested，不伪造 cancelled；返回可读“停止尚未确认”，重试同一个取消请求是幂等的。
- 外部连接器可能不支持真正取消：保留现有行为但明确标注“仅本地停止跟踪，上游可能继续”，不能与 sandbox 已停止混为一谈。
- 新增 `WorkspaceJobGuard`（job_access.py）负责**本次新增 sandbox 任务**的提交交接和删除门禁。按用户+规范化工作区键使用进程内 asyncio.Lock；提交在锁内复核所属资源仍存在、登记 pending、交接 runner。删除在同一锁内检查 pending/running Job 后再执行原删除，防止检查后提交的竞争。
- 删除会话或项目遇到关联活跃 sandbox Job 返回 409，提示先取消；项目检查所有绑定会话的 Job，不能只看当前 sid。删会话时即使其 Job 使用项目目录，也阻止删除该任务的通知归属会话。
- 同时需要会话和项目门禁时按排序后的稳定 key 获取，所有入口保持同一锁顺序。绑定项目修改不迁移已接收 Job 的工作根；原项目的活跃 Job 删除保护仍有效。
- 正常停机先标记 runner 不再接收 → 停 poller → 取消并清理 runner → 关闭 store。停机取消不触发新的 Agent 唤醒。
- 重启先查 backend=sandbox 且 pending/running 的遗留任务，按部署命名空间+job_id 标签清理对应容器，确认不再运行后标 failed/process_interrupted；不自动重新执行代码。
- daemon 不可达、停止无法确认时保留明确未确认标记和活跃资源保护，不假报清理成功；后续状态查询或后台轻量清理重试仅做清理，不做续跑。这属于不遗留失控执行的基本生命周期，不是完整故障恢复系统。
- 外部 Job 不做重启中断标记，继续既有轮询。严禁启动时把所有外部 pending/running 一起判失败。

---

## 5. 任务与批次

依赖顺序：**任务 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8a → 8b → 8c → 8 → 9 → 10**。保留原任务编号以便跨对话追踪，新增后台任务为 8a—8c，共 13 项任务。任务 4 的执行模型与任务 7 的装配结果是后台任务的前置依赖。每批验证后接续下一批，除新的设计冲突或明确授权事项外，不默认只完成批次 A。

### 批次 A：内核边界与公开协议（任务 1—3）

### 任务 1：迁移产品提示词与 SearXNG 适配

**文件：**
- 修改：`packages/synlys-harness/src/synlys_harness/prompts.py`、`__init__.py`、`tools/builtin.py`。
- 新建：`apps/web/backend/app/runtime/__init__.py`、`apps/web/backend/app/runtime/prompts.py`、`apps/web/backend/app/tools/web_search.py`。
- 新建：`apps/web/backend/catalog/prompts/{identity,workflow,output,workspace}.md`。
- 修改：`apps/web/backend/app/tools/__init__.py`、`app/services/tool_registry.py`、`app/services/agent_service.py`。
- 测试位置：`packages/synlys-harness/tests/test_prompts.py`、`test_builtin.py`；宿主 `tests/test_chat_api.py`、新增 `tests/test_runtime_prompts.py`。

**接口：** 核心提供 `PromptSection(priority: int, text: str)` 与 `render_prompt_sections(sections, variables)`；宿主提供原语义的 `build_system_prompt`，增加可选的执行环境说明及 shell 可用性参数。

- [x] 阅读现有全部提示词及 DSH system-prompt 分段实现，记录原段落排序。
- [x] 获准后将产品提示词断言移到宿主测试；核心仅验证排序、变量替换、空段处理。
- [x] 把产品原文无损迁入四个 Markdown 文件。技能索引、日期、执行器能力说明在宿主构造；不顺手重写科研人设。
- [x] 将 build_system_prompt 调用改到宿主，不在 harness 留“反向导入 app”的兼容函数。同步仓库内全部导入，文档明确该旧产品 API 已迁移。
- [x] 将 web.search 的 SearXNG 请求实现整体移到宿主，工具名、参数、结果不变；宿主 build_registry 显式注册。
- [x] 不将 WeKnora 改造成插件；本任务不改变用户可见能力规则。
- [x] 运行核心提示词、工具注册及宿主提示词/聊天相关回归，确认既有工具名不丢失。

**验收：** harness 不再含科研身份、前端公式渲染限制和 SearXNG 专属参数；Web 原有 prompt 和 web.search 行为保留。

### 任务 2：中立化审批与唤醒协议

**文件：**
- 修改：`packages/synlys-harness/src/synlys_harness/{types,agent,jobs,__init__}.py`、`tools/pipeline.py`。
- 修改：`apps/web/backend/app/services/agent_service.py`。
- 测试位置：harness `tests/test_pipeline.py`、`test_agent_loop.py`、`test_jobs.py`；宿主 `tests/test_agent_wake.py`、`test_chat_api.py`。

**消费/产出：** 采用 §4.6 的 ApprovalDecision 与 input_metadata；HTTP/SSE 既有字段保持不变。

- [x] 阅读 DSH 工具执行前权限判定和当前 ask/approval 等待回路。
- [x] 补核心用例：approved=True 放行；False、字符串、None 拒绝；工具只执行一次。
- [x] 管线先检查参数对象及必填项，再请求审批，避免非法参数先触发问答；本次不引入完整 JSON Schema 引擎。
- [x] 修改宿主审批回调为返回 ApprovalDecision；后台唤醒审批直接拒绝；普通 ask_user 仍返回用户文本。
- [x] 新增 run 的 input_metadata 参数，移除核心对 wake_source 和 job_wake_kind 的认识；同步所有调用及公开导出。
- [x] 在宿主 _drive 的首轮传入唤醒元数据，取出 queued turn 后将其清空。
- [x] 验证保护字段不可覆盖、历史事件仍为 job_completed、普通插话不带唤醒标记。

**验收：** 前端无需调整审批协议；核心不认识按钮文字和任务通知 UI 分类。

### 任务 3：整理宿主插件公开接口与任务来源边界

**文件：**
- 新建：`apps/web/backend/app/plugins/contracts.py`。
- 修改：宿主 `app/services/job_connectors.py`、`app/plugins/__init__.py`、`app/plugins/service.py`、`app/main.py`、`catalog/plugins/spec_agent/connectors.py`。
- 测试位置：宿主 `tests/test_plugin_connectors.py`、`test_job_service.py`、`test_spec_agent_plugin.py`；不为此新增 harness 连接器模块或测试文件。

**消费/产出：** §4.6 的连接器类与相同异常身份；保持现有 Spec_Agent 配置和调用协议。

- [x] 仅在宿主内搬迁外部连接器协议、异常、状态映射条目和注册表至 app.plugins.contracts；公开模块不导入 services、FastAPI 或执行启动装配。
- [x] 旧服务模块仅保留测试辅助类；所有正式插件和宿主消费者改用公开导入。
- [x] 验证正式代码不再从旧服务路径导入连接器协议或异常。
- [x] 检查 app.plugins.__init__ 的导出链并消除循环导入，main 改为显式导入具体实现，不能以导入顺序偶然成功作为验收。
- [x] 文档和类型说明明确：JobConnector 是外部任务适配，不要求平台内置 Job 使用它。保留现有 external 提交流程，本批不制造空的通用 Driver 抽象。
- [x] 注册表拒绝占用 sandbox.* 保留命名空间；保留已有外部任务 kind 与状态映射。
- [x] 分来源授权与沙箱分发在任务 8a 结合实际实现接入，本任务不增加“所有提交都必须有插件集合”的限制。

**验收：** 插件使用稳定宿主公共接口；harness 不固化外部平台模型；现有 Spec_Agent 异步任务不回归。

### 批次 B：统一执行、技能资源与通用后台任务（任务 4—7、8a—8c、8）

### 任务 4：统一执行器并加入 Docker shell

**文件：**
- 新建：`packages/synlys-harness/src/synlys_harness/tools/execution.py`、`execution_tools.py`。
- 修改：`tools/sandbox.py`、`tools/builtin.py`、`tools/__init__.py`、harness `__init__.py`。
- 修改：`docker/sandbox/Dockerfile`，必要时更新同目录说明；不自动安装大型科研套件。
- 测试位置：`packages/synlys-harness/tests/test_sandbox.py`、`test_sandbox_docker.py`、`test_builtin.py`。

**消费/产出：** §4.1 ExecutionRequest/ReadOnlyResource/execute；§4.2 shell.run；旧 run 和 run_python 继续有效。

- [x] 先读 Pi Bash 的取消与进程清理、DSH 工具/执行器分离实现。
- [x] 定义请求对象与校验器，添加最小非法 cwd、冲突 target、资源与工作区重叠用例。
- [x] 将既有 Docker 的 probe/create/wait/logs/cleanup 复用于 execute，command 来自 argv，而不是固定 Python。
- [x] Docker volumes 保留 workspace 的 rw，资源一律 ro；每个资源单独挂载，不先复制进 workspace。
- [x] 所有分支都清理容器：启动失败、等待异常、超时、取消、读取日志失败。容器标签使用通用执行语义，不仅写 python-run。
- [x] 接入可信 execution_id 和精确 cleanup_execution；to_thread 创建容器遇取消时收敛启动线程及晚到的容器，不在 cleanup 仍可能遗漏容器时报告完成。
- [x] Local execute 使用无 shell 的 create_subprocess_exec，Python 使用当前解释器；延续环境白名单。改动触及的超时/取消路径按平台清理子进程树，不能只 kill 父 Python 留下子任务。
- [x] Local 不接受需承诺隔离的只读挂载请求；可信本地技能通过宿主路径访问，结果标记 local。FailingExecutor 同时拒绝 run/execute。
- [x] 将 python.run 的实现搬到 execution_tools，并在 builtin 重导出，避免破坏已有测试导入路径。
- [x] 注册 shell.run，schema 只包含 command/cwd；工具检查 executor.sandbox 必须为 docker。超时与管线预算一致，输出过量有明确截断标记。
- [x] Dockerfile 显式保证 /bin/bash 存在，继续保持非 root、断网、资源上限；不改为特权容器。
- [x] 验证两次调用之间文件保留，但 cd/export/后台进程不延续。

**验收：** Python 老接口可用；Docker 中直接执行 CLI；无法取得 Docker 时 shell 明确拒绝，绝不运行宿主命令。

### 任务 5：统一 catalog 有效内容与可选插件贡献项

**文件：**
- 修改：`apps/web/backend/app/catalog/loader.py`、`items.py`、`app/plugins/service.py`、`app/services/skill_service.py`、`app/main.py`。
- 测试位置：`tests/test_catalog_loader.py`、`test_catalog_items.py`、`test_catalog_seed.py`、`test_plugin_service.py`、`test_plugin_connectors.py`。

**接口：** PluginPackage.tools_module 改为可选空字符串；catalog_roots 与 scan_catalog 对外签名保持；合并后的 `CatalogIndex.skills` 是目录内容的胜出来源。

- [x] 保留根优先级：仓库 catalog → public/catalog，后者同 ID 覆盖并告警。
- [x] manifest 必需项改为 id/name/version；tools_module、connectors_module、skills、expert 四类贡献至少一类有效。
- [x] 未声明模块是正常空贡献，不报警；声明了但缺失/非法则标记包不可用并给出可定位错误，不静默装成空包。
- [x] 校验模块路径为包内相对 .py 文件，不能使用绝对路径或逃逸；源码加载仍仅管理员可信部署。
- [x] 校验声明技能确实存在于该插件 skills 下；不把未声明的额外技能目录自动变成会话可见能力。
- [x] 调整 load_plugin_tools 在未声明时直接返回空；仅连接器插件、仅技能插件、仅专家插件均可枚举和装配。
- [x] 不在本任务实现 ZIP 上传、Git 拉取、自动 pip install 或热加载。新部署包重启扫描；已有目录里的用户安装仍沿用当前注册流程。
- [x] 给 SkillService 增加 `set_catalog_skills(packages: dict[str, SkillPackage]) -> None`，替代 main 只添加第一个 catalog 根；该方法保存胜出的目录映射，不能把整个父目录反向扫描进去。
- [x] SkillPackage 类型引用采用 TYPE_CHECKING，避免 catalog loader 与 skill_service 新增运行时循环导入；该映射用于本批现有 list/read 方法，下一任务再统一为 ResolvedSkill。
- [x] 保留 PluginService.add_root 的插件接入，但传入其 manifest 已声明技能集合，避免“索引枚举 A、运行加载 A+B”。

**验收：** public/catalog 新增和覆盖的能力在目录与运行期使用同一个版本；无须创建空 tools.py。

### 任务 6：技能来源解析、资源合法性与只读文本访问

**文件：**
- 修改：`apps/web/backend/app/services/skill_service.py`。
- 修改：`packages/synlys-harness/src/synlys_harness/tools/builtin.py`（skill.read）。
- 新建：`apps/web/backend/app/runtime/assembly.py` 中资源相关结构和读取辅助函数。
- 测试位置：`tests/test_skill_service.py`、`tests/test_catalog_items.py`、harness `tests/test_builtin.py`。

**消费/产出：** §4.3 ResolvedSkill/resolve_skills；§4.4 的资源读取回调；§4.5 skill.read。

- [x] 先读 Pi/DSH 的技能来源和路径处理，保持当前 frontmatter、名称规则和管理 API 响应兼容。
- [x] 实现 resolve_skills，一次读取正文和元信息，保留来源目录；移除运行侧“读取不到正文就用空串”的静默有效技能。
- [x] 保留既有覆盖优先级；针对公共 catalog 胜出目录直接读取，不二次按名称跨根搜索。
- [x] 首版技能包内拒绝符号链接、Windows junction/reparse 跳转和嵌套挂载类资源；至少保证目录本身和所有可执行资源不存在可越界链接。拒绝项记录技能名、来源和原因。
- [x] 不把硬链接或 ro 挂载描述为宿主更新隔离；本轮不提供内容快照。目录内容在使用期间由管理员避免原地修改。
- [x] 实现闭包绑定的文本资源读取，先校验 name 属于本轮映射，再校验包内相对路径；返回限长文本和资源根。
- [x] 扩展 skill.read 默认正文输出，保持原 data 字段，附加 resource_root；额外 path 没有回调时明确拒绝。
- [x] 不改 file.* 的工作区边界；不把资源源路径加到普通文件 API 的可读根。

**验收：** 同名技能的说明、模板、脚本均来自同一来源；恶意相对路径和包内链接不能读出包外文件。

### 任务 7：接通本轮技能、提示词、挂载和工具权限

**文件：**
- 修改：`apps/web/backend/app/runtime/assembly.py`、`prompts.py`、`app/services/agent_service.py`。
- 修改：harness `tools/execution_tools.py`、宿主 `app/main.py`。
- 测试位置：`tests/test_capability_enforcement.py`、`test_chat_api.py`、`test_agent_wake.py`、新增 `tests/test_runtime_assembly.py`。

**消费/产出：** §4.4 PreparedSkills；execution_resources、skill_resource_roots、skill_resource_reader 统一由本轮装配产出；有效插件集、最终工具集及工作区归属供任务 8a 构造 JobSubmissionScope。

- [x] 将 AgentService 中现有技能/工具筛选原样提炼为 assembly 辅助函数，先保持当前用户能力、会话插件默认全关及专家白名单语义。
- [x] 以 resolve_skills 的结果筛选；plugin_id 不在有效插件集合时直接排除，即使技能名不在旧黑名单也不能进入。
- [x] requested_skills 为 None/[] 仍代表全部可用；只按当前集合取交集，不扩大权限。
- [x] 构造 PreparedSkills：Docker 每项 `/skills/<name>`；资源源目录与工作区不得重叠；Local 则使用真实路径并明确弱隔离提示。
- [x] 同一 items 生成提示词索引、skills 正文、skill_meta、资源路径与只读挂载；禁止另一路再读全局技能字典。
- [x] python.run/shell.run 从执行上下文取挂载清单并构造 ExecutionRequest；每次容器执行都携带该清单，不依赖前一次容器状态。
- [x] 工具列表先遵守专家白名单，再按执行器能力移除 shell.run；不能无条件加入 SKILL_TOOLS。旧自定义专家需要管理员显式增加权限。
- [x] Docker 提示词工作区使用 /workspace，不显示宿主 Windows 路径；Python 默认 tmp 保持不变。shell 可用性和技能资源根与实际环境一致。
- [x] 后台唤醒走同一装配函数，仍禁止 ask_user、拒绝审批；任务通知不能绕过资源筛选。
- [x] 验证 user-a/user-b 的公共 source 相同、workspace 不同、个人技能互不可见；未启用插件无工具、正文、参考资料和挂载。

**验收：** 完成“读取技能 → 读取参考资料 → 在沙箱运行包内脚本 → 输出到本用户工作区”的闭环；没有每用户公共技能副本。

### 任务 8a：扩展 Job 分发、参数和分来源授权

**文件：**
- 新建：`apps/web/backend/app/services/job_access.py`。
- 修改：`apps/web/backend/app/services/job_service.py`、`job_poller.py`、`agent_service.py`、`app/runtime/assembly.py`、`app/core/settings.py`、`app/db/repos.py`（仅需要的任务辅助访问）。
- 修改：`packages/synlys-harness/src/synlys_harness/tools/builtin.py` 的 job.submit 描述和转发；不将平台 kind 的分发硬编码进 harness。
- 测试位置：`apps/web/backend/tests/test_job_service.py`、`test_job_poller.py`、`test_capability_enforcement.py`。

**消费/产出：** §4.7—4.9；JobSubmissionScope、平台 kind 到 ExecutionRequest 的编译入口 `prepare_sandbox_job(kind, params, scope, settings) -> ExecutionRequest`（位于 job_access.py）；外部任务保留现有流程。

- [x] 为三种 sandbox kind 定义独立严格参数模型；拒绝多余权限字段、非法 timeout、非字符串 args、脚本逃逸。验证失败不创建 Job、不启动容器。
- [x] JobSubmissionScope 由当前装配结果生成并经 job_handler 闭包注入；沙箱分支检查对应工具权限，外部分支检查 allowed_plugins。
- [x] 不修改核心 job.submit 为只允许 sandbox.* 枚举；宿主编写工具说明补充平台任务示例，仍保留插件 kind 扩展。
- [x] 提交流程明确 backend：sandbox 不查插件配置、不调用外部连接器、不生成伪 external_id；external 沿用解析配置与提交逻辑，本轮不重做外部提交幂等。
- [x] sandbox.pending 必须在启动前落库，记录真实 user/session/workspace_owner；登记失败不调用执行器。首次运行仅接收 Docker executor，不走 local-weak。
- [x] JobService 增加可注入的 sandbox runner 接口，未装配时返回明确不可用结果，不建无法执行的 pending；本任务可先用假 runner 测试，真实实现由 8b 提供。
- [x] JobPoller 只刷新 external；JobService.describe 的 sandbox 分支只读取文档；缺 backend 的开发期旧记录不兼容。
- [x] 加入配置默认 1800 秒、最大 7200 秒及正数/有限值校验，前台 30 秒预算与 Job 独立。
- [x] 验证插件全关但 python.run 获准仍能提交 sandbox.python；未启用外部插件无法提交其任务；禁止 shell 的专家无法借 sandbox.shell/.sh 绕过。

**验收：** 统一入口可区分平台与插件任务，不强制所有任务携带 plugin_id；授权和参数错误在执行前清楚失败。

### 任务 8b：实现沙箱后台句柄、终态与取消

**文件：**
- 新建：`apps/web/backend/app/services/sandbox_job_runner.py`。
- 修改：`apps/web/backend/app/services/job_service.py`、`app/main.py`、`packages/synlys-harness/src/synlys_harness/tools/sandbox.py`（所需执行生命周期接口）。
- 测试位置：新增 `apps/web/backend/tests/test_sandbox_job_runner.py`；扩展 `test_job_service.py`、`test_agent_wake.py`、`test_jobs_e2e.py`、核心 `test_sandbox_docker.py`。

**消费/产出：** §4.8 的 runner 生命周期及 `finish_sandbox`；沿用原 JobStatus 和现有完成唤醒回路。

- [x] runner 保存 job_id → asyncio.Task 强引用；start 为同步交接，不 await executor.execute，不把 Job 放进当前 RunSession 或 AgentService._bg。
- [x] 执行请求复制提交时的资源清单与 workspace，execution_id 使用 job_id；执行前不重新按技能名选来源。
- [x] 后台标记 running 后执行同一个 CodeExecutor.execute，返回时调用 finish_sandbox；result.ok/退出码/超时映射准确，非零不能记 completed。
- [x] 只对后台 Job 容器增加有界日志配置和尾部限长采集，不改变前台兼容入口的输出方向；若日志驱动不支持所需选项，启动明确失败，不悄悄使用无界日志。
- [x] 对创建容器失败、execute 抛错、CancelledError 分别收敛并清理；数据库终态异常如实日志报告，不将错误隐藏为成功，不扩展成通用重试队列。
- [x] 任务锁只保护状态转换，不跨整个 execution 持有；取消路径先取得句柄再发取消、等待清理，最后收敛状态，避免与 finish_sandbox 互相等待。
- [x] 完成与取消竞争只产生一个有效终态转换；锁外触发现有通知，失败通知不得自动重新提交原代码。
- [x] 取消正在创建容器的任务也能按 execution_id 清理晚到容器；停止未确认时保留 cancel_requested，而非先写 cancelled。
- [x] 停止用户当前回答、SSE 断连、Agent turn 结束均不停止 Job；job.cancel 才停止指定计算。
- [x] 完成文本包含任务类型、状态、退出码、有限输出及工作区归属，不包含凭证；产物通过已有 file.send 处理。
- [x] 使用 asyncio.Event 控制的假执行器验证“submit 已返回但任务仍阻塞运行”，不要用大段 sleep 或真实耗时计算做单元测试。

**验收：** 三种 sandbox Job 真正在后台运行；当前对话可结束/继续；结果、取消和唤醒形成闭环。

### 任务 8c：任务 API、工作区保护与停机中断

**文件：**
- 修改：`apps/web/backend/app/api/jobs_api.py`、`sessions_api.py`、`projects_api.py`、`app/services/project_service.py`、`job_access.py`、`job_service.py`、`sandbox_job_runner.py`、`app/main.py`。
- 修改：`apps/web/frontend/src/components/rightbar/JobList.tsx`、`apps/web/frontend/src/types.ts`（JobDoc 定义）。
- 测试位置：`tests/test_jobs_api.py`、`test_projects_api.py`、`test_chat_api.py`、`test_sandbox_job_runner.py`、`test_jobs_e2e.py`。

**消费/产出：** §4.9—4.10 的状态投影、鉴权取消端点、WorkspaceJobGuard、停机与重启语义。

- [x] 添加 POST /jobs/{id}/cancel，用户身份取鉴权上下文；调用同一 JobService.cancel，不复制取消逻辑。异用户查询/取消维持 404。
- [x] 任务列表增加 backend 和 cancel_requested 等安全状态字段，详情明确过滤运行凭证及源挂载清单。
- [x] 工作区门禁检查覆盖 sandbox pending/running、创建交接中状态；提交与删除共用锁并在锁内复核资源存在。测试并发删除/提交，不能只用一次 list_active 检查冒充原子保护。
- [x] 删除会话/项目有活跃 Job 返回 409；用户取消后等待停止确认再允许删。项目门禁覆盖多个会话共用项目目录的情况。
- [x] main 装配 runner、JobService 和 guard，停机时在 store.close 前停止接收并清理全部后台执行；关闭过程不触发 Agent 唤醒。
- [x] 启动仅清理本部署 sandbox 遗留容器和任务，不触碰 external Job；确认停止后写 process_interrupted，未确认则继续保护工作区且展示原因。
- [x] JobList 增加复用现有按钮样式的取消操作及请求中状态；阅读 DSH 对应任务列表与 Jiuwen 操作行实现，不设计新界面。取消点击不自动停止当前聊天。
- [x] 用户取消后的自动通知只说明取消结果；完成唤醒提示不得诱导重新提交相同任务。重启清理、停机取消不触发新的后台计算或 Agent 轮。
- [x] shell/Python Job 输出存在共享项目时按提交归属说明，不把会话切换后的不同 workspace 路径误用作产物路径。
- [x] 运行 API、删除保护、取消竞争回归及前端 build；真实容器清理测试在获得许可时运行。

**验收：** 用户可不依赖模型回答而取消后台任务；运行目录不会被删除；重启明确中断、不自动重复执行、不遗留可见却失控的容器。

### 任务 8：最小产品接入与真实容器演练

**文件：**
- 修改：`apps/web/frontend/src/components/chat/ToolCallCard.tsx`、`toolLabels.ts`。
- 修改：`apps/web/backend/catalog/experts/data-analyst/expert.json`、`research-assistant/expert.json` 的新部署默认白名单（仅适合命令执行的专家）。
- 修改：`apps/web/backend/catalog/skills/data-analysis/SKILL.md`。
- 新建：`apps/web/backend/catalog/skills/data-analysis/scripts/summarize_csv.py`。
- 测试位置：既有 Docker 测试文件、宿主核心装配和 Job 集成测试；不另建脚本专用测试套件。

**示例脚本契约：** 使用标准库 argparse/csv/json，接收 `--input` 和 `--output`，输出包含列名和数据行数的 UTF-8 JSON；失败返回非零，输出路径由用户明确传入。脚本是保留的产品资源，不是临时验证文件。

- [x] 阅读 Jiuwen 工具分类和详情展示实现，只为 shell.run 增加命令类别、首行命令摘要和中文标签，不增加新布局/token。
- [x] 为新部署的通用科研/数据专家模板显式加入 shell.run；不修改 Spec_Agent 受限专家，除非其技能实际需要命令且用户另行同意。
- [x] 已落库专家仍“存在则不覆盖”；升级说明要求管理员选择性增加白名单，禁止批量扩大现有权限。
- [x] 添加上述小型 CSV 资源脚本及技能使用说明，按脚本所在目录定位自身资源，输入输出使用显式绝对工作区路径。
- [x] 不因 Python -I 导致包内辅助模块不可导入而去掉隔离参数；该示例保持单文件，未来多文件脚本按技能说明显式处理模块路径。
- [x] 在获准且 Docker 可用时，验证只读技能文件可执行，写入技能目录失败，output 文件可读取；第二个用户的容器不含第一个用户私有技能。
- [x] 同一 CSV 示例分别验证前台 shell 与后台 sandbox.skill；另用最小 Python/Shell 命令验证两个后台入口，确认工具立即返回 job_id、执行后任务面板更新、唤醒带结果。
- [x] 验证 job.cancel 实际停止一个受控长任务并删除容器；演练停止聊天不停止 Job、关插件不重新选挂载、重启不重跑代码。不调用真实科研 API。
- [x] 若镜像缺 Bash 或运行依赖，记录为阻断项，不改成宿主执行来“通过验证”。
- [x] 执行前端 npm run build；不启动浏览器验证循环。

**验收：** 一个真实、无需外部 API 的科研数据技能以前台和后台两种方式跑通；工具展示复用现有风格。

### 批次 C：宿主职责收口与文档交付（任务 9—10）

### 任务 9：拆分 AgentService 接线，保持运行行为

**文件：**
- 新建：`apps/web/backend/app/runtime/interactions.py`、`event_bridge.py`。
- 修改：`apps/web/backend/app/services/agent_service.py`、`runtime/assembly.py`。
- 测试位置：`tests/test_chat_api.py`、`test_agent_wake.py`、`test_jobs_e2e.py`、`test_e2e.py`。

**接口约定：**
- interactions 提供 `make_approval_handler(ask_handler, *, unattended: bool)`，返回 ApprovalDecision 回调；不负责 HTTP 请求或用户归属查询。
- event_bridge 提供 `make_event_sinks(*, session_id, user_id, event_repo, jsonl_path, queue)`，返回现有 EventLog sink 列表。
- assembly 只返回构造结果与显式回调，不持有 ActiveRun 全局表，不直接写 SSE。

- [x] 先读 Jiuwen RuntimeSessionCoordinator 的职责边界，不照搬其全局服务或完整调度器。
- [x] 从 AgentService 提取已验证的审批回调及事件 sink 构造，不在这一任务改变吞错、seq、TRANSIENT、断线不取消等语义。
- [x] 保留 ActiveRun、会话互斥、用户准入、_drive、cancel/steer/answer/wake 在原服务，避免本轮引入新调度模型。
- [x] 文件交付保留既有服务路径，本次不为了清空大文件而拆出不必要的数据访问层。
- [x] 不让 runtime 模块导入 FastAPI；HTTP 映射留 API 层，身份和插件配置仍由宿主组装。
- [x] 测试插话续跑、问答超时、审批拒绝、后台让位、文件交付及任务唤醒无回归。

**验收：** 能力装配、交互接线、事件桥接分别可读；AgentService 不再同时展开全部细节，但行为保持。

### 任务 10：开发指南、兼容说明与最终验证

**文件：**
- 修改：`README.md`、`packages/synlys-harness/README.md`、`apps/web/backend/README.md`、`apps/web/backend/.env.example`。
- 新建：`docs/development/plugin-and-skill-guide.md`。
- 修改：本计划勾选项与执行记录；必要时更新对应路线图的简短链接，不重写全部历史计划。

- [x] 文档解释三层职责、执行接口、临时容器语义及目录放置规则。
- [x] 文档明确 job.submit 为统一入口而非插件专属工具，给出 §4.7 三种后台沙箱请求和一个现有外部插件任务示例。
- [x] 补充提交立即返回、停止回答不停止 Job、独立运行超时、取消未确认、外部取消能力差异、删除 409、重启不续跑及有限日志语义。
- [x] 同步 .env.example 的 SANDBOX_JOB_DEFAULT_TIMEOUT_S、SANDBOX_JOB_MAX_TIMEOUT_S、SANDBOX_DEPLOYMENT_ID；不将轮询完成或任务启动误写为“当前只做 DB 往返”。
- [x] 写完整插件 manifest 示例：工具插件、连接器插件、纯技能/专家包；每个示例都使用本次实际接口。
- [x] 技能示例包含 SKILL.md、scripts、references、assets，说明资源只读、工作区可写、如何查 resource_root。
- [x] 明确公共单份存储、每次容器单独挂载；不声称只读挂载是快照。
- [x] 明确宿主目录必须对 Docker daemon 可见；远程 daemon 不在本次支持范围。后端若以后也容器化，需保证 daemon 所见源路径一致，不能直接使用后端容器独有路径。
- [x] 管理员部署流程：放置完整包 → 检查依赖 → 重启后端 → 配策略/配置 → 用户安装与会话启用。修改宿主依赖使用既有 conda 环境，不允许模型运行期安装。
- [x] 当前用户在线技能编辑器仍主要编辑 Markdown；本次不增加目录上传 UI。附属脚本由受信任的部署/文件管理流程放置，不能承诺已有在线上传功能。
- [x] 发布包必须同带 catalog/prompts、技能、插件以及前端 dist；现有 pyproject 只打包 app，文档明确按仓库目录部署，本轮不把 catalog 偷偷遗漏到 wheel 外还宣称支持 wheel 独立部署。
- [x] 兼容列表：python.run/CodeExecutor.run 保留；提示词产品 API 与 job_wake_kind 移出核心；审批回调返回类型变化；现有仓库消费者同步更新，外部消费者需依指南迁移。
- [x] 说明新 Job 文档必须显式 backend；sandbox 不要求 plugin_id/external_id；连接器公共导入为 app.plugins.contracts，不保留旧入口。
- [x] 修正后端 README 本地包安装相对路径：从 apps/web/backend 到 packages 应为 `../../../packages/synlys-harness`，仅在本次更新安装说明时同步修正。
- [x] 版本：仅本计划不改版本。实现交付时根据实际公开 API 变化核对语义化版本，不能只凭新增 shell 就断定无破坏性变更；推送前按用户要求统一版本口径。
- [x] 当前记录：应用显示版本 0.12.0-beta.1，两个 Python pyproject 为 0.1.0。执行时重新核实；不得把旧值当最新值或未经确认大规模统一版本体系。
- [x] 执行 §6 验证，记录命令、通过/失败/跳过及原因，最终检查 diff 只含本次内容。

**验收：** 后续模型/开发者能只参考指南增加新科研插件或技能，知道依赖、路径、权限和生效方式。

---

## 6. 验证命令与核心验收矩阵

### 6.1 环境和基线（执行模型运行，本次未运行）

在仓库根目录：

```powershell
git status --short
conda env list
conda run -n synlysagent python --version
```

预期：明确用户已有修改，存在 synlysagent 且 Python 3.12；不满足先报告，不安装到系统 Python。

### 6.2 分批测试

核心新增测试取得一次确认后，先运行改动相关文件，随后再完整回归：

```powershell
Set-Location E:\agent_projects\Synlora\packages\synlys-harness
conda run -n synlysagent python -m pytest tests/test_pipeline.py tests/test_agent_loop.py tests/test_jobs.py -q
conda run -n synlysagent python -m pytest tests/test_sandbox.py tests/test_builtin.py -q
conda run -n synlysagent python -m pytest -v
```

```powershell
Set-Location E:\agent_projects\Synlora\apps\web\backend
conda run -n synlysagent python -m pytest tests/test_skill_service.py tests/test_catalog_loader.py tests/test_plugin_connectors.py -q
conda run -n synlysagent python -m pytest tests/test_capability_enforcement.py tests/test_chat_api.py tests/test_agent_wake.py -q
conda run -n synlysagent python -m pytest -v
```

完成任务 8a—8c 且新增 runner 核心测试文件后，单独验证后台生命周期：

```powershell
Set-Location E:\agent_projects\Synlora\apps\web\backend
conda run -n synlysagent python -m pytest tests/test_sandbox_job_runner.py tests/test_job_service.py tests/test_job_poller.py tests/test_jobs_api.py tests/test_jobs_e2e.py tests/test_projects_api.py -v
```

新增测试文件在创建后加入对应批次命令；不能执行不存在的文件并忽略失败。

Docker 测试先读现有文件的 skip 条件，再运行：

```powershell
docker version
docker image inspect synlora-sandbox:latest
Set-Location E:\agent_projects\Synlora\packages\synlys-harness
conda run -n synlysagent python -m pytest tests/test_sandbox_docker.py -v -rs
```

daemon 不可达、镜像未构建等导致的 skipped 必须记录，不能作为 ro 隔离实测证据。

前端与差异：

```powershell
Set-Location E:\agent_projects\Synlora\apps\web\frontend
npm run build
Set-Location E:\agent_projects\Synlora
git diff --check
git status --short
```

预期：相关测试与构建返回 0；基线已有失败单独说明，不修复无关问题。

### 6.3 最少核心用例（不是要求每条单建测试文件）

| 编号 | 场景 | 通过条件 |
|---|---|---|
| V01 | 核心依赖边界 | harness 无 app/FastAPI/科研产品身份/SearXNG 专属实现 |
| V02 | 审批 | 明确批准才执行，错误类型和无人值守拒绝 |
| V03 | 唤醒与插话 | 任务首条消息有旧标记，接续真人插话无该标记 |
| V04 | 连接器导入 | 宿主 app.plugins.contracts 可用，旧路径异常 identity 不变，核心不新增外部连接器模型 |
| V05 | 分来源任务权限 | 无插件也可提交授权 sandbox 任务；未启用插件的外部 kind 在调用前拒绝 |
| V06 | Python 兼容 | 旧 run、python.run、输出字段与 tmp 语义可用 |
| V07 | shell 与降级 | Docker shell 可执行，local/local-weak/不可用均不执行宿主 shell |
| V08 | 路径约束 | cwd 逃逸、target 覆盖、资源与工作区重叠拒绝 |
| V09 | 容器资源 | source 为既有技能目录，target 为 /skills/name，挂载 ro |
| V10 | 多用户 | 共享技能 source 相同、工作区不同、个人技能不串用户 |
| V11 | 插件会话开关 | 关闭插件后新轮无其工具、正文、参考文件与资源挂载 |
| V12 | 同名覆盖 | 说明与脚本来自同一胜出目录，public/catalog 覆盖实际生效 |
| V13 | 技能资源读取 | 参考文件可读，越界/链接/二进制拒绝，长文本显式截断 |
| V14 | 只读实测 | 可运行脚本，不能改技能文件，产物写入用户工作区 |
| V15 | 执行生命周期 | 超时/取消/启动失败/日志异常清理容器，无后台进程遗留 |
| V16 | 可选贡献 | 无 tools.py 的连接器包、技能包、专家包可装配；空贡献拒绝 |
| V17 | 既有专家 | 已落库白名单不被自动放大，用户可显式授权 shell |
| V18 | 无复制 | 创建多个会话不产生公共技能的用户目录副本 |
| V19 | 默认产品示例 | CSV 技能脚本生成预期 JSON，文件交付仍可用 |
| V20 | 前端 | shell 工具行与原卡片风格一致，构建通过 |
| V21 | 提交后台化 | executor 尚未完成时 submit 已返回 job_id，pending 已落库且 runner 持有句柄 |
| V22 | 三种平台 kind | sandbox.python/shell/skill 均可执行，sandbox.skill 使用 argv 和已解析的资源目录 |
| V23 | 对话独立性 | turn 结束、停止当前回答、SSE 断连不停止已交接 Job |
| V24 | 预算分离 | 前台提交预算不终止长任务；独立 timeout 生效，超时停止容器且任务 failed |
| V25 | 后台权限 | 禁止 python/shell 时不能从对应后台入口或技能解释器绕过；越界脚本和任意挂载参数拒绝 |
| V26 | 状态兼容 | sandbox 无 plugin_id/external_id 也工作；无 backend 的旧任务仍走 external 轮询 |
| V27 | 完成唤醒 | sandbox 不经过外部 poll，完成回调写结果后复用现有唤醒；通知不重复执行原任务 |
| V28 | 取消竞争 | 用户取消确认容器停止；与自然完成竞争不覆盖既定终态，不因任务锁死锁 |
| V29 | 直接取消 API | 本人可从任务面板取消，异用户 404；停止未确认如实显示 |
| V30 | 工作区保护 | 活跃 sandbox Job 阻止相关会话/项目删除；提交与删除竞争无孤立运行目录 |
| V31 | 正常停机 | runner 停止接收、清理任务后关闭 DB，关闭阶段不唤醒新 Agent |
| V32 | 重启中断 | 只清本部署遗留 sandbox 容器，停止确认后写 process_interrupted，不重跑、不判死外部任务 |
| V33 | 资源固定 | 后台使用提交时技能与工作根；后续改会话绑定不让任务读取新用户/新目录 |
| V34 | 登记与启动失败 | DB 失败不启动容器；交接失败收敛记录；启动线程取消无晚到容器泄漏 |
| V35 | 长任务日志 | 结果限长且标注截断，容器日志有界，详情不暴露运行凭证和挂载清单 |

获准新增测试后的最小断言示例，实际使用现有 fixture，不新建独立测试框架：

```python
assert prepared_a.resources[0].source == prepared_b.resources[0].source
assert prepared_a.resource_roots["data-analysis"] == "/skills/data-analysis"
assert "private-a" not in prepared_b.resource_roots
assert create_kwargs["volumes"][str(skill_dir)]["mode"] == "ro"
assert create_kwargs["volumes"][str(workspace_dir)]["mode"] == "rw"
assert not (user_root / "skills" / "data-analysis").exists()
```

只验证“SDK 收到 ro 参数”不等于已实测内核拒绝写入；V14 必须运行 Docker，否则明确标注未完成。

---

## 7. 兼容性与易踩坑清单

1. **不要复制所有技能到 workspace。** 这会破坏公共单一来源，且让模型改写公共能力副本。
2. **不要把 plugins 根整体挂进去。** 只挂获准技能目录；插件工具模块、密钥配置不进入通用执行环境。
3. **不要只改 shell 工具。** 技能来源、执行器、提示词、正文路径和资源读取必须配套。
4. **不要在共享 DockerCodeExecutor 上保存“当前用户挂载”。** 执行器是部署级共享对象，资源必须随 ExecutionRequest 传入，避免并发串用户。
5. **不要依赖一次运行的挂载影响下一次容器。** 每次 execute 都显式装配资源，文件持久化仅靠 workspace。
6. **不要按目录名二次查技能。** 已解析的 ResolvedSkill 是本轮唯一来源。
7. **不要直接追加所有 catalog 根。** 现有根遍历是先到优先，与 scan_catalog 的后到覆盖相反，应使用合并后的胜出目录。
8. **不要给旧专家无条件加 shell。** 模型工具可见集仍尊重白名单；用户自己已有 Python 权限也不意味着本计划可以自动放宽配置。
9. **不要认为 local 也具备只读隔离。** 宿主实际文件权限不因 Python 描述对象变为只读；本地仅可信开发。
10. **不要把脚本运行依赖当作技能文件自动携带。** 只读挂载不会安装 Python 包、R 或系统 CLI；镜像依赖由管理员构建。
11. **不要默认 shell 继承 API 凭证。** 现有插件配置和 AI⁴MS token 不得复制到容器环境、命令文本、挂载文件或 prompt。
12. **不要原地更新正在使用的公共技能。** 首版管理员维护时更新并重启，ro 不是快照，不承诺运行中版本固定。
13. **不要假设技能包全部文件都能用 Markdown 编辑器上传。** 当前目录资源部署与在线正文编辑的能力分开说明。
14. **不要在核心内留产品兼容导入 app。** 真正迁移公开产品 API 时同步消费者并写迁移说明。
15. **不要把本轮扩大为可靠性专项。** 事件吞错、通知持久化、容量治理保留现状并标注，不夹带修复。
16. **不要把外部连接器当作所有任务协议。** sandbox Job 无需虚假插件、external_id 或轮询适配器；harness 只认识 job_handler。
17. **不要只 create_task 就认为后台化完成。** 必须登记任务、持有句柄、处理终态/取消、清理容器、保护工作区和标记重启中断。
18. **不要复用前台取消链和超时。** Job 自己拥有执行生命周期，前台 submit 只负责接受与交接。
19. **不要持有 Job 状态锁等待取消完成。** 终态回调也需要该锁，容易造成死锁。
20. **不要让 job.submit 成为白名单绕过入口。** 分来源授权，不限制为插件任务，也不取消实际执行权限检查。
21. **不要把 SKILL.md 当可执行工作流。** sandbox.skill 只执行获准技能包中的明确脚本。
22. **不要在取消状态写入后假设进程已停止。** 尤其要收敛 to_thread 容器启动的晚到结果；未确认时继续保护资源。

---

## 8. 执行记录与模型交接

### 8.1 批次状态

- [x] 执行前：确认核心测试范围与 Docker 测试许可。
- [x] 批次 A：任务 1—3，内核与插件公共接口收口。
- [x] 批次 B：任务 4—7、8a—8c、8，执行器、技能资源、通用后台任务与产品闭环。
- [x] 批次 C：任务 9—10，职责整理、指南与整体回归。

### 8.2 每批结束必须记录

- 实际完成任务及变动文件。
- 与计划不同的接口或行为、原因以及影响的后续任务。
- 已运行命令及结果；未运行和跳过项的原因。
- 当前未提交改动和用户原有改动是否保持。
- 下一批从哪个任务开始，有无需要用户确认的问题。

### 8.3 当前交接状态

- 已完成：批次 A、B、C 全部实施；生产代码、配置、文档、版本和核心测试已更新。
- 未执行：提交、推送、分支或工作树创建；未调用真实模型、科研服务或实验设备。
- 当前工作区：所有实现均为未提交修改；用户原有的本计划文件继续保留。
- 后续起点：先阅读本节各批执行记录和最终验证结果，再由用户决定审阅、提交或继续功能开发。

建议交接指令：

> 请完整阅读 `docs/superpowers/plans/2026-09-17-synlora-17-extensible-runtime-and-skill-sandbox.md` 第二版及适用项目指令，核对 Git 状态后，按依赖顺序完成批次 A、B、C（包含新增任务 8a—8c）。job.submit 必须同时支持后台 Python、Shell、技能脚本和现有外部插件任务，不要按旧版“所有 Job 必须绑定插件”实施。先一次性确认核心测试范围；各批验证并更新记录后继续，不必每批等待确认，但设计冲突、权限变化及破坏性偏差需询问。不创建分支/工作树、不提交推送，不做明确暂缓的分布式和完整恢复改造。如中途停止，先记录准确进度和下一步。

### 8.4 批次 A 执行记录（2026-09-17）

- 完成任务 1—3：harness 提示词仅保留通用分段渲染；产品提示词和 SearXNG 迁至宿主；审批改为 `ApprovalDecision`；唤醒通过 `input_metadata` 传递；外部连接器公开接口迁至 `app.plugins.contracts`，正式消费者不再使用旧协议路径。
- 主要变动文件：`packages/synlys-harness/src/synlys_harness/{prompts,types,agent,jobs,__init__}.py`、`tools/{pipeline,builtin}.py`；`apps/web/backend/app/runtime/prompts.py`、`app/tools/web_search.py`、`app/plugins/contracts.py`、`app/services/{agent_service,job_connectors,tool_registry}.py`、`catalog/prompts/*.md` 及对应核心测试。
- 验证：harness 完整回归 `151 passed`；Web 后端完整回归 `584 passed, 198 skipped`。跳过项为既有 MongoDB/外部环境条件；出现 1 条 Starlette/AnyIO 第三方弃用警告。既有 Docker 测试在 harness 完整回归中通过。
- 计划偏差：无接口或权限偏差。Windows 下 `conda run` 默认输出编码曾触发 GBK 包装器异常，后续命令显式设置 `PYTHONIOENCODING=utf-8` 并使用 `--no-capture-output`。
- 工作区：用户原有未跟踪计划文件保留；未提交、未推送、未创建分支或工作树。
- 下一步：批次 B 从任务 4“统一执行器并加入 Docker shell”开始。

### 8.5 批次 B 执行记录（2026-09-17）

- 完成任务 4—7、8a—8c、8：统一 `ExecutionRequest`/只读资源/执行器；新增 Docker `shell.run`；catalog 胜出目录和插件可选贡献；`ResolvedSkill` 一次解析与资源读取；本轮技能装配；三种 sandbox Job、后台 runner、取消 API、工作区门禁、停机与重启中断；CSV 产品技能与前端最小展示。
- 主要变动文件：harness `tools/{execution,execution_tools,sandbox,builtin}.py`；宿主 `runtime/assembly.py`、`services/{skill_service,job_access,sandbox_job_runner,job_service}.py`、catalog loader、API/main/settings；前端工具卡和 JobList；`data-analysis/scripts/summarize_csv.py` 及相关测试。
- 验证：harness 完整回归 `159 passed`；Web 后端完整回归 `600 passed, 199 skipped`；前端 `npm run build` 通过。真实 Docker 验证了 Shell、ro 技能挂载、三种后台 kind、CSV 技能前台/后台执行、长任务取消与容器清理。
- 计划偏差：根据用户在执行中的明确决定，不保留历史插件协议导入、旧 manifest 降级或缺 `backend` 的历史 Job 兼容。正式插件及宿主统一使用 `app.plugins.contracts`，新 Job 必须显式 `backend`；测试辅助连接器单独留在 services。此偏差简化新架构，不影响当前开发内测数据承诺。
- 验证说明：MongoDB 参数化和 Windows 未开放符号链接权限的用例按既有条件跳过；1 条 Starlette/AnyIO 第三方弃用警告；前端有既有 chunk 大小提示。Docker 镜像已有 Bash，本批无需重建或拉取。
- 工作区：用户原有未跟踪计划文件保留；未提交、未推送、未创建分支或工作树。
- 下一步：批次 C 从任务 9“拆分 AgentService 接线”开始。

### 8.6 批次 C 执行记录（2026-09-17）

- 完成任务 9—10：从 AgentService 提取 `runtime/interactions.py` 和 `runtime/event_bridge.py`，保留 ActiveRun、准入、控制、唤醒和文件交付职责；新增插件/技能/后台任务开发指南并同步三份 README、`.env.example` 和部署说明。
- 主要变动文件：`apps/web/backend/app/runtime/{interactions,event_bridge}.py`、`app/services/agent_service.py`、`docs/development/plugin-and-skill-guide.md`、根/核心/后端 README、`.env.example` 及版本文件。
- 行为偏差：继续遵循用户的新架构决定，不提供旧插件协议或缺 backend Job 文档兼容。任务 9 的拆分不改变事件吞错、瞬态过滤、SSE 断线不取消和唤醒队列语义。
- 版本：应用版本从 `0.12.0-beta.1` 升至 `0.13.0-beta.1`；harness 与 Web Python 包从 `0.1.0` 升至 `0.2.0`，前端 package/package-lock 与后端健康检查版本同步。
- 阶段验证：任务 9 的 chat/wake/jobs/e2e 回归 `78 passed, 13 skipped`；最终全量为 harness `159 passed`、Web 后端 `601 passed, 199 skipped`，前端构建通过；Docker 残留执行容器为 0，`git diff --check` 与依赖边界检查通过。
- 工作区：未提交、未推送、未创建分支或工作树；下一步由用户审阅改动并决定是否提交。

### 8.7 后续交互调整：任务终态不自动回复（2026-09-17）

- 产品决定：后台任务完成、失败或取消后只更新 Job 文档和右侧“运行信息”，不再自动唤醒 Agent，也不在聊天区创建新的模型回复。用户可查看右栏，或在后续对话中主动要求调用 `job.status` / `job.list` 查询和总结；`job.cancel` 与右栏取消保持不变。
- 实现变动：删除 `JobService` 的 wake 回调、忙时排队和 drain 机制；删除 `main.py` 的 JobService/AgentService 双向唤醒接线；移除 `AgentService` 中仅供后台任务自动轮使用的 `wake_source`、`wake()`、自动 run 类型和运行结束钩子；任务状态机、结果回填、外部轮询、sandbox runner、取消和工作区保护不变。
- 主要修改文件：`apps/web/backend/app/services/{job_service,agent_service,sandbox_job_runner,session_runtime,job_poller}.py`、`app/main.py`、相关 Job/Agent 测试；`packages/synlys-harness/src/synlys_harness/tools/builtin.py`；根 README、后端 README、插件与技能开发指南、Spec_Agent 技能说明。
- 测试过程：先新增端到端断言“外部任务终态落库但不启动新的 Agent 对话”，旧实现按预期失败；移除自动唤醒后该用例通过。相关后端回归 `133 passed, 54 skipped`。最终完整回归：harness `159 passed`；Web 后端 `583 passed, 183 skipped`，仅有 1 条既有 Starlette/AnyIO 弃用警告；前端 `npm run build` 通过，仅有既有 chunk 大小提示。
- 计划偏差：本调整覆盖原计划中的“完成唤醒”设计及 V03/V27/V31 的唤醒部分，属于用户在本地体验后的明确产品决策；新的验收口径是终态持久化与面板/主动查询可见，不保留历史自动回复兼容层。
- 当前状态：工作区仍未提交、未推送，未创建分支或工作树；未调用真实模型、科研平台或实验设备。
