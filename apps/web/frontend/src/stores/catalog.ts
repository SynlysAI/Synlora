/**
 * 用户侧能力中心-市场 store：按类型拉取可安装条目 + 安装/启用/停用/卸载。
 *
 * 走 `/api/v1/market/{kind}`（普通用户视角，不出现 hidden 条目）；开关与卸载走
 * `PUT /api/v1/me/capabilities/{kind}/{id}`，插件安装一律走既有
 * `POST /api/v1/catalog/plugin/{id}/install`——它按 schema 先校验必填再落安装记录
 * （原子），避免"先装后写配置失败"留下半装状态。
 * 拉取逐类容错：某类失败不影响其余两类，三类全失败才抛错。
 * 任何写操作成功后重拉市场列表（不做乐观更新，与 PluginsAdmin 策略开关一致），
 * 且重拉失败不算写失败（写已落库，下次加载自愈）。
 */
import { create } from 'zustand'
import type { CapabilityDetail, CatalogItem, PluginConfigSnapshot } from '@/types'
import { api } from '@/api/client'

/** 市场按类型分组存放（四类分别拉取，避免一次请求混排后再在前端切分）。 */
type KindMap = Record<CatalogItem['kind'], CatalogItem[]>

const EMPTY: KindMap = { expert: [], skill: [], plugin: [], mcp: [] }
const KINDS: CatalogItem['kind'][] = ['expert', 'skill', 'plugin', 'mcp']

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
  /**
   * 更新已安装插件的个人配置（敏感字段留空 = 保持原值，由后端存储层合并）。
   *
   * 不重拉市场列表：配置只影响插件自己的运行参数，市场行的可见性与开关态都不变；
   * 调用方（详情页）自己 refresh 详情即可拿到最新值。
   */
  updatePluginConfig: (
    pluginId: string,
    config: Record<string, string>,
  ) => Promise<PluginConfigSnapshot>
  /** 拉取单个能力详情（详情页与编辑回填共用；不缓存，每次实时读）。 */
  loadDetail: (kind: CatalogItem['kind'], itemId: string) => Promise<CapabilityDetail>
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  byKind: EMPTY,
  loaded: false,

  loadMarket: async () => {
    // 逐类容错：某一类拉取失败不应连累其余两类（旧实现任一失败整批失败，
    // 页面三组全空、连空态文案都不出）。失败的类型保留上一次的数据。
    const settled = await Promise.allSettled(
      KINDS.map((kind) => api<CatalogItem[]>(`/api/v1/market/${kind}`)),
    )
    const byKind = { ...get().byKind }
    let failed = 0
    settled.forEach((res, index) => {
      const kind = KINDS[index]
      if (res.status === 'fulfilled') byKind[kind] = res.value
      else failed += 1
    })
    // 三类全部失败：抛第一个错误，让上层能提示；否则至少有一类成功即可标记已加载。
    if (failed === KINDS.length) {
      throw (settled[0] as PromiseRejectedResult).reason
    }
    set({ byKind, loaded: true })
  },

  install: async (kind, itemId, config) => {
    // 插件一律走 POST：它按 schema 先校验必填、通过后才落安装记录（原子）。
    // 不要按缓存里的 config_schema 判断走哪条路径：详情页路由下市场缓存是空的，
    // 插件会被误判为"无配置"而走 PUT，用户的配置被丢弃且跳过必填校验。
    // 也不能按"config 是否非空"判断——required+secret 字段允许空提交，那样会绕过校验留下半装状态。
    // POST 在空配置时跳过校验并照常写安装记录，是 PUT 路径的严格超集。
    if (kind === 'plugin') {
      await api(`/api/v1/catalog/${kind}/${encodeURIComponent(itemId)}/install`, {
        method: 'POST',
        body: { config: config ?? {} },
      })
    } else {
      await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
        method: 'PUT',
        body: { installed: true },
      })
    }
    // 写已成功；重拉失败只影响列表刷新，不应报成"安装失败"（下次加载会自愈）
    await get().loadMarket().catch(() => undefined)
  },

  uninstall: async (kind, itemId) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { installed: false },
    })
    // 写已成功；重拉失败只影响列表刷新，不应报成"卸载失败"（下次加载会自愈）
    await get().loadMarket().catch(() => undefined)
  },

  setEnabled: async (kind, itemId, enabled) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { enabled },
    })
    // 写已成功；重拉失败只影响列表刷新，不应报成"操作失败"（下次加载会自愈）
    await get().loadMarket().catch(() => undefined)
  },

  updatePluginConfig: async (pluginId, config) => api<PluginConfigSnapshot>(
    `/api/v1/me/plugins/${encodeURIComponent(pluginId)}/config`,
    { method: 'PUT', body: { config } },
  ),

  loadDetail: async (kind, itemId) => {
    // 路径与 PUT 同源（/me/capabilities/{kind}/{item_id}），语义为「我视角下的这个能力」；
    // 后端按「自建 → catalog 可见性」解析，前端不需要知道条目来源
    return api<CapabilityDetail>(
      `/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`,
    )
  },
}))
