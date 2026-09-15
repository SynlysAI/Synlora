/**
 * 后端 API 契约的 TypeScript 类型定义。
 *
 * 与 apps/web/backend 各 API 模块的响应结构一一对应：
 * - auth_api.py   -> LoginResponse / MeResponse
 * - models_api.py -> ModelProvider / ProviderTestResult
 * - assistants_api.py -> Assistant
 * - sessions_api.py   -> Session / SessionEvent / EventType
 * - files_api.py  -> FileDoc / UploadResponse
 * - projects_api.py -> Project / TreeEntry
 *
 * 时间戳约定：created_at / updated_at 为 Unix 浮点秒（存储层 TEXT 列）。
 */

/** 用户角色。 */
export type Role = 'admin' | 'user'

/** POST /api/v1/auth/login 成功响应。 */
export interface LoginResponse {
  token: string
  username: string
  role: Role
}

/** GET /api/v1/auth/me 响应（token payload 回显，iat/exp 由签发方补齐）。 */
export interface MeResponse {
  sub: string
  username: string
  role: Role
  iat?: number
  exp?: number
}

/** 认证用户信息（auth store 持久化的会话态）。 */
export interface AuthUser {
  sub: string
  username: string
  role: Role
}

/** 模型服务公共视图（api_key 永不出明文，仅回 has_key）。 */
export interface ModelProvider {
  _id: string
  name: string
  base_url: string
  model_id: string
  enabled: boolean
  has_key: boolean
}

/** 模型服务连通性测试结果。 */
export interface ProviderTestResult {
  ok: boolean
  latency_ms: number | null
  error: string | null
}

/** 助手文档（列表联查 model_name：正常名称 / "(已停用)" / "(已删除)" / null=未关联）。 */
export interface Assistant {
  _id: string
  name: string
  avatar?: string | null
  description: string
  system_prompt: string
  model_provider_id: string | null
  tool_whitelist: string[]
  knowledge_base_ids?: string[]
  builtin: boolean
  model_name: string | null
}

/** 全局技能（磁盘 SKILL.md 扫描结果；content 为正文，不含 frontmatter）。 */
export interface Skill {
  /** 技能名（即目录名，kebab-case）。 */
  name: string
  description: string
  version: string
  author: string
  tags: string[]
  allowed_tools: string[]
  /** SKILL.md 正文（不含 frontmatter）。 */
  content: string
  /** 内置技能不可删除（data-analysis / pdf-extraction 等）。 */
  builtin: boolean
  /** 来源：catalog（内置目录）/ public（公共层）/ plugin（插件贡献）/ user（用户自建）。 */
  source?: 'catalog' | 'public' | 'plugin' | 'user'
}

/** 插件配置字段 schema（对应后端 app/catalog/loader.py 的 config_schema）。 */
export interface PluginConfigField {
  key: string
  label: string
  type: 'text' | 'password'
  required?: boolean
  secret?: boolean
  placeholder?: string
  description?: string
}

/** 插件状态（对应后端 app/plugins/api.py 的 GET /api/v1/plugins）。 */
export interface PluginInfo {
  id: string
  name: string
  version: string
  description: string
  config_schema: PluginConfigField[]
  installed: boolean
  configured: boolean
  missing: string[]
  /** 非敏感字段的当前值（敏感字段不回传） */
  config: Record<string, string>
  /** 敏感字段是否已配置（true 时输入框留空表示保持不变） */
  secrets_set: Record<string, boolean>
  /** 插件贡献的技能清单（统一在插件页查看，不进技能管理页）。 */
  skills: Array<{ name: string; description: string }>
  /** 插件播种的专家清单（统一在插件页查看，不进助手管理页）。 */
  experts: Array<{ id: string; name: string }>
  /** 插件注册的工具名列表。 */
  tools: string[]
}

/** 能力目录条目（对应后端 app/catalog/service.py 的 market_items）。 */
export interface CatalogItem {
  kind: 'expert' | 'skill' | 'plugin'
  id: string
  name: string
  description: string
  source: 'builtin'
  visibility: 'public' | 'hidden'
  default_enabled: boolean
  installed: boolean
  /** 已安装时是否处于启用态（停用 = 已装但不注入运行期）。 */
  enabled: boolean
  visible: boolean
  /** 仅插件行返回：安装时可填的配置字段 */
  config_schema?: PluginConfigField[]
  /** 仅插件行返回：管理员公共配置已就绪的字段名（这些留空即用系统配置） */
  config_ready_keys?: string[]
}

/** 「我的」列表条目：自建（mine）/ 已安装的内置（installed）/ 平台内置只读（builtin）。 */
export interface MyCapability {
  kind: 'skill' | 'expert' | 'plugin'
  id: string
  name: string
  description: string
  source: 'mine' | 'installed' | 'builtin'
  enabled: boolean
  builtin: boolean
  /** 管理员已下架（仅 installed 条目可能出现）：不可启用。 */
  revoked?: boolean
}

/** 会话元数据文档。 */
export interface Session {
  _id: string
  user_id: string
  /** 绑定的专家 id（null = 不使用专家，只走平台默认提示词）。 */
  assistant_id: string | null
  title: string
  archived: boolean
  message_count: number
  /** 会话级模型覆盖（null = 跟随助手绑定；前端用户在输入区可切换）。 */
  model_provider_id: string | null
  /** 会话绑定的项目 id（null/缺省 = 无工作区会话，会话目录 sessions/{id} 即工作区）。 */
  project_id?: string | null
  /** 会话级插件开关：null/缺省 = 跟随用户级可见集；列表 = 只用这些（[] = 本会话禁用全部插件）。 */
  enabled_plugins?: string[] | null
  created_at: number
  updated_at: number
}

/** 用户工作区文件记录。 */
export interface FileDoc {
  _id: string
  user_id: string
  filename: string
  /** 相对所属根（项目或会话目录）的存储路径（files/ 沙箱内）。 */
  stored_path: string
  /** 归属项目 id（与会话归属互斥：两者取一）。 */
  project_id?: string | null
  /** 归属会话 id（无工作区会话的文件，随会话删除一并清理）。 */
  session_id?: string | null
  size: number
  mime: string
  created_at: number
  updated_at: number
}

/** SSE 事件类型（synlys_harness.EventType 枚举值）。 */
export type EventType =
  | 'turn/start'
  | 'user/message'
  | 'llm/delta'
  | 'reasoning/delta'
  | 'assistant/reasoning'
  | 'assistant/message'
  | 'tool/call'
  | 'tool/result'
  | 'ask/user'
  | 'file/send'
  | 'session/compaction'
  | 'turn/end'
  | 'turn/aborted'
  | 'error'

/** 会话事件（事件回放与 SSE 流的统一结构）。 */
export interface SessionEvent {
  seq: number
  type: EventType
  payload: Record<string, unknown>
  ts: number
}

/** tool/call 事件负载（harness agent.py：同组首个调用携带前置文本，其余为 null）。 */
export interface ToolCallPayload {
  tool_call: { id: string; name: string; arguments: Record<string, unknown> }
  content: string | null
}

/** tool/result 事件负载（harness agent.py：content 为给 LLM 看的文本表示）。 */
export interface ToolResultPayload {
  tool_call_id: string
  name: string
  ok: boolean
  content: string
  error: string | null
  truncated: boolean
}

/** 项目文档（GET /api/v1/projects 列表项）。 */
export interface Project {
  _id: string
  user_id: string
  name: string
  /** 磁盘目录名（重名时后端自动加后缀，与 name 可能不同）。 */
  dir_name: string
  archived: boolean
  created_at: number
  updated_at: number
}

/** 项目目录树的一级条目（GET /api/v1/projects/{pid}/tree）。 */
export interface TreeEntry {
  name: string
  /** 相对项目根的路径（POSIX 分隔符；目录可直接作为下一层的 path 参数）。 */
  path: string
  is_dir: boolean
  size: number
  mtime: number
}

/** 文件上传响应（207 简化：逐项结果 + 顶层 status）。 */
export interface UploadResponse {
  status: 'ok' | 'partial' | 'failed'
  results: Array<{
    ok: boolean
    code?: number
    error?: string
    file?: { _id: string; filename: string; size: number }
  }>
}

/** 随消息发送的附件（user/message 事件 payload.attachments 项）。 */
export interface MessageAttachment {
  /** files 集合记录 id（下载端点用）。 */
  file_id: string
  filename: string
  /** 工作区相对路径（agent 按此读取）。 */
  path: string
  size: number
}
