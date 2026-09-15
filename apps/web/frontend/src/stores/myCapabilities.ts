/**
 * 「我的能力」store：自建（技能/专家）∪ 已安装的内置（技能/专家/插件）∪ 平台内置只读视图。
 *
 * 数据来源（后端各自负责归属与可见性）：
 * - `GET /api/v1/me/skills`  自建技能 + 已装内置技能（内置条目已由后端跳过）
 * - `GET /api/v1/me/experts` 自建专家 + 已装内置专家（同上）
 * - `GET /api/v1/market/{skill,expert,plugin}` 三类市场：
 *   过滤 installed（且非内置）→ 我的已装；过滤 default_enabled → 内置只读列表
 * 写操作复用市场 store 的开关语义（`PUT /me/capabilities/...`）与 `/me/skills|experts` 的 CRUD。
 */
import { create } from 'zustand'
import type { CatalogItem, MyCapability } from '@/types'
import { api } from '@/api/client'

/** 后端 /me/skills 与 /me/experts 的行形状。 */
interface MyRow {
  name: string
  description: string
  source: 'mine' | 'installed'
  enabled: boolean
  builtin: boolean
  revoked?: boolean
}
interface MyExpertRow {
  id: string
  name: string
  avatar: string
  description: string
  source: 'mine' | 'installed'
  enabled: boolean
  builtin: boolean
}
/** 自建技能/专家的提交体。 */
export interface SkillDraft {
  name: string
  description: string
  content: string
}
export interface ExpertDraft {
  name: string
  avatar: string
  description: string
  system_prompt: string
  tool_whitelist: string[]
}

interface MyCapabilitiesState {
  items: MyCapability[]
  /** 平台内置条目（管理员配置 default_enabled）：只读展示，用户无启停。 */
  builtinItems: MyCapability[]
  loaded: boolean
  loadMine: () => Promise<void>
  createSkill: (draft: SkillDraft) => Promise<void>
  updateSkill: (name: string, draft: Omit<SkillDraft, 'name'>) => Promise<void>
  deleteSkill: (name: string) => Promise<void>
  createExpert: (draft: ExpertDraft) => Promise<void>
  updateExpert: (id: string, draft: ExpertDraft) => Promise<void>
  deleteExpert: (id: string) => Promise<void>
}

export const useMyCapabilitiesStore = create<MyCapabilitiesState>((set, get) => ({
  items: [],
  builtinItems: [],
  loaded: false,

  loadMine: async () => {
    const [skills, experts, markets] = await Promise.all([
      api<MyRow[]>('/api/v1/me/skills'),
      api<MyExpertRow[]>('/api/v1/me/experts'),
      Promise.all([
        api<CatalogItem[]>('/api/v1/market/skill'),
        api<CatalogItem[]>('/api/v1/market/expert'),
        api<CatalogItem[]>('/api/v1/market/plugin'),
      ]),
    ])
    const [skillRows, expertRows, pluginRows] = markets
    const toMine = (kind: MyCapability['kind'], p: CatalogItem): MyCapability => ({
      kind,
      id: p.id,
      name: p.name,
      description: p.description,
      source: p.default_enabled ? 'builtin' : 'installed',
      enabled: p.default_enabled || p.enabled,
      builtin: true,
    })
    const items: MyCapability[] = [
      ...skills.map((s) => ({
        kind: 'skill' as const,
        id: s.name,
        name: s.name,
        description: s.description,
        source: s.source,
        enabled: s.enabled,
        builtin: s.builtin,
        revoked: s.revoked,
      })),
      ...experts.map((e) => ({
        kind: 'expert' as const,
        id: e.id,
        name: e.name,
        description: e.description,
        source: e.source,
        enabled: e.enabled,
        builtin: e.builtin,
      })),
      // 已装插件（内置插件不进 items——用户对其无启停概念）
      ...pluginRows.filter((p) => p.installed && !p.default_enabled).map((p) => toMine('plugin', p)),
    ]
    const builtinItems: MyCapability[] = [
      ...skillRows.filter((p) => p.default_enabled).map((p) => toMine('skill', p)),
      ...expertRows.filter((p) => p.default_enabled).map((p) => toMine('expert', p)),
      ...pluginRows.filter((p) => p.default_enabled).map((p) => toMine('plugin', p)),
    ]
    set({ items, builtinItems, loaded: true })
  },

  createSkill: async (draft) => {
    await api('/api/v1/me/skills', { method: 'POST', body: draft })
    await get().loadMine()
  },

  updateSkill: async (name, draft) => {
    await api(`/api/v1/me/skills/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: draft,
    })
    await get().loadMine()
  },

  deleteSkill: async (name) => {
    await api(`/api/v1/me/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
    await get().loadMine()
  },

  createExpert: async (draft) => {
    await api('/api/v1/me/experts', { method: 'POST', body: draft })
    await get().loadMine()
  },

  updateExpert: async (id, draft) => {
    await api(`/api/v1/me/experts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: draft,
    })
    await get().loadMine()
  },

  deleteExpert: async (id) => {
    await api(`/api/v1/me/experts/${encodeURIComponent(id)}`, { method: 'DELETE' })
    await get().loadMine()
  },
}))
