/**
 * 助手（专家）列表 store：输入区「+ → 专家」面板与新建会话引导共用。
 *
 * 专家是**可选**的：`selectedId` 是"新对话默认专家"，`null` 表示**不使用专家**
 * （不注入 persona，只走平台默认提示词，工具放开全部内置工具）。因此
 * `load()` **不**在无显式选择时回落到第一个助手——回落会让"不使用专家"
 * 这个显式选项无处安放，也会让用户清掉专家后又被自动选回来。
 */
import { create } from 'zustand'
import type { Assistant } from '@/types'
import { api } from '@/api/client'

interface AssistantsState {
  /** 全部助手（含 builtin）。 */
  assistants: Assistant[]
  /** load() 是否完成。 */
  loaded: boolean
  /** 新对话默认专家 id（null = 不使用专家）。 */
  selectedId: string | null
  /** 拉取助手列表（已显式选择的助手若仍存在则保留，否则回到「不使用专家」）。 */
  load: () => Promise<void>
  /** 切换新对话默认专家（null = 不使用专家）。 */
  select: (id: string | null) => void
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
      // 已显式选择的助手若仍存在则保留；否则（含从未选择）保持 null
      selectedId: s.selectedId && assistants.some((a) => a._id === s.selectedId) ? s.selectedId : null,
    }))
  },

  select: (id) => set({ selectedId: id }),
}))

/**
 * 从 assistants state 计算"新对话默认专家"。
 *
 * @param state assistants store 快照。
 * @returns 显式选中的专家；未选择（或所选已不存在）时 **null**，表示不使用专家。
 */
export function pickSelectedAssistant(state: AssistantsState): Assistant | null {
  return state.assistants.find((a) => a._id === state.selectedId) ?? null
}
