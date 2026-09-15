/**
 * 「+」菜单「插件」二级面板：对本会话生效的插件开关（照 jiuwen 扩展面板语义）。
 *
 * **默认全关**：只有显式打开的插件才在本会话生效（工具、配置、技能索引、
 * 播种专家）。会话级开关（区别于技能的「本轮一次性」）：切换即 PATCH 会话
 * 文档持久化，刷新不丢；草稿态（会话未落库）先存 sessions store 的
 * draftEnabledPlugins，首次发送随建会话一起写进会话。
 *
 * 数据源：市场插件行（/api/v1/market/plugin）里「对该用户生效」的插件——
 * 内置（default_enabled）或已装且启用。
 */
import { useEffect, useState } from 'react'
import { Switch } from '@/components/admin/shared'
import { api } from '@/api/client'
import { toast } from '@/stores/toasts'
import { useSessionsStore } from '@/stores/sessions'
import type { CatalogItem } from '@/types'
import PickerPanel from './PickerPanel'

interface PluginPickerProps {
  /** 一级菜单展开方向（同步二级面板的生长方向）。 */
  direction: 'up' | 'down'
}

/** 插件选择面板（会话级开关）。 */
export default function PluginPicker({ direction }: PluginPickerProps) {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = sessions.find((x) => x._id === currentId) ?? null
  const setEnabledPlugins = useSessionsStore((s) => s.setEnabledPlugins)
  const setDraftPlugins = useSessionsStore((s) => s.setDraftPlugins)
  const draftEnabledPlugins = useSessionsStore((s) => s.draftEnabledPlugins)
  const [plugins, setPlugins] = useState<CatalogItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  // 首次展开拉取市场插件行（失败仅面板内提示，不影响发送）
  useEffect(() => {
    if (plugins !== null) return
    api<CatalogItem[]>('/api/v1/market/plugin')
      .then((rows) => setPlugins(rows.filter((p) => p.default_enabled || (p.installed && p.enabled))))
      .catch((err: Error) => setError(err.message))
  }, [plugins])

  /** 当前启用集合：默认全关（null/[] 同义），只含显式打开的插件。 */
  const effective = session
    ? session.enabled_plugins ?? []
    : draftEnabledPlugins ?? []

  /** 切换某插件开关：写会话（PATCH）或草稿暂存；全集等于打开集时不回落 null（显式集合更直观）。 */
  const toggle = (id: string) => {
    const next = effective.includes(id)
      ? effective.filter((x) => x !== id)
      : [...effective, id]
    if (session) {
      setEnabledPlugins(session._id, next).catch((err: Error) =>
        toast('error', `插件开关保存失败：${err.message}`),
      )
    } else {
      setDraftPlugins(next)
    }
  }

  const q = query.trim().toLowerCase()
  const filtered = (plugins ?? []).filter(
    (p) => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q),
  )

  return (
    <PickerPanel
      direction={direction}
      widthClass="w-[300px]"
      maxHeightClass="max-h-[358px]"
      ariaLabel="本会话插件开关"
      testId="composer-plugin-picker"
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索插件"
    >
      {error ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-state-error-primary)]">
          插件加载失败：{error}
        </div>
      ) : plugins === null ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          加载中…
        </div>
      ) : plugins.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          暂无可用插件（去「能力中心」安装）
        </div>
      ) : (
        <>
          <div className="px-2 pb-1 pt-0.5 text-[11px] text-[var(--sa-alias-label-caption)]">
            默认关闭；打开后本会话启用该插件的工具、技能与专家（刷新保持）
          </div>
          {filtered.map((p) => (
            <div
              key={p.id}
              title={p.description}
              className="flex w-full items-start gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                  {p.name}
                </span>
                <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
                  {p.description}
                </span>
              </span>
              <span className="mt-0.5 shrink-0">
                <Switch
                  checked={effective.includes(p.id)}
                  label={`${p.name} 在本会话启用`}
                  onChange={() => toggle(p.id)}
                />
              </span>
            </div>
          ))}
        </>
      )}
    </PickerPanel>
  )
}
