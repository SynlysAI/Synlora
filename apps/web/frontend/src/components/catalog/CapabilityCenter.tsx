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
import ExtensionCenter from './ExtensionCenter'
import MinePanel, { type SubTab } from './MinePanel'
import { PluginInstallModal } from './CapabilityModals'

/** 左导航项（类型 → 展示名）。 */
const NAV: Array<{ kind: CapabilityKind; label: string }> = [
  { kind: 'expert', label: '专家' },
  { kind: 'skill', label: '技能' },
  { kind: 'plugin', label: '扩展' },
]

/**
 * 各类型的页内标题与副标题：随左导航切换（顶栏固定为「能力中心」，页内标题才是当前模块，
 * 两者不重复）。页签文案也由此拼出——「专家市场 / 我的专家」。
 */
const KIND_HEADING: Record<CapabilityKind, { title: string; subtitle: string }> = {
  expert: { title: '专家管理', subtitle: '平台提供的专家，安装后即可在自己的对话里选用' },
  skill: { title: '技能管理', subtitle: '平台提供的技能，安装后即可在自己的对话里调用' },
  plugin: { title: '扩展中心', subtitle: '统一管理平台插件与你接入的 MCP 工具服务' },
}

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

/** 工具行搜索框（照参考项目 PageToolbarSearch：前置放大镜、12px 字、有值时显示清除按钮）。 */
function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  return (
    <div className="relative w-[320px] max-w-full shrink-0">
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--sa-alias-label-tertiary)]"
        aria-hidden="true"
      >
        <path d="M21 21l-4.35-4.35M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z" />
      </svg>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full rounded-[6px] border border-[var(--sa-alias-border-l2)] py-1.5 pl-8 pr-7 text-xs text-[var(--sa-alias-label-primary)] outline-none transition-colors duration-[var(--sa-duration-fast)] placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
      />
      {value.length > 0 && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="清除搜索"
          className="absolute right-2 top-1/2 flex h-4 w-4 -translate-y-1/2 items-center justify-center text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  )
}

/** 占位卡（虚线圈内一行提示，空态与加载态共用）。 */
function PlaceholderCard({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--sa-radius-lg)] border border-dashed border-[var(--sa-alias-border-l2)] py-16 text-[13px] text-[var(--sa-alias-label-caption)]">
      {text}
    </div>
  )
}

/** 适配某类型的卡片网格（无条目时渲染空态卡）。 */
function CardGrid({ items, empty, children }: {
  items: number
  empty: string
  children: ReactNode
}) {
  if (items === 0) {
    return <PlaceholderCard text={empty} />
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
  // 页签、搜索词与「我的」子页签提到壳层：进详情页会卸载列表页、返回时再重建，
  // state 放列表页内会被重置（页签回「市场」、搜索清空、子页签回「已启用」）
  const [tab, setTab] = useState<'market' | 'mine'>('market')
  const [marketQuery, setMarketQuery] = useState('')
  const [mineQuery, setMineQuery] = useState('')
  const [mineSub, setMineSub] = useState<SubTab>('enabled')

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
            // key=itemId：详情页 state 只在挂载时初始化，换条目必须重建，否则会残留上一条的内容
            <CapabilityDetail
              key={route.itemId}
              capabilityKind={route.capabilityKind}
              itemId={route.itemId}
            />
          ) : capabilityKind === 'plugin' ? (
            <ExtensionCenter />
          ) : (
            <CapabilityList
              capabilityKind={capabilityKind}
              tab={tab}
              onTabChange={setTab}
              marketQuery={marketQuery}
              onMarketQueryChange={setMarketQuery}
              mineQuery={mineQuery}
              onMineQueryChange={setMineQuery}
              mineSub={mineSub}
              onMineSubChange={setMineSub}
            />
          )}
        </main>
      </div>
      <ToastHost />
    </div>
  )
}

/** 列表页（市场 / 我的）。 */
function CapabilityList({
  capabilityKind, tab, onTabChange,
  marketQuery, onMarketQueryChange, mineQuery, onMineQueryChange,
  mineSub, onMineSubChange,
}: {
  capabilityKind: CapabilityKind
  /** 页签、搜索词、子页签由壳层持有：列表页会随「进详情/返回」卸载重建，放这里会被重置。 */
  tab: 'market' | 'mine'
  onTabChange: (tab: 'market' | 'mine') => void
  marketQuery: string
  onMarketQueryChange: (query: string) => void
  mineQuery: string
  onMineQueryChange: (query: string) => void
  mineSub: SubTab
  onMineSubChange: (sub: SubTab) => void
}) {
  const byKind = useCatalogStore((s) => s.byKind)
  const marketLoaded = useCatalogStore((s) => s.loaded)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
  const install = useCatalogStore((s) => s.install)
  const setEnabled = useCatalogStore((s) => s.setEnabled)

  /** 需要先填配置再安装的插件（null 关闭）。 */
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)
  /** 快按钮进行中的条目（`kind:id`）。 */
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    loadMarket().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadMarket])

  const rows = byKind[capabilityKind]
  const query = tab === 'market' ? marketQuery : mineQuery
  const setQuery = tab === 'market' ? onMarketQueryChange : onMineQueryChange
  /** 类型名（专家/技能/插件）：页签文案带它，切类型时能一眼看出范围变了。 */
  const kindLabel = NAV.find((n) => n.kind === capabilityKind)?.label ?? ''
  const tabs = [
    { key: 'market' as const, label: `${kindLabel}市场` },
    { key: 'mine' as const, label: `我的${kindLabel}` },
  ]

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
    <div className="mx-auto w-full max-w-[1400px] px-12 pt-8 pb-10">
      {/* 页头：照参考项目 PageHeader（24px semibold 主标题 + 14px 次要色副标题），
          标题与副标题随左导航的类型切换（顶栏固定为「能力中心」） */}
      <header>
        <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">
          {KIND_HEADING[capabilityKind].title}
        </h2>
        <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
          {KIND_HEADING[capabilityKind].subtitle}
        </p>
      </header>

      {/* 工具行：照参考项目 .page-toolbar（上 32 / 下 16 间距，页签靠左、搜索靠右） */}
      <div className="mb-4 mt-8 flex items-center justify-between gap-2">
        {/* 页签：照参考项目 .chat-picker-panel__tabs——16px 字、18px 间距、
            整行 1px 分隔线上压激活项的 2px 下划线（非胶囊按钮） */}
        <div role="tablist" className="relative flex items-stretch gap-[18px] text-base">
          <span
            aria-hidden="true"
            className="absolute inset-x-0 bottom-0 h-px bg-[var(--sa-alias-border-l2)]"
          />
          {tabs.map(({ key, label }) => {
            const active = tab === key
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onTabChange(key)}
                className={`relative whitespace-nowrap px-0.5 pb-2 transition-colors duration-[var(--sa-duration-fast)] ${
                  active
                    ? 'font-semibold text-[var(--sa-alias-label-primary)]'
                    : 'text-[var(--sa-alias-label-secondary)] hover:text-[var(--sa-alias-label-primary)]'
                }`}
              >
                {label}
                {active && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-0 bottom-0 h-0.5 bg-[var(--sa-alias-label-primary)]"
                  />
                )}
              </button>
            )
          })}
        </div>

        <SearchBox
          value={query}
          onChange={setQuery}
          placeholder={tab === 'market' ? '搜索可用能力' : '搜索我的能力'}
        />
      </div>

      <div>
        {tab === 'market' ? (
          marketLoaded ? (
            <CardGrid items={filtered.length} empty={query ? '无匹配能力' : `暂无可用${kindLabel}`}>
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
            // 首帧还没拉到市场数据（或三类全失败），此时 filtered 恒为空，
            // 直接判空会误报「暂无可用专家」，所以加载完成前只显示占位
            <PlaceholderCard text="加载中…" />
          )
        ) : (
          <MinePanel
            capabilityKind={capabilityKind}
            kindLabel={kindLabel}
            query={mineQuery}
            sub={mineSub}
            onSubChange={onMineSubChange}
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
