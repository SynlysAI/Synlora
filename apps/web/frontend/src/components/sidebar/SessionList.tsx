/**
 * 会话列表：updated_at 倒序 + 归档折叠分组 + 搜索过滤 + 行内重命名 /
 * 归档切换 / 删除二次确认；当前会话用 nav-item active token 高亮。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Session } from '@/types'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
import { formatRelativeTime } from '@/utils/format'

/** 按更新时间分组标签（今天/昨天/更早）。 */
function groupLabel(updatedAt: number): string {
  const d = new Date(updatedAt)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, today)) return '今天'
  if (sameDay(d, yesterday)) return '昨天'
  return '更早'
}

/** 分组插入标题行（相邻同组只插一次）。 */
function withGroupHeaders(list: Session[]): { label: string; session: Session }[] {
  const out: { label: string; session: Session }[] = []
  let prev = ''
  for (const s of list) {
    const label = groupLabel(s.updated_at)
    out.push({ label: label === prev ? '' : label, session: s })
    prev = label
  }
  return out
}

interface SessionItemProps {
  /** 会话文档。 */
  session: Session
  /** 是否当前选中。 */
  active: boolean
  /** 选中会话（抽屉模式同时关闭抽屉）。 */
  onSelect: () => void
}

/** 单条会话项：主体按钮 + hover "⋯" 操作菜单（重命名/归档/删除）。 */
function SessionItem({ session, active, onSelect }: SessionItemProps) {
  const rename = useSessionsStore((s) => s.rename)
  const archive = useSessionsStore((s) => s.archive)
  const remove = useSessionsStore((s) => s.remove)
  const [menuOpen, setMenuOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(session.title)
  const menuRef = useRef<HTMLDivElement>(null)

  // 菜单外点击关闭并重置二次确认态
  useEffect(() => {
    if (!menuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
        setConfirming(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [menuOpen])

  /** 提交重命名（空标题取消）。 */
  const commitRename = async () => {
    const title = draft.trim()
    setEditing(false)
    if (!title || title === session.title) return
    try {
      await rename(session._id, title)
    } catch (err) {
      toast('error', `重命名失败：${(err as Error).message}`)
    }
  }

  /** 归档/取消归档。 */
  const toggleArchive = async () => {
    setMenuOpen(false)
    setConfirming(false)
    try {
      await archive(session._id, !session.archived)
      toast('success', session.archived ? '已取消归档' : '已归档')
    } catch (err) {
      toast('error', `归档失败：${(err as Error).message}`)
    }
  }

  /** 删除（二次确认后执行）。 */
  const handleDelete = async () => {
    if (!confirming) {
      setConfirming(true)
      return
    }
    setMenuOpen(false)
    setConfirming(false)
    try {
      await remove(session._id)
      toast('success', '会话已删除')
    } catch (err) {
      toast('error', `删除失败：${(err as Error).message}`)
    }
  }

  return (
    <div className="group relative">
      {editing ? (
        <input
          type="text"
          value={draft}
          autoFocus
          aria-label="会话标题"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void commitRename()
            if (e.key === 'Escape') setEditing(false)
          }}
          onBlur={() => void commitRename()}
          className="w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-button-ghost-active-border)] bg-[var(--sa-specific-input-major)] px-2.5 py-1.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={onSelect}
          title={session.title}
          className={`relative w-full rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)] ${
            active
              ? 'bg-[var(--sa-specific-sidebar-nav-item-active)]'
              : session.archived
                ? 'opacity-70'
                : ''
          }`}
        >
          {/* 选中态左侧强调条 */}
          {active && (
            <span
              aria-hidden="true"
              className="absolute left-0 top-1/2 h-[14px] w-[2.5px] -translate-y-1/2 rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-link)]"
            />
          )}
          <span className="block truncate pr-5 text-[13px] text-[var(--sa-alias-label-primary)]">
            {session.title || '新对话'}
          </span>
          <span className="flex items-center gap-1.5 pt-0.5 text-xs text-[var(--sa-alias-label-caption)]">
            <span>{formatRelativeTime(session.updated_at)}</span>
            {session.message_count > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span>{session.message_count} 条</span>
              </>
            )}
          </span>
        </button>
      )}

      {/* hover 操作入口（重命名态隐藏） */}
      {!editing && (
        <button
          type="button"
          aria-label={`会话操作：${session.title || '新对话'}`}
          onClick={() => {
            setMenuOpen((v) => !v)
            setConfirming(false)
          }}
          className={`absolute right-1 top-1.5 flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-opacity duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] ${
            menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          }`}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <circle cx="8" cy="3.5" r="1.2" />
            <circle cx="8" cy="8" r="1.2" />
            <circle cx="8" cy="12.5" r="1.2" />
          </svg>
        </button>
      )}

      {/* 操作菜单 */}
      {menuOpen && (
        <div
          ref={menuRef}
          className="absolute right-1 top-8 z-30 w-32 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
        >
          <button
            type="button"
            onClick={() => {
              setMenuOpen(false)
              setDraft(session.title)
              setEditing(true)
            }}
            className="w-full rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            重命名
          </button>
          <button
            type="button"
            onClick={() => void toggleArchive()}
            className="w-full rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            {session.archived ? '取消归档' : '归档'}
          </button>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className={`w-full rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] ${
              confirming
                ? 'bg-[var(--sa-alias-state-error-primary)] text-white'
                : 'text-[var(--sa-alias-state-error-primary)] hover:bg-[var(--sa-alias-interactive-bg-hover-danger)]'
            }`}
          >
            {confirming ? '确认删除？' : '删除'}
          </button>
        </div>
      )}
    </div>
  )
}

interface SessionListProps {
  /** 搜索关键词（按标题过滤，空串不过滤）。 */
  query: string
  /** 抽屉模式下选中会话后关闭抽屉。 */
  onNavigate?: () => void
}

/** 会话列表组件（左栏主体滚动区）。 */
export default function SessionList({ query, onNavigate }: SessionListProps) {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const setCurrent = useSessionsStore((s) => s.setCurrent)
  const [archivedOpen, setArchivedOpen] = useState(false)

  // 标题过滤 + updated_at 倒序（后端有序，patch 后本地重排兜底）
  const { active, archived } = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? sessions.filter((s) => s.title.toLowerCase().includes(q))
      : sessions
    const sorted = [...filtered].sort((a, b) => b.updated_at - a.updated_at)
    return {
      active: sorted.filter((s) => !s.archived),
      archived: sorted.filter((s) => s.archived),
    }
  }, [sessions, query])

  // 搜索态自动展开归档组，便于全库检索
  const showArchived = archivedOpen || query.trim().length > 0

  if (sessions.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-2 py-8 text-xs text-[var(--sa-alias-label-caption)]">
        暂无会话
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pb-1">
      {active.length === 0 && archived.length === 0 && (
        <div className="px-2.5 py-4 text-xs text-[var(--sa-alias-label-caption)]">
          无匹配会话
        </div>
      )}
      {withGroupHeaders(active).map(({ label, session: s }) => (
        <div key={s._id}>
          {label && (
            <div className="px-2.5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-[var(--sa-alias-label-caption)]">
              {label}
            </div>
          )}
          <SessionItem
            session={s}
            active={s._id === currentId}
            onSelect={() => {
              setCurrent(s._id)
              onNavigate?.()
            }}
          />
        </div>
      ))}

      {/* 归档折叠组 */}
      {archived.length > 0 && (
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setArchivedOpen((v) => !v)}
            aria-expanded={showArchived}
            className="flex w-full items-center gap-1 rounded-[var(--sa-radius-sm)] px-2 py-1 text-xs text-[var(--sa-alias-label-caption)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]"
          >
            <svg
              width="10"
              height="10"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`transition-transform duration-[var(--sa-duration-base)] ${showArchived ? 'rotate-90' : ''}`}
              aria-hidden="true"
            >
              <path d="M6 3.5 10.5 8 6 12.5" />
            </svg>
            已归档
            <span className="ml-0.5">{archived.length}</span>
          </button>
          {showArchived &&
            archived.map((s) => (
              <SessionItem
                key={s._id}
                session={s}
                active={s._id === currentId}
                onSelect={() => {
                  setCurrent(s._id)
                  onNavigate?.()
                }}
              />
            ))}
        </div>
      )}
    </div>
  )
}
