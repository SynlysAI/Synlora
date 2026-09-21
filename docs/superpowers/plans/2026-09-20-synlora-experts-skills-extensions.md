# 专家、技能与扩展中心改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有能力中心升级为参考 Jiuwen 的专家编排、目录型技能资产和统一扩展中心，并首期接入安全的 Streamable HTTP MCP。

**Architecture:** 专家只保存人设与能力引用，不保存连接凭证；技能继续以目录为事实源，增加 ZIP 导入和文件预览；原生插件与 MCP 在 UI 中统一归入“扩展”，后端保持两套独立模型。MCP 配置按用户加密落库，运行时按当前用户与所选专家动态发现并注册远程工具。

**Tech Stack:** FastAPI、Pydantic、httpx、React 19、TypeScript、Zustand、Tailwind CSS 4。

## Global Constraints

- 所有对话、代码注释和文档使用中文。
- 前端结构与交互参考 JiuwenSwarm，不自行发明新的视觉语言。
- 普通用户首期只允许接入 Streamable HTTP MCP，不允许配置 STDIO 命令。
- 专家只引用技能和 MCP 的稳定 ID，不复制技能目录、不保存 MCP 密钥。
- 插件仍是平台部署的可信 Python 包；MCP 是用户配置的远程工具连接，两者不得合并存储。
- 技能 ZIP 必须包含合法 `SKILL.md`，并防止路径逃逸、符号链接和解压炸弹。
- 会话级插件开关语义保持不变；专家引用不能绕过用户能力可见性。
- 本次为向下兼容的新功能，应用版本从 `0.13.0-beta.1` 升至 `0.14.0-beta.1`。

---

### Task 1: 技能目录资产 API

**Files:**
- Modify: `apps/web/backend/app/services/skill_service.py`
- Modify: `apps/web/backend/app/api/me_api.py`
- Modify: `apps/web/backend/app/catalog/api.py`
- Test: `apps/web/backend/tests/test_skill_service.py`
- Test: `apps/web/backend/tests/test_me_api.py`

**Interfaces:**
- Produces: `SkillService.import_user_skill_zip(user_id, archive)`。
- Produces: `SkillService.list_skill_files(name, user_id)`。
- Produces: `SkillService.read_skill_file(name, relative_path, user_id)`。

- [ ] 添加 ZIP 安全解压与技能包根目录识别测试。
- [ ] 实现用户技能 ZIP 导入与同名冲突校验。
- [ ] 添加技能文件树和文本/图片预览端点。
- [ ] 在能力详情中返回技能文件清单。

### Task 2: MCP 配置与协议客户端

**Files:**
- Create: `apps/web/backend/app/services/mcp_service.py`
- Create: `apps/web/backend/app/api/mcp_api.py`
- Modify: `apps/web/backend/app/main.py`
- Modify: `apps/web/backend/app/db/store.py`
- Test: `apps/web/backend/tests/test_mcp_service.py`
- Test: `apps/web/backend/tests/test_mcp_api.py`

**Interfaces:**
- Produces: `McpService.list_for_user(user_id)`。
- Produces: `McpService.save_for_user(user_id, payload)`。
- Produces: `McpService.discover_tools(user_id, mcp_id)`。
- Produces: `McpService.call_tool(user_id, mcp_id, tool_name, arguments)`。

- [ ] 定义用户 MCP 文档和安全字段回传格式。
- [ ] 实现配置加密、CRUD 与连接测试。
- [ ] 实现 MCP initialize、tools/list 和 tools/call。
- [ ] 注册 `/api/v1/me/mcps` 系列端点。

### Task 3: MCP 动态工具装配

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`
- Modify: `apps/web/backend/app/runtime/assembly.py`
- Modify: `apps/web/backend/app/api/deps.py`
- Test: `apps/web/backend/tests/test_agent_service.py`
- Test: `apps/web/backend/tests/test_runtime_assembly.py`

**Interfaces:**
- Consumes: `McpService.discover_tools()` 与 `McpService.call_tool()`。
- Produces: 单轮独立 `ToolRegistry` / `ToolPipeline`，包含当前专家允许的 MCP 工具。

- [ ] 为专家引用解析当前用户可用 MCP。
- [ ] 将远程工具映射为 `mcp.<id>.<tool>`。
- [ ] 保持插件工具和内置工具现有权限过滤。
- [ ] MCP 不可用时跳过并在系统提示中说明。

### Task 4: 专家能力编排模型

**Files:**
- Modify: `apps/web/backend/app/services/expert_service.py`
- Modify: `apps/web/backend/app/api/me_api.py`
- Modify: `apps/web/backend/app/api/assistants_api.py`
- Modify: `apps/web/backend/app/catalog/loader.py`
- Modify: `apps/web/backend/app/catalog/api.py`
- Modify: `apps/web/backend/app/services/agent_service.py`
- Test: `apps/web/backend/tests/test_expert_service.py`
- Test: `apps/web/backend/tests/test_catalog_api.py`

**Interfaces:**
- Produces: 专家字段 `skill_refs`、`mcp_refs`、`suggested_prompts`。
- Consumes: 当前用户技能可见集与 MCP 可用集。

- [ ] 扩展专家 manifest、API DTO 与目录详情。
- [ ] 校验专家引用的技能、MCP 和工具。
- [ ] 会话运行时将专家技能引用与本轮手选技能合并。
- [ ] 保持旧专家 manifest 无需迁移即可读取。

### Task 5: 技能文件预览前端

**Files:**
- Create: `apps/web/frontend/src/components/catalog/SkillFilePreview.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityDetail.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityModals.tsx`
- Modify: `apps/web/frontend/src/stores/myCapabilities.ts`
- Modify: `apps/web/frontend/src/types.ts`

**Interfaces:**
- Consumes: 技能 ZIP 导入、文件树和文件内容 API。

- [ ] 将新建技能入口改为上传 ZIP 与手动创建并存。
- [ ] 详情页增加“技能说明 / 文件”页签。
- [ ] 实现左侧目录树和右侧文本/图片预览。
- [ ] 保持旧的 `SKILL.md` 在线编辑入口。

### Task 6: 统一扩展中心前端

**Files:**
- Create: `apps/web/frontend/src/components/catalog/ExtensionCenter.tsx`
- Create: `apps/web/frontend/src/components/catalog/McpEditor.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityDetail.tsx`
- Modify: `apps/web/frontend/src/stores/myCapabilities.ts`
- Modify: `apps/web/frontend/src/types.ts`

**Interfaces:**
- Consumes: 现有插件目录 API 与 `/api/v1/me/mcps`。

- [ ] 将左侧“插件”改名为“扩展”。
- [ ] 增加插件广场、MCP 接入、我的扩展页签。
- [ ] 实现 HTTP MCP 创建、编辑、测试、启停和删除。
- [ ] MCP 详情展示连接状态与发现的工具。

### Task 7: 专家整页编辑器

**Files:**
- Create: `apps/web/frontend/src/components/catalog/ExpertEditor.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityDetail.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityModals.tsx`
- Modify: `apps/web/frontend/src/stores/myCapabilities.ts`
- Modify: `apps/web/frontend/src/types.ts`

**Interfaces:**
- Consumes: 技能列表、MCP 列表和工具目录。
- Produces: `skill_refs`、`mcp_refs`、`tool_whitelist`、`suggested_prompts`。

- [ ] 参考 Jiuwen 实现基本信息与 Markdown 人设区。
- [ ] 实现技能、MCP 和可用工具多选。
- [ ] 实现推荐问题增删编辑。
- [ ] 详情页展示完整能力编排。

### Task 8: 文档、版本与验证

**Files:**
- Modify: `apps/web/backend/app/version.py`
- Modify: `apps/web/frontend/package.json`
- Modify: `apps/web/frontend/package-lock.json`
- Modify: `README.md`
- Modify: `apps/web/backend/README.md`

**Interfaces:**
- Consumes: 前述全部功能。

- [ ] 更新版本号为 `0.14.0-beta.1`。
- [ ] 更新能力中心、专家、技能与扩展说明。
- [ ] 运行后端相关测试。
- [ ] 运行前端 `npm run build`。
- [ ] 检查工作区差异，不修改用户已有变更。
