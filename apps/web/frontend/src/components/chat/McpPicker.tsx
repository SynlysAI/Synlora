/**
 * 「+」菜单「MCP」二级面板：对本会话附加的 MCP 开关（照 PluginPicker 语义）。
 *
 * **默认不附加**：只有显式打开的 MCP 才在本会话注入工具；切换即 PATCH 会话
 * 文档持久化，草稿态（会话未落库）先存 sessions store 的 draftEnabledMcp，
 * 首次发送随建会话一起写进会话。
 *
 * 数据源：GET /api/v1/me/mcps/panel（自建 enabled ∪ 可见公共），分组展示，
 * 公共条目带「公共」徽标；error 状态标灰并提示失败原因。
 */
import { useEffect, useState } from 'react'
import { Switch } from '@/components/admin/shared'
import { api } from '@/api/client'
import { toast } from '@/stores/toasts'
import { useSessionsStore } from '@/stores/sessions'
import type { McpPanelItem } from '@/types'
import PickerPanel from './PickerPanel'

interface McpPickerProps {
  /** 一级菜单展开方向（同步二级面板的生长方向）。 */
  direction: 'up' | 'down'
}

/** MCP 附加面板（会话级开关）。 */
export default function McpPicker({ direction }: McpPickerProps) {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = sessions.find((x) => x._id === currentId) ?? null
  const setEnabledMcp = useSessionsStore((s) => s.setEnabledMcp)
  const setDraftMcp = useSessionsStore((s) => s.setDraftMcp)
  const draftEnabledMcp = useSessionsStore((s) => s.draftEnabledMcp)
  const [items, setItems] = useState<McpPanelItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  // 首次展开拉取合并视图（失败仅面板内提示，不影响发送）
  useEffect(() => {
    if (items !== null) return
    api<McpPanelItem[]>('/api/v1/me/mcps/panel')
      .then(setItems)
      .catch((err: Error) => setError(err.message))
  }, [items])

  /** 当前附加集合：默认不附加（null/[] 同义），只含显式打开的 MCP。 */
  const effective = session
    ? session.enabled_mcp ?? []
    : draftEnabledMcp ?? []

  /** 切换某 MCP 开关：写会话（PATCH）或草稿暂存；全集等于打开集时不回落 null（显式集合更直观）。 */
  const toggle = (id: string) => {
    const next = effective.includes(id)
      ? effective.filter((x) => x !== id)
      : [...effective, id]
    if (session) {
      setEnabledMcp(session._id, next).catch((err: Error) =>
        toast('error', `MCP 开关保存失败：${err.message}`),
      )
    } else {
      setDraftMcp(next)
    }
  }

  const q = query.trim().toLowerCase()
  const filtered = (items ?? []).filter(
    (m) => !q || m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
  )
  const mine = filtered.filter((m) => m.source === 'user')
  const shared = filtered.filter((m) => m.source === 'catalog')

  return (
    <PickerPanel
      direction={direction}
      widthClass="w-[300px]"
      maxHeightClass="max-h-[358px]"
      ariaLabel="本会话 MCP 附加"
      testId="composer-mcp-picker"
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索 MCP"
    >
      {error ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-state-error-primary)]">
          MCP 加载失败：{error}
        </div>
      ) : items === null ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          加载中…
        </div>
      ) : items.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          暂无可用 MCP（去「能力中心」添加或安装）
        </div>
      ) : (
        <>
          <div className="px-2 pb-1 pt-0.5 text-[11px] text-[var(--sa-alias-label-caption)]">
            默认不附加；打开后本会话启用该 MCP 的工具（刷新保持）
          </div>
          {[
            { label: '我的 MCP', rows: mine },
            { label: '公共 MCP', rows: shared },
          ].map(({ label, rows }) =>
            rows.length ? (
              <div key={label}>
                <div className="px-2 pb-0.5 pt-1.5 text-[11px] font-medium text-[var(--sa-alias-label-caption)]">
                  {label}
                </div>
                {rows.map((m) => (
                  <div
                    key={m.id}
                    title={m.status === 'error' ? `连接失败：${m.last_error}` : m.description}
                    className={`flex w-full items-start gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 transition-colors duration-[var(--sa-duration-fast)] ${
                      m.status === 'error'
                        ? 'opacity-60'
                        : 'hover:bg-[var(--sa-alias-interactive-bg-hover)]'
                    }`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                        {m.name}
                        {m.source === 'catalog' && (
                          <span className="ml-1 rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-1 text-[10px] text-[var(--sa-alias-label-caption)]">
                            公共
                          </span>
                        )}
                        {m.status === 'error' && (
                          <span className="ml-1 text-[11px] text-[var(--sa-alias-state-error-primary)]">
                            连接失败
                          </span>
                        )}
                      </span>
                      <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
                        {m.tool_count > 0 ? `${m.tool_count} 个工具 · ${m.transport}` : m.transport}
                      </span>
                    </span>
                    <span className="mt-0.5 shrink-0">
                      <Switch
                        checked={effective.includes(m.id)}
                        label={`${m.name} 在本会话附加`}
                        onChange={() => toggle(m.id)}
                      />
                    </span>
                  </div>
                ))}
              </div>
            ) : null,
          )}
        </>
      )}
    </PickerPanel>
  )
}
