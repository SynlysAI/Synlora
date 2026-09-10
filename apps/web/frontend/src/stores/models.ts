/**
 * 模型服务 store（用户侧轻量视图）：仅 enabled 列表，供对话输入区的
 * 会话级模型选择器使用（管理页完整 CRUD 走 admin store 的 all=true 视图）。
 */
import { create } from 'zustand'
import type { ModelProvider } from '@/types'
import { api } from '@/api/client'

interface ModelsState {
  /** enabled 的模型服务列表（GET /api/v1/models 对普通用户默认只回 enabled）。 */
  models: ModelProvider[]
  /** load() 是否完成（加载失败保持 false，选择器显示空态文案）。 */
  loaded: boolean
  /** 拉取 enabled 模型列表（幂等，失败静默由调用方空态兜底）。 */
  load: () => Promise<void>
}

export const useModelsStore = create<ModelsState>((set) => ({
  models: [],
  loaded: false,

  load: async () => {
    const models = await api<ModelProvider[]>('/api/v1/models')
    set({ models, loaded: true })
  },
}))
