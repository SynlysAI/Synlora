/**
 * 用户侧能力中心 store：市场条目列表 + 安装/卸载。
 *
 * 与管理页 store（`stores/admin.ts` 的 catalog 是管理员视角、含 hidden 条目与
 * 策略）刻意分开：这里走 `/api/v1/catalog`（任意登录用户的可见市场视图），
 * 安装/卸载只写当前用户自己的记录，不携带任何管理员语义。
 *
 * 沿用既有 store 惯例——列表态集中在 zustand，组件 effect 只调用 loadCatalog；
 * 安装/卸载成功后重拉列表（不做乐观更新，与 PluginsAdmin 的策略开关一致）。
 */
import { create } from 'zustand'
import type { CatalogItem } from '@/types'
import { api } from '@/api/client'

interface CatalogState {
  /** 当前用户可见的市场条目（三类混排，按 kind 分组由页面负责）。 */
  items: CatalogItem[]
  /** items 是否完成首次成功加载。 */
  loaded: boolean
  /** 拉取当前用户可见的能力目录。 */
  loadCatalog: () => Promise<void>
  /** 安装条目（插件可带个人配置），成功后重拉列表。 */
  install: (kind: CatalogItem['kind'], itemId: string, config?: Record<string, string>) => Promise<void>
  /** 卸载条目（删除本人安装记录），成功后重拉列表。 */
  uninstall: (kind: CatalogItem['kind'], itemId: string) => Promise<void>
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  items: [],
  loaded: false,

  loadCatalog: async () => {
    const items = await api<CatalogItem[]>('/api/v1/catalog')
    set({ items, loaded: true })
  },

  install: async (kind, itemId, config) => {
    await api(`/api/v1/catalog/${kind}/${itemId}/install`, {
      method: 'POST',
      body: { config: config ?? {} },
    })
    await get().loadCatalog()
  },

  uninstall: async (kind, itemId) => {
    await api(`/api/v1/catalog/${kind}/${itemId}/install`, { method: 'DELETE' })
    await get().loadCatalog()
  },
}))
