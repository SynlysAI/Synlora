/**
 * 统一扩展中心：插件市场、MCP 接入和我的扩展集中展示。
 *
 * 插件仍沿用能力目录的安装与权限模型；MCP 是用户自己的 Streamable HTTP 连接，
 * 只在本页管理，不混入平台插件的安装记录。
 */
import { useEffect, useMemo, useState } from 'react'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import { useRouterStore } from '@/routing/router'
import type { CatalogItem, McpConnection } from '@/types'
import CapabilityCard, { CardBadge } from './CapabilityCard'
import McpEditor from './McpEditor'
import { PluginInstallModal } from './CapabilityModals'
import { Switch } from '@/components/admin/shared'

type Tab = 'market' | 'mcp' | 'mine'

/** 插件与 MCP 的统一扩展管理页。 */
export default function ExtensionCenter() {
  const plugins = useCatalogStore((state) => state.byKind.plugin)
  const marketLoaded = useCatalogStore((state) => state.loaded)
  const loadMarket = useCatalogStore((state) => state.loadMarket)
  const install = useCatalogStore((state) => state.install)
  const setEnabled = useCatalogStore((state) => state.setEnabled)
  const mine = useMyCapabilitiesStore((state) => state.items)
  const loadMine = useMyCapabilitiesStore((state) => state.loadMine)
  const mcps = useMyCapabilitiesStore((state) => state.mcps)
  const loadMcps = useMyCapabilitiesStore((state) => state.loadMcps)
  const deleteMcp = useMyCapabilitiesStore((state) => state.deleteMcp)
  const testMcp = useMyCapabilitiesStore((state) => state.testMcp)
  const [tab, setTab] = useState<Tab>('market')
  const [query, setQuery] = useState('')
  const [editor, setEditor] = useState<McpConnection | null | undefined>(undefined)
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    void loadMarket().catch((err) => toast('error', `加载扩展市场失败：${errorText(err)}`))
    void loadMine().catch(() => undefined)
    void loadMcps().catch((err) => toast('error', `加载 MCP 失败：${errorText(err)}`))
  }, [loadMarket, loadMine, loadMcps])

  const filteredPlugins = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? plugins.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(q)) : plugins
  }, [plugins, query])
  const installedPlugins = mine.filter((item) => item.kind === 'plugin')

  const installPlugin = async (item: CatalogItem) => {
    if (item.config_schema && item.config_schema.length > 0) {
      setConfiguring(item)
      return
    }
    setBusy(`install:${item.id}`)
    try {
      await install('plugin', item.id)
      toast('success', `已安装 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy('')
    }
  }

  const togglePlugin = async (item: { id: string; name: string; enabled: boolean }) => {
    setBusy(`toggle:${item.id}`)
    try {
      await setEnabled('plugin', item.id, !item.enabled)
      await loadMine().catch(() => undefined)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy('')
    }
  }

  const runMcp = async (id: string, action: () => Promise<void>, success: string) => {
    setBusy(id)
    try {
      await action()
      toast('success', success)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="mx-auto w-full max-w-[1400px] px-12 pt-8 pb-10">
      <header>
        <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">扩展中心</h2>
        <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">统一管理平台插件与你接入的 MCP 工具服务</p>
      </header>
      <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-b border-[var(--sa-alias-border-l2)]">
        <div className="flex gap-5">
          {([['market', '插件市场'], ['mcp', 'MCP 接入'], ['mine', '我的扩展']] as Array<[Tab, string]>).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setTab(key)} className={`border-b-2 px-1 pb-2 text-sm ${tab === key ? 'border-[var(--sa-alias-link)] font-medium text-[var(--sa-alias-label-primary)]' : 'border-transparent text-[var(--sa-alias-label-secondary)]'}`}>{label}</button>
          ))}
        </div>
        {tab !== 'mcp' && <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索扩展" className="mb-2 w-64 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-3 py-1.5 text-xs outline-none" />}
        {tab === 'mcp' && <button type="button" onClick={() => setEditor(null)} className="mb-2 rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-1.5 text-xs text-white">+ 新增 MCP</button>}
      </div>

      {tab === 'market' && (
        !marketLoaded ? <p className="py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">加载中…</p> : (
          <div className="mt-5 grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">
            {filteredPlugins.map((item) => <CapabilityCard key={item.id} title={item.name} description={item.description} badges={<CardBadge>{item.default_enabled ? '内置 · 全员可用' : item.installed ? '已安装' : '未安装'}</CardBadge>} actionIcon={!item.installed && !item.default_enabled ? <span>＋</span> : undefined} actionLabel={`安装 ${item.name}`} actionBusy={busy === `install:${item.id}`} onAction={() => void installPlugin(item)} onClick={() => useRouterStore.getState().navigate({ kind: 'capability-detail', capabilityKind: 'plugin', itemId: item.id })} />)}
            {filteredPlugins.length === 0 && <p className="col-span-full py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">暂无匹配插件</p>}
          </div>
        )
      )}

      {tab === 'mine' && <div className="mt-5 grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">{installedPlugins.map((item) => <CapabilityCard key={item.id} title={item.name} description={item.description} badges={<><CardBadge>插件</CardBadge><CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge></>} actionSlot={<Switch checked={item.enabled} label={`${item.name} 启用`} onChange={() => void togglePlugin(item)} disabled={busy === `toggle:${item.id}`} />} onClick={() => useRouterStore.getState().navigate({ kind: 'capability-detail', capabilityKind: 'plugin', itemId: item.id })} />)}{installedPlugins.length === 0 && <p className="col-span-full py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">还没有安装插件</p>}</div>}

      {tab === 'mcp' && <div className="mt-5 flex flex-col gap-3">{mcps.map((item) => <div key={item.id} className="flex flex-wrap items-center gap-3 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] p-4"><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><strong className="text-sm">{item.name}</strong><CardBadge>{item.status === 'connected' ? '已连接' : item.status === 'error' ? '连接失败' : '未测试'}</CardBadge><CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge></div><p className="mt-1 truncate text-xs text-[var(--sa-alias-label-secondary)]">{item.description || item.url}</p><p className="mt-1 text-[11px] text-[var(--sa-alias-label-caption)]">{item.tools.length} 个工具 · {item.url}</p></div><button type="button" disabled={busy === item.id} onClick={() => void runMcp(item.id, async () => { await testMcp(item.id) }, 'MCP 连接测试成功')} className="rounded-[var(--sa-radius-sm)] border px-3 py-1.5 text-xs">测试连接</button><button type="button" onClick={() => setEditor(item)} className="rounded-[var(--sa-radius-sm)] border px-3 py-1.5 text-xs">编辑</button><button type="button" onClick={() => void runMcp(item.id, () => deleteMcp(item.id), '已删除 MCP')} className="rounded-[var(--sa-radius-sm)] border px-3 py-1.5 text-xs">删除</button></div>)}{mcps.length === 0 && <p className="py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">还没有 MCP 接入</p>}</div>}
      {editor !== undefined && <McpEditor initial={editor} onClose={(changed) => { setEditor(undefined); if (changed) void loadMcps() }} />}
      {configuring && <PluginInstallModal item={configuring} onClose={() => setConfiguring(null)} onDone={(message) => { setConfiguring(null); toast('success', message) }} />}
    </div>
  )
}
