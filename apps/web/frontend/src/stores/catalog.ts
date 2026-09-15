/**
 * 用户侧能力中心-市场 store：按类型拉取可安装条目 + 安装/启用/停用/卸载。
 *
 * 走 `/api/v1/market/{kind}`（普通用户视角，不出现 hidden 条目）；安装与开关统一
 * 走 `PUT /api/v1/me/capabilities/{kind}/{id}`。插件带个人配置时改走既有
 * `POST /api/v1/catalog/plugin/{id}/install`——它按 schema 先校验必填再落安装记录
 * （原子），避免"先装后写配置失败"留下半装状态。
 * 任何写操作成功后重拉市场列表（不做乐观更新，与 PluginsAdmin 策略开关一致）。
 */
import { create } from 'zustand'
import type { CatalogItem } from '@/types'
import { api } from '@/api/client'

/** 市场按类型分组存放（三类分别拉取，避免一次请求混排后再在前端切分）。 */
type KindMap = Record<CatalogItem['kind'], CatalogItem[]>

const EMPTY: KindMap = { expert: [], skill: [], plugin: [] }
const KINDS: CatalogItem['kind'][] = ['expert', 'skill', 'plugin']

interface CatalogState {
  /** 当前用户可见的市场条目（三类分组）。 */
  byKind: KindMap
  /** 三类是否均完成首次成功加载。 */
  loaded: boolean
  /** 拉取市场三类条目。 */
  loadMarket: () => Promise<void>
  /** 安装条目（插件可带个人配置），成功后重拉。 */
  install: (kind: CatalogItem['kind'], itemId: string, config?: Record<string, string>) => Promise<void>
  /** 卸载条目，成功后重拉。 */
  uninstall: (kind: CatalogItem['kind'], itemId: string) => Promise<void>
  /** 启用/停用已安装条目，成功后重拉。 */
  setEnabled: (kind: CatalogItem['kind'], itemId: string, enabled: boolean) => Promise<void>
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  byKind: EMPTY,
  loaded: false,

  loadMarket: async () => {
    const lists = await Promise.all(
      KINDS.map((kind) => api<CatalogItem[]>(`/api/v1/market/${kind}`)),
    )
    const byKind = { ...EMPTY }
    KINDS.forEach((kind, index) => {
      byKind[kind] = lists[index]
    })
    set({ byKind, loaded: true })
  },

  install: async (kind, itemId, config) => {
    // 插件带个人配置时走既有 POST：它按 schema 先校验必填、通过后才落安装记录（原子）。
    // 若改成"先 PUT 装、再补 POST 写配置"，POST 校验失败时会留下"已装但无配置"的
    // 半装状态——PUT 已 200、前端无从回滚，运行期插件工具只会报"未配置"。
    if (kind === 'plugin' && config && Object.keys(config).length > 0) {
      await api(`/api/v1/catalog/${kind}/${encodeURIComponent(itemId)}/install`, {
        method: 'POST',
        body: { config },
      })
    } else {
      await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
        method: 'PUT',
        body: { installed: true },
      })
    }
    await get().loadMarket()
  },

  uninstall: async (kind, itemId) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { installed: false },
    })
    await get().loadMarket()
  },

  setEnabled: async (kind, itemId, enabled) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { enabled },
    })
    await get().loadMarket()
  },
}))
