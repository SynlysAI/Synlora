/**
 * 管理页 store：模型服务/助手/技能三页的列表数据源。
 *
 * 沿用 files/sessions 等 store 惯例——列表态与加载集中在 zustand，
 * 组件 effect 只调用 load*（不在组件内 setState，规避级联渲染）；
 * 助手变更后同时刷新工作台的 assistants store，返回工作台即时可见。
 */
import { create } from 'zustand'
import type { Assistant, CatalogItem, ModelProvider, PluginInfo, Skill } from '@/types'
import { api } from '@/api/client'
import { useAssistantsStore } from './assistants'

interface AdminState {
  /** 全部模型服务（含停用项，管理页专用 all=true 视图）。 */
  providers: ModelProvider[]
  /** providers 是否完成首次成功加载。 */
  providersLoaded: boolean
  /** 管理页助手列表（含 builtin 标记与联查 model_name）。 */
  assistants: Assistant[]
  /** 当前 enabled 的模型服务（助手表单 select 选项来源）。 */
  enabledModels: ModelProvider[]
  /** assistants/enabledModels 是否完成首次成功加载。 */
  assistantsLoaded: boolean
  /** 全局技能列表（含 builtin 标记）。 */
  skills: Skill[]
  /** skills 是否完成首次成功加载。 */
  skillsLoaded: boolean
  /** 插件列表（含配置 schema 与安装状态）。 */
  plugins: PluginInfo[]
  /** plugins 是否完成首次成功加载。 */
  pluginsLoaded: boolean
  /** 能力目录（管理员视角，含 hidden 条目与策略）。 */
  catalog: CatalogItem[]
  /** 拉取全部模型服务（含停用项）。 */
  loadProviders: () => Promise<void>
  /** 拉取助手列表与 enabled 模型，并同步工作台助手 store。 */
  loadAssistants: () => Promise<void>
  /** 拉取全局技能列表。 */
  loadSkills: () => Promise<void>
  /** 拉取插件列表（含配置 schema 与安装状态）。 */
  loadPlugins: () => Promise<void>
  /** 拉取能力目录（管理员视角，含 hidden 条目与策略）。 */
  loadCatalog: () => Promise<void>
}

export const useAdminStore = create<AdminState>((set) => ({
  providers: [],
  providersLoaded: false,
  assistants: [],
  enabledModels: [],
  assistantsLoaded: false,
  skills: [],
  skillsLoaded: false,
  plugins: [],
  pluginsLoaded: false,
  catalog: [] as CatalogItem[],

  loadProviders: async () => {
    const providers = await api<ModelProvider[]>('/api/v1/models?all=true')
    set({ providers, providersLoaded: true })
  },

  loadAssistants: async () => {
    const [assistants, enabledModels] = await Promise.all([
      api<Assistant[]>('/api/v1/assistants'),
      api<ModelProvider[]>('/api/v1/models'),
    ])
    set({ assistants, enabledModels, assistantsLoaded: true })
    // 工作台顶栏标题/左栏选择列表的联查数据同步刷新
    await useAssistantsStore.getState().load()
  },

  loadSkills: async () => {
    const skills = await api<Skill[]>('/api/v1/skills')
    set({ skills, skillsLoaded: true })
  },

  loadPlugins: async () => {
    const plugins = await api<PluginInfo[]>('/api/v1/plugins')
    set({ plugins, pluginsLoaded: true })
  },

  loadCatalog: async () => {
    const catalog = await api<CatalogItem[]>('/api/v1/admin/catalog')
    set({ catalog })
  },
}))
