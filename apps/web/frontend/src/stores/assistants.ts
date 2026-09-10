/**
 * 助手列表 store：顶栏助手名联查、空态欢迎页与新建会话引导共用。
 */
import { create } from 'zustand'
import type { Assistant } from '@/types'
import { api } from '@/api/client'

interface AssistantsState {
  /** 全部助手（含 builtin）。 */
  assistants: Assistant[]
  /** load() 是否完成。 */
  loaded: boolean
  /** 拉取助手列表。 */
  load: () => Promise<void>
}

export const useAssistantsStore = create<AssistantsState>((set) => ({
  assistants: [],
  loaded: false,

  load: async () => {
    const assistants = await api<Assistant[]>('/api/v1/assistants')
    set({ assistants, loaded: true })
  },
}))
