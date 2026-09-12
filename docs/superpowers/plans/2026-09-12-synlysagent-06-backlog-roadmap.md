# SynlysAgent 待办计划（路线图执行版）

> 对应总设计 §11 演进路线。基线：2026-09-12，V1 + 架构补强已完成
> （事件内核/工具管线/三栏工作台/WeKnora 实接/steering/hooks/上下文压缩/LLM 重试）。
> 用 checkbox 跟踪进度；每项含验收标准，做之前可展开为独立实施计划。

---

## 阶段一：V1.x 打磨（近期，各项 0.5~2 天）

### 1. 沙箱升级（多用户部署的前置条件）
- [ ] 选型：Docker 容器执行（首选）vs Windows 受限令牌（备选），写结论
- [ ] `run_python` 抽象执行器接口：local（现状）/ docker 两实现，env 开关切换
- [ ] Docker 实现：临时容器、工作区 tmp/ 卷挂载、CPU/内存/超时限制、无网络（或代理白名单）
- [ ] 回退兼容：Docker 不可用时回落 local 并在日志/事件中标记 `sandbox=weak`
- [ ] 测试：逃逸用例（写工作区外路径、读环境变量、外联）在强沙箱下全被拦

### 2. Office 文档生成（模型出报告的刚需）
- [ ] synlysagent 环境安装：python-docx、python-pptx、openpyxl、pandas
- [ ] 内置技能 `office-doc`（SKILL.md）：可用库清单、保存路径约定（工作区 `output/`）、matplotlib 图嵌入 Word/PPT 的套路、中文字体注意
- [ ] 验收：对话「把上面的分析整理成 Word 报告（含图）」→ 产物落工作区可下载

### 3. 跨会话长期记忆（工作区级）
- [ ] 记忆存储：`memories` 集合，维度 `user_id + project_id`（事实/偏好/实验结论，带来源会话 id）
- [ ] 抽取：turn/end 钩子（on_session_end）→ LLM 抽取候选记忆 → 去重合并入库
- [ ] 注入：会话装配时按 user+project 检索 top-N，进 system prompt（带"参考记忆"标注）
- [ ] 开关：全局配置 `memory_enabled`，**默认关**（照 jiuwen 惯例），管理页可切
- [ ] 遗忘：会话删除不级联删记忆（记忆独立于会话）；提供管理页查看/删除入口
- [ ] 验收：工作区 A 会话里告知的偏好，新会话（同工作区）能遵守；开关关闭时零记忆

### 4. 小修（顺手项）
- [ ] 管线截断时在 content 末尾追加一行「（输出超限已截断）」，模型可知可补救
- [ ] 设计文档 §11 同步实际进度（WeKnora 划掉、补 steering/压缩/重试条目）

---

## 阶段二：V2 科研能力（中期，按价值排序）

### 5. AI⁴MS 真实工具接入（最高优先：项目立身之本）
- [ ] Spec_Agent 异步任务工具化：`/api/v1/tasks/{nmr,gpc,...}` 提交 → 工具内轮询 → 取结果
- [ ] 轮询模式设计：提交即返回 task_id + `task.poll` 工具（避免长阻塞占 step），或工具内带上限轮询
- [ ] 凭证与白名单：AI⁴MS 网关地址/凭证走 settings，工具按 ctx.extra 注入
- [ ] SpecLabOS 设备/工作流接入（排 Spec_Agent 后）
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
| 要开放多用户 / 公网部署 | 1 沙箱升级（必须先做） |
| 用户反馈"换会话就失忆" | 3 长期记忆 |
| Spec_Agent 接口可用 | 5 立即接入 |
| 出现"多专家并行协作"真实需求 | 7 多 Agent |
| 需要接入第三方工具生态 | 6 MCP |

---

## 已完成基线（2026-09-12）

- V1：事件会话内核（derive 投影/turn-step 循环/取消）、四段工具管线、10 个内置工具、三栏工作台、工作区/专家/技能、AI⁴MS 兼容认证、双后端存储、SSE 断连续传
- 架构补强：steering 插话全链路、ExtensionHooks 宿主挂载、上下文自动压缩（pi 式阈值摘要 + 持久化标记）、LLM 瞬时错误退避重试、WeKnora 实接（hybrid-search + 助手绑定硬边界 + knowledge.list）、运行中插话 UI、工具行执行中动画、自定义弹窗
