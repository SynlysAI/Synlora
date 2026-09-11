/**
 * 会话列表 store：CRUD 与当前选中态。
 *
 * 列表按 updated_at 倒序（后端排序）；create 成功后置为当前会话。
 * `create` 缺省不指定 project_id，由后端回落到默认工作区（侧栏「新会话」
 * 与「会话」组 `+` 都走这条路径）。
 * remove 删除当前会话时清空 currentId（由面板层决定后续引导）。
 */
import { create } from 'zustand'
import type { Session } from '@/types'
import { api } from '@/api/client'

/** 新建会话的可选参数。 */
export interface CreateSessionOptions {
  /** 会话标题（缺省空串，首轮对话后由后端自动命名）。 */
  title?: string
  /** 绑定的工作区 id；缺省不传该字段，由后端回落到默认工作区。 */
  projectId?: string | null
}

interface SessionsState {
  /** 当前用户全部会话（updated_at 倒序）。 */
  sessions: Session[]
  /** 当前选中会话 id（null 未选中）。 */
  currentId: string | null
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
  loaded: false,

  load: async () => {
    const sessions = await api<Session[]>('/api/v1/sessions')
    set({ sessions, loaded: true })
  },

  create: async (assistantId, options = {}) => {
    const { title = '', projectId = null } = options
    // 缺省不带 project_id：由后端回落到默认工作区
    const session = await api<Session>('/api/v1/sessions', {
      method: 'POST',
      body: projectId
        ? { assistant_id: assistantId, title, project_id: projectId }
        : { assistant_id: assistantId, title },
    })
    // 新会话 updated_at 最新：插到列表头并选中
    set((s) => ({ sessions: [session, ...s.sessions], currentId: session._id }))
    return session
  },

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

  remove: async (id) => {
    await api(`/api/v1/sessions/${id}`, { method: 'DELETE' })
    set((s) => ({
      sessions: s.sessions.filter((x) => x._id !== id),
      currentId: s.currentId === id ? null : s.currentId,
    }))
  },

  setCurrent: (id) => set({ currentId: id }),

  resetAll: () => set({ sessions: [], currentId: null, loaded: false }),
}))
