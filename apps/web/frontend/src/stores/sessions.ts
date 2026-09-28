/**
 * 会话列表 store：CRUD 与当前选中态。
 *
 * 列表按 updated_at 倒序（后端排序）；create 成功后置为当前会话。
 * `create` 缺省不指定 project_id = 无工作区会话（会话目录即工作区；侧栏顶部
 * 「新会话」走这条路径，工作区行 `+` 传 projectId 走绑定路径）。
 * remove 删除当前会话时清空 currentId（由面板层决定后续引导）。
 */
import { create } from 'zustand'
import type { Session } from '@/types'
import { api } from '@/api/client'

/** 新建会话的可选参数。 */
export interface CreateSessionOptions {
  /** 会话标题（缺省空串，首轮对话后由后端自动命名）。 */
  title?: string
  /** 绑定的工作区 id；缺省不传该字段 = 无工作区会话（会话目录即工作区）。 */
  projectId?: string | null
  /** 会话级模型 provider id；缺省不传该字段（= 跟随助手绑定）。 */
  modelProviderId?: string | null
  /** 会话级插件开关；缺省不传该字段（= 跟随用户级可见集）。 */
  enabledPlugins?: string[] | null
  /** 会话级 MCP 附加；缺省不传该字段（= 不附加任何 MCP）。 */
  enabledMcp?: string[] | null
}

interface SessionsState {
  /** 当前用户全部会话（updated_at 倒序）。 */
  sessions: Session[]
  /** 当前选中会话 id（null 未选中 = 草稿态）。 */
  currentId: string | null
  /**
   * 草稿态下选定的模型 provider id（懒创建：会话还没落库，选好的模型先存这里，
   * 首次发送时随 `create` 一起写进会话）。null = 跟随专家绑定。
   */
  draftModelProviderId: string | null
  /** 设置草稿态模型（仅在草稿态有意义）。 */
  setDraftModel: (providerId: string | null) => void
  /**
   * 草稿态下选定的插件开关（null = 跟随用户级可见集，未做任何选择；
   * 列表 = 只用这些）。首次发送时随 `create` 一起写进会话。
   */
  draftEnabledPlugins: string[] | null
  /** 设置草稿态插件开关（仅在草稿态有意义）。 */
  setDraftPlugins: (plugins: string[] | null) => void
  /**
   * 草稿态下选定的 MCP 附加（null = 未做过任何选择；列表 = 附加这些）。
   * 首次发送时随 `create` 一起写进会话。
   */
  draftEnabledMcp: string[] | null
  /** 设置草稿态 MCP 附加（仅在草稿态有意义）。 */
  setDraftMcp: (ids: string[] | null) => void
  /** load() 是否完成（启动引导依据）。 */
  loaded: boolean
  /** 拉取会话列表。 */
  load: () => Promise<void>
  /**
   * 新建会话并置为当前。
   *
   * @param assistantId 绑定的专家 id（null = 不使用专家，走平台默认提示词）。
   * @param options 标题与工作区绑定（见 CreateSessionOptions）。
   */
  create: (assistantId: string | null, options?: CreateSessionOptions) => Promise<Session>
  /** 会话改名。 */
  rename: (id: string, title: string) => Promise<void>
  /** 会话归档/取消归档。 */
  archive: (id: string, archived: boolean) => Promise<void>
  /** 切换会话级模型（providerId=null 恢复跟随助手绑定）。 */
  setModel: (id: string, providerId: string | null) => Promise<void>
  /** 切换会话级插件开关（plugins=null 恢复跟随用户级可见集；[] = 本会话禁用全部插件）。 */
  setEnabledPlugins: (id: string, plugins: string[] | null) => Promise<void>
  /** 切换会话级 MCP 附加（ids=null 恢复不附加任何 MCP；[] = 同义清空）。 */
  setEnabledMcp: (id: string, ids: string[] | null) => Promise<void>
  /** 删除会话（删除当前会话时清空 currentId）。 */
  remove: (id: string) => Promise<void>
  /** 切换当前会话。 */
  setCurrent: (id: string | null) => void
  /** 清空全部状态（切换账号时调用，防止上一账号的会话被新账号引用）。 */
  resetAll: () => void
}

export const useSessionsStore = create<SessionsState>((set) => ({
  sessions: [],
  currentId: null,
  draftModelProviderId: null,
  draftEnabledPlugins: null,
  draftEnabledMcp: null,
  loaded: false,

  load: async () => {
    const sessions = await api<Session[]>('/api/v1/sessions')
    set({ sessions, loaded: true })
  },

  create: async (assistantId, options = {}) => {
    const { title = '', projectId = null, modelProviderId = null,
            enabledPlugins, enabledMcp } = options
    // 缺省的字段一律不传：不传 project_id = 无工作区会话（会话目录即工作区），
    // model_provider_id 不传 = 跟随助手绑定，enabled_plugins 不传 = 跟随用户级可见集，
    // enabled_mcp 不传 = 不附加任何 MCP
    const session = await api<Session>('/api/v1/sessions', {
      method: 'POST',
      body: {
        assistant_id: assistantId,
        title,
        ...(projectId ? { project_id: projectId } : {}),
        ...(modelProviderId ? { model_provider_id: modelProviderId } : {}),
        ...(enabledPlugins ? { enabled_plugins: enabledPlugins } : {}),
        ...(enabledMcp ? { enabled_mcp: enabledMcp } : {}),
      },
    })
    // 新会话 updated_at 最新：插到列表头并选中
    set((s) => ({ sessions: [session, ...s.sessions], currentId: session._id }))
    return session
  },

  setDraftModel: (providerId) => set({ draftModelProviderId: providerId }),

  setDraftPlugins: (plugins) => set({ draftEnabledPlugins: plugins }),

  setDraftMcp: (ids) => set({ draftEnabledMcp: ids }),

  rename: async (id, title) => {
    const updated = await api<Session>(`/api/v1/sessions/${id}`, {
      method: 'PATCH',
      body: { title },
    })
    set((s) => ({ sessions: s.sessions.map((x) => (x._id === id ? updated : x)) }))
  },

  archive: async (id, archived) => {
    const updated = await api<Session>(`/api/v1/sessions/${id}`, {
      method: 'PATCH',
      body: { archived },
    })
    set((s) => ({ sessions: s.sessions.map((x) => (x._id === id ? updated : x)) }))
  },

  setModel: async (id, providerId) => {
    // body 显式携带 null：后端以此区分"恢复助手默认"与"未提供该字段"
    const updated = await api<Session>(`/api/v1/sessions/${id}`, {
      method: 'PATCH',
      body: { model_provider_id: providerId },
    })
    set((s) => ({ sessions: s.sessions.map((x) => (x._id === id ? updated : x)) }))
  },

  setEnabledPlugins: async (id, plugins) => {
    // body 显式携带 null/列表：后端以此区分"恢复跟随可见集"与显式集合
    const updated = await api<Session>(`/api/v1/sessions/${id}`, {
      method: 'PATCH',
      body: { enabled_plugins: plugins },
    })
    set((s) => ({ sessions: s.sessions.map((x) => (x._id === id ? updated : x)) }))
  },

  setEnabledMcp: async (id, ids) => {
    // body 显式携带 null/列表：后端以此区分"恢复不附加"与显式集合
    const updated = await api<Session>(`/api/v1/sessions/${id}`, {
      method: 'PATCH',
      body: { enabled_mcp: ids },
    })
    set((s) => ({ sessions: s.sessions.map((x) => (x._id === id ? updated : x)) }))
  },

  remove: async (id) => {
    await api(`/api/v1/sessions/${id}`, { method: 'DELETE' })
    set((s) => ({
      sessions: s.sessions.filter((x) => x._id !== id),
      currentId: s.currentId === id ? null : s.currentId,
    }))
  },

  setCurrent: (id) => set({ currentId: id }),

  resetAll: () => set({
    sessions: [],
    currentId: null,
    draftModelProviderId: null,
    draftEnabledPlugins: null,
    draftEnabledMcp: null,
    loaded: false,
  }),
}))
