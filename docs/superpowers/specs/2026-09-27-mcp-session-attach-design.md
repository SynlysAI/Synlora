# MCP 会话级附加与公共 MCP 管理 — 设计文档

- 日期：2026-09-27
- 状态：待评审
- 目标版本：1.5.0
- 关联 backlog：`docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md` 第 6 项（MCP adapter，本设计为其修正与落地）

## 1. 背景与目标

用户级 MCP 已存在（`McpService` + 能力中心扩展中心），但唯一消费路径是**专家 `mcp_refs`**——只有专家绑定 MCP 才能在运行中使用。本设计补齐三块：

1. **会话级附加**：聊天输入框 `+` 号面板直接勾选 MCP（不依赖专家绑定）
2. **公共 MCP**：管理员在 catalog 配置默认公共 MCP 服务（支持 stdio 与 HTTP），走能力目录的可见性/内置/安装模型
3. **stdio transport**：仅公共 MCP 开放（用户自建仍限 Streamable HTTP）

### 已有资产（全部复用，不重建）

| 资产 | 位置 | 说明 |
|---|---|---|
| 用户 MCP CRUD + 加密凭证 | `app/services/mcp_service.py` | Fernet 加密 headers/bearer 落 `mcp_connections` 集合；测试连接 + 状态持久化 |
| 自研 HTTP JSON-RPC 客户端 | `mcp_service.py::_request` | 纯 httpx（无 mcp SDK、无 anyio 任务组问题）：2026-07-28 无状态协议优先，旧 initialize 握手回退，SSE 响应解析 |
| per-run 工具装配 | `app/services/agent_service.py:426-467` | `mcp.<id>.<tool>` 命名、逐工具动态注册进 run_registry、白名单扩展 |
| 用户自建 MCP UI | `components/catalog/ExtensionCenter.tsx` + `McpEditor.tsx` | 市场/MCP/我的 三 tab，增删改查 + 测试连接 |
| 会话级开关模式 | `enabled_plugins` 全链路 | 会话文档 + 草稿暂存 + PATCH 切换即刷新，MCP 照抄 |
| 能力目录可见性模型 | `CapabilityService` + `catalog_policy` | 按 `(kind, item_id)` 判定，kind 天然可扩展 |

## 2. 范围

**做**：
- 会话文档 `enabled_mcp` 字段与 `+` 面板 `McpPicker`
- catalog 第 4 类条目 `mcp`（`catalog/mcp/<id>/mcp.json`），loader 扫描 + policy 可见/内置 + 市场安装模型
- 管理后台 MCP 管理页（新增/编辑/删除/恢复默认/policy/连接测试）
- `McpService` 抽 transport 抽象，新增 stdio 子进程实现（仅公共 MCP）
- chat 装配：MCP 来源 = 会话 `enabled_mcp` ∪ 专家 `mcp_refs`

**不做（一期）**：
- 用户自建 stdio / SSE transport（维持 Streamable HTTP）
- OAuth 授权流（`auth_required` + 浏览器授权）
- 自动重连 / 健康巡检（失败标记 + 懒重建即可）
- 公共 MCP 凭证的 DB 加密覆盖层（一期凭证直接写 mcp.json，管理员信任级，同插件 manifest 定位；后续有需要再加）
- MCP resources / prompts 能力（只接 tools）
- 表单粘贴 `mcpServers` JSON 自动解析（后续增强）

## 3. 数据模型

### 3.1 会话文档

`sessions` 集合文档新增可选字段：

```json
{ "enabled_mcp": ["filesystem", "my-remote"] }
```

语义与 `enabled_plugins` 完全同构：**缺省 / null / [] = 本会话不附加任何 MCP**；列表 = 附加这些（含公共与用户自建，同一 id 空间）；PATCH 切换即落库刷新，不丢运行态。

### 3.2 公共 MCP manifest（`catalog/mcp/<id>/mcp.json`）

```json
{
  "id": "filesystem",
  "name": "文件系统",
  "description": "受限目录的文件读写工具",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "cwd": null,
  "env": {},
  "timeout_s": 60
}
```

HTTP 型公共 MCP：

```json
{
  "id": "remote-api",
  "name": "远程 API",
  "description": "...",
  "transport": "streamable-http",
  "url": "https://example.com/mcp",
  "headers": { "X-Api-Key": "..." },
  "bearer_token": null,
  "timeout_s": 30
}
```

约束：
- `id` kebab-case（与用户自建同一校验 `MCP_ID_OK`），目录名 = id
- `transport` 仅 `stdio` / `streamable-http`
- 必填：`id`、`name`、`description`、`transport`，及对应 transport 的连接字段（stdio: `command`；http: `url`）
- 凭证（headers/bearer_token/env）一期允许明文写 manifest——管理员维护、部署级信任，与插件 manifest 同级；文档注明风险

### 3.3 catalog 条目类型

`app/catalog/items.py::KINDS` 扩展为 `("expert", "skill", "plugin", "mcp")`；`loader.py::scan_catalog` 增加 `_scan_mcps`（照 `_scan_plugins` 模式：非法 manifest 告警跳过、数据目录根覆盖仓库根）。`CatalogIndex` 增加 `mcps` 字典。公共 MCP 不声明 `tools_module`/`skills`/`expert` 等插件贡献字段。

## 4. 运行时设计

### 4.1 MCP 来源解析（McpService 扩展）

新增统一解析入口（`enabled_mcp` 与专家 `mcp_refs` 共用）：

```
resolve_runtime_mcps(user_id, mcp_ids) -> list[RuntimeMcp]
```

对每个 id（去重后）：
1. 先查用户自建（`mcp_connections`，现有逻辑：存在且 `enabled` 才用）
2. 不存在再查公共 catalog：`CapabilityService.is_visible(user_id, "mcp", id)` 通过才用；解析 manifest 配置（含 stdio/http 分流）
3. **同名冲突：用户自建优先**，公共条目跳过并告警（两边 id 空间一致，用户用自己的同名配置盖公共的是合理语义）
4. 拿工具列表：自建走现有 `tools` 快照/懒发现；公共走 `discover_public_tools`（见 4.3）
5. 返回统一结构 `{id, name, source: "user" | "catalog", config, tools}`

`call_tool` 相应扩展为按 `(source, mcp_id)` 分发：自建走现有 httpx 路径；公共按 transport 走 httpx 或 stdio 进程。

### 4.2 chat 装配（agent_service 最小改动）

`agent_service.py:426-467` 的注册逻辑不动，只换数据来源：

```
mcp_ids = 去重(session.enabled_mcp + assistant.mcp_refs)
connections = mcp_service.resolve_runtime_mcps(user_sub, mcp_ids)
```

工具命名、动态注册、白名单扩展（`whitelist + mcp_tool_names`）维持现状。专家 `mcp_refs` 继续生效（向后兼容），会话勾选是新增来源，两者取并集。

### 4.3 transport 抽象与 stdio 实现

`McpService` 内部抽 transport：

- **HTTP（现状零改动）**：`_modern_request` / `_legacy_request` 保持无状态短连接（每次调用新建 httpx 请求），天然无连接生命周期问题
- **stdio（新增，仅公共 MCP）**：手写子进程 JSON-RPC（不引入 mcp SDK，与 httpx 客户端同风格）：
  - `asyncio.create_subprocess_exec` spawn，stdin/stdout 按行读写 JSON-RPC
  - 启动即 `initialize` 握手 + `notifications/initialized`（协议版本与 legacy 路径一致）
  - 进程按 mcp id 进程级缓存（`{mcp_id: proc}`）；单飞锁防并发重复 spawn
  - 每次调用带 `timeout_s` 读超时；超时/进程退出 = kill + 清缓存 + 标记 error，下次调用懒重建（无自动重连风暴）
  - 应用退出统一清理（lifespan 挂 `aclose()`）
  - 协议版本协商失败回退旧握手版本重试一次

### 4.4 公共 MCP 状态缓存

进程级状态 `{mcp_id: {status, last_error, tools, checked_at}}`：
- 管理页"测试连接"按钮触发真实探测（stdio = 真实握手 + `tools/list`；http = initialize body 探测）并更新缓存
- `+` 面板与管理页列表读缓存展示 可用/失败(原因)/未检测
- 探测失败不阻止保存配置（可能临时故障），只标灰提示

## 5. 可见性与安装模型

公共 MCP 完整套用能力目录既有模型（`CapabilityService` 已按 kind 泛化，无机制改动）：

- `catalog_policy`：`hidden` = 全局隐藏（勾选/专家引用一律跳过）；`default_enabled`（内置）= 全员可用、用户侧只读
- 非内置 = 能力中心市场可见，需用户安装（`user_capabilities` 记录，不复制文件），可启停/卸载
- 会话勾选不能放大可见性：PATCH 校验 `enabled_mcp` 里每个 id = 用户自建存在且属于本人，或公共条目对该用户可见；运行期再判一次（管理员事后 hidden 的，装配时静默跳过）
- 用户自建 MCP 不进市场/安装模型（维持现状，`ExtensionCenter` 自管）

## 6. API 变更

| 端点 | 变更 |
|---|---|
| `POST /sessions`、`PATCH /sessions/{sid}` | 新增 `enabled_mcp` 字段（可选），校验规则见 §5；写法与 `enabled_plugins` 同款 |
| `GET /me/capabilities`（市场） | kind 扩展含 `mcp`，公共 MCP 进市场列表 |
| `PUT /me/capabilities`（安装/启停） | 泛化到 mcp kind，无新端点 |
| 管理端点（admin 路由） | 新增 MCP 管理：列表（含 policy + 探测状态）、新增/编辑（写 `public/mcp/`）、删除（仅数据目录层条目）、恢复默认（删覆盖文件）、policy 设置、测试连接。路径与鉴权照现有 admin catalog 管理端点模式 |
| `GET /me/mcp/panel`（新增） | `+` 面板候选合并视图：用户自建(enabled) ∪ 可见公共 MCP，每项含 `source` 与探测状态（公共项状态来自后端进程级缓存，前端无法拼接，故由后端统一出） |

## 7. 前端

| 组件 | 改动 |
|---|---|
| `McpPicker`（新增） | `+` 号面板选择器，照 `PluginPicker` 结构：分组「我的 MCP / 公共 MCP」、状态徽标（可用/失败/未检测）、勾选写 sessions store 草稿态、随建会话提交、已建会话切换即 PATCH |
| `ExtensionCenter` | 市场 tab 列表增加公共 MCP（安装/启停/卸载，与插件同卡片模型）；「MCP」tab 维持用户自建 CRUD |
| 后台 `McpAdmin`（新增） | 管理页左导航加「MCP 服务」：列表（policy 状态 + 探测状态 + 已被覆盖标记）、新增/编辑表单（transport 切换 stdio/http 字段组）、删除、恢复默认、测试连接。照 `PluginsAdmin`/`AssistantsAdmin` 结构 |
| `types.ts` / stores | `McpConnection` 加 `source`；sessions store 加 `enabledMcp` 草稿态 |

UI 参考纪律：`+` 面板与后台页分别照抄本项目既有 `PluginPicker` 与 `PluginsAdmin`（它们已对齐 DSH/jiuwen 参考），不自行发挥。

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| 公共 MCP 不可见/已 hidden | 装配静默跳过 + 日志；`+` 面板条目消失 |
| 连接失败（http 探测失败 / stdio 握手失败） | 状态缓存标 error + last_error；`+` 面板标灰显示原因；工具不注入 |
| stdio 进程运行中崩溃 | kill + 清缓存，下次调用懒重建；单次调用返回错误给模型（`ok=False`） |
| 自建与公共同名 | 用户自建优先，公共跳过 + 告警 |
| 工具名与已注册工具撞名 | 现有逻辑：跳过该工具（`agent_service.py:438`） |
| 调用超时 | HTTP 沿用 httpx 30s；stdio 按行读超时 `timeout_s`；工具层 `timeout_s=60` 管线兜底 |
| 凭证解密失败（自建） | 现有行为：提示重新填写（`resolved()` 抛错，装配跳过） |

## 9. 测试策略

- **后端 pytest**：
  - loader：`_scan_mcps` 正常/缺字段/非法 transport 跳过、数据目录覆盖仓库根
  - 可见性：mcp kind 的 hidden/内置/安装判定、`enabled_mcp` PATCH 校验（自建他人 id 拒绝、公共不可见拒绝）
  - transport：stdio 用 fixture 假脚本（echo 型 JSON-RPC server）测握手/调用/超时/进程重建；http 走现有 fake client_factory 模式
  - 装配：`resolve_runtime_mcps` 的来源合并/同名优先/工具注册进 run_registry
- **前端**：`npm run build` 通过即交付，真机验证由用户自测（项目惯例）

## 10. 开放问题

无——凭证明文 manifest、无自动重连等取舍已在 §2 声明，如评审有异议再调整。
