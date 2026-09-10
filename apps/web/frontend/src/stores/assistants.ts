/**
 * 助手列表 store：顶栏助手名联查、空态欢迎页与新建会话引导共用。
 *
 * selectedId 是"新对话默认助手"（左栏头部卡的选择态）：仅影响后续新建
 * 会话用哪个助手，已有会话不受切换影响；未显式选择时回退第一个助手。
 */
import { create } from 'zustand'
import type { Assistant } from '@/types'
import { api } from '@/api/client'

interface AssistantsState {
  /** 全部助手（含 builtin）。 */
  assistants: Assistant[]
  /** load() 是否完成。 */
  loaded: boolean
  /** 新对话默认助手 id（null = 未选择，取列表第一个）。 */
  selectedId: string | null
  /** 拉取助手列表（首次加载后若无选择则默认选第一个）。 */
  load: () => Promise<void>
  /** 切换新对话默认助手。 */
  select: (id: string) => void
}

export const useAssistantsStore = create<AssistantsState>((set) => ({
  assistants: [],
  loaded: false,
  selectedId: null,

  load: async () => {
    const assistants = await api<Assistant[]>('/api/v1/assistants')
    set((s) => ({
      assistants,
      loaded: true,
      // 已显式选择的助手若仍存在则保留，否则回退第一个
      selectedId:
        s.selectedId && assistants.some((a) => a._id === s.selectedId)
          ? s.selectedId
          : (assistants[0]?._id ?? null),
    }))
  },

  select: (id) => set({ selectedId: id }),
}))

/**
 * 从 assistants state 计算"新对话默认助手"（显式选择优先，回退第一个）。
 *
 * @param state assistants store 快照。
 * @returns 默认助手（列表为空时 null）。
 */
export function pickSelectedAssistant(state: AssistantsState): Assistant | null {
  return (
    state.assistants.find((a) => a._id === state.selectedId) ??
    state.assistants[0] ??
    null
  )
}
