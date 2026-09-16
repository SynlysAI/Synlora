/**
 * 用户侧「能力中心」整页（/capabilities，任意登录用户可见）。
 *
 * 结构：顶栏 + 左导航（专家/技能/插件，照 AdminLayout 的 nav）+ 右侧内容。
 * 右侧按路由渲染列表页（本文件内）或详情页（CapabilityDetail）。
 *
 * 列表页：顶部「市场 | 我的」页签（state 在壳层，照 jiuwen ConnectorMarket 的
 * topTab——范围是同一列表的过滤视图，不是独立资源）+ 搜索（前端过滤）+
 * 卡片网格（card-grid-auto，照 jiuwen）。
 *
 * 卡片动作：市场卡未安装给「安装」快按钮（带 config_schema 的插件先弹配置框）；
 * 我的卡给「启用/停用」快按钮。其余操作都在详情页（点卡片进入）。
 * 内置条目（default_enabled）对普通用户只读。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { PageTopBar, ToastHost } from '@/components/layout'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import { useRouterStore } from '@/routing/router'
import type { CapabilityKind, AppRoute } from '@/routing/route'
import type { CatalogItem, MyCapability } from '@/types'
import CapabilityCard, { CardBadge } from './CapabilityCard'
import CapabilityDetail from './CapabilityDetail'
import MinePanel from './MinePanel'
import { PluginInstallModal } from './CapabilityModals'

/** 左导航项（类型 → 展示名）。 */
const NAV: Array<{ kind: CapabilityKind; label: string }> = [
  { kind: 'expert', label: '专家' },
  { kind: 'skill', label: '技能' },
  { kind: 'plugin', label: '插件' },
]

/** 类型图标（16px 线性，与全站图标风格一致）。 */
function kindIcon(kind: CapabilityKind): ReactNode {
  const common = {
    width: 13,
    height: 13,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  if (kind === 'expert') {
    return (
      <svg {...common}>
        <circle cx="8" cy="5.5" r="2.75" />
        <path d="M2.75 13.5c0-2.6 2.35-4.25 5.25-4.25s5.25 1.65 5.25 4.25" />
      </svg>
    )
  }
  if (kind === 'skill') {
    return (
      <svg {...common}>
        <path d="M8 2.5 9.6 6l3.65.35-2.75 2.5.8 3.65L8 10.7l-3.3 1.8.8-3.65-2.75-2.5L6.4 6Z" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path d="M6.5 2.5h3v1.6a2.2 2.2 0 0 1 1.1 1.9V12a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 2.6 12V6a2.2 2.2 0 0 1 1.1-1.9V2.5" />
      <path d="M4 8.5h5" />
    </svg>
  )
}

/** 安装图标（市场卡快按钮）。 */
const INSTALL_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <path d="M8 3.5v9M3.5 8h9" />
  </svg>
)

/** 适配某类型的卡片网格（无条目时渲染空态卡）。 */
function CardGrid({ items, empty, children }: {
  items: number
  empty: string
  children: ReactNode
}) {
  if (items === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--sa-radius-lg)] border border-dashed border-[var(--sa-alias-border-l2)] py-16 text-[13px] text-[var(--sa-alias-label-caption)]">
        {empty}
      </div>
    )
  }
  return (
    <div className="grid justify-center gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">
      {children}
    </div>
  )
}

/** 能力中心整页壳 + 列表页。 */
export default function CapabilityCenter({ route }: { route: AppRoute }) {
  const capabilityKind: CapabilityKind =
    route.kind === 'capabilities' || route.kind === 'capability-detail'
      ? route.capabilityKind
      : 'expert'
  // 页签提到壳层：进详情页会卸载列表页、返回时再重建，state 放列表页内会被重置为「市场」
  const [tab, setTab] = useState<'market' | 'mine'>('market')

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <PageTopBar title="能力中心" />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左导航：类型切换（照 AdminLayout 的 nav 样式） */}
        <nav
          aria-label="能力类型切换"
          className="flex w-[188px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--sa-alias-border-l1)] p-3"
        >
          {NAV.map((item) => {
            const active = item.kind === capabilityKind
            return (
              <button
                key={item.kind}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => useRouterStore.getState().navigate({
                  kind: 'capabilities', capabilityKind: item.kind,
                })}
                className={`flex h-10 items-center gap-2 rounded-[var(--sa-radius-md)] px-3 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
                  active
                    ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] font-medium text-[var(--sa-alias-label-primary)]'
                    : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]'
                }`}
              >
                {kindIcon(item.kind)}
                <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
              </button>
            )
          })}
        </nav>

        {/* 右侧内容：详情页整页接管，列表页走 CapabilityList */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {route.kind === 'capability-detail' ? (
            <CapabilityDetail capabilityKind={route.capabilityKind} itemId={route.itemId} />
          ) : (
            <CapabilityList capabilityKind={capabilityKind} tab={tab} onTabChange={setTab} />
          )}
        </main>
      </div>
      <ToastHost />
    </div>
  )
}

/** 列表页（市场 / 我的）。 */
function CapabilityList({ capabilityKind, tab, onTabChange }: {
  capabilityKind: CapabilityKind
  /** 页签由壳层持有：列表页会随「进详情/返回」卸载重建，放这里会被重置。 */
  tab: 'market' | 'mine'
  onTabChange: (tab: 'market' | 'mine') => void
}) {
  const byKind = useCatalogStore((s) => s.byKind)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
  const install = useCatalogStore((s) => s.install)
  const setEnabled = useCatalogStore((s) => s.setEnabled)

  const [marketQuery, setMarketQuery] = useState('')
  const [mineQuery, setMineQuery] = useState('')
  /** 需要先填配置再安装的插件（null 关闭）。 */
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)
  /** 快按钮进行中的条目（`kind:id`）。 */
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    loadMarket().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadMarket])

  const rows = byKind[capabilityKind]
  const query = tab === 'market' ? marketQuery : mineQuery
  const setQuery = tab === 'market' ? setMarketQuery : setMineQuery

  /** 搜索过滤（名称 + 描述）。 */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (r) => r.name.toLowerCase().includes(q) || r.description.toLowerCase().includes(q),
    )
  }, [rows, query])

  const goDetail = (id: string) =>
    useRouterStore.getState().navigate({
      kind: 'capability-detail', capabilityKind, itemId: id,
    })

  /** 安装：带配置 schema 的插件先开表单，其余直装。 */
  const handleInstall = async (item: CatalogItem) => {
    if (item.config_schema && item.config_schema.length > 0) {
      setConfiguring(item)
      return
    }
    setBusy(`${item.kind}:${item.id}`)
    try {
      await install(item.kind, item.id)
      toast('success', `已安装 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy(null)
    }
  }

  /** 启用/停用（我的页签的快按钮）。 */
  const handleToggle = async (item: MyCapability) => {
    setBusy(`${item.kind}:${item.id}`)
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      // 我的列表与市场是两个 store，启停后必须各自刷新，否则徽标与子页签过滤不会更新。
      // 写已落库，重拉失败只影响刷新，不该报成"操作失败"（下次加载自愈）。
      await useMyCapabilitiesStore.getState().loadMine().catch(() => undefined)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto w-full max-w-[1200px] px-6 py-6">
      {/* 工具行：页签 + 搜索 + 我的态新建入口 */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          {([['market', '市场'], ['mine', '我的']] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-selected={tab === key}
              role="tab"
              onClick={() => onTabChange(key)}
              className={
                tab === key
                  ? 'rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-[5px] text-[13px] font-medium text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-md)] border border-transparent px-3 py-[5px] text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {label}
            </button>
          ))}
        </div>

        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={tab === 'market' ? '搜索可用能力' : '搜索我的能力'}
          aria-label={tab === 'market' ? '搜索可用能力' : '搜索我的能力'}
          className="h-8 max-w-[320px] min-w-0 flex-1 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-2.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
        />
      </div>

      <div className="pt-5">
        {tab === 'market' ? (
          <CardGrid items={filtered.length} empty={query ? '无匹配能力' : `暂无可用${NAV.find((n) => n.kind === capabilityKind)?.label ?? ''}`}>
            {filtered.map((item) => (
              <CapabilityCard
                key={item.id}
                title={item.name}
                description={item.description}
                avatar={item.avatar}
                badges={
                  <>
                    {item.default_enabled ? (
                      <CardBadge>内置 · 全员可用</CardBadge>
                    ) : item.installed ? (
                      <CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge>
                    ) : null}
                  </>
                }
                actionIcon={!item.installed && !item.default_enabled ? INSTALL_ICON : undefined}
                actionLabel={`安装 ${item.name}`}
                actionBusy={busy === `${item.kind}:${item.id}`}
                onAction={() => void handleInstall(item)}
                onClick={() => goDetail(item.id)}
              />
            ))}
          </CardGrid>
        ) : (
          <MinePanel
            capabilityKind={capabilityKind}
            query={mineQuery}
            onOpen={(item) => goDetail(item.id)}
            onToggle={(item) => void handleToggle(item)}
            busyKey={busy}
          />
        )}
      </div>

      {configuring && (
        <PluginInstallModal
          item={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(message) => {
            setConfiguring(null)
            toast('success', message)
          }}
        />
      )}
    </div>
  )
}
