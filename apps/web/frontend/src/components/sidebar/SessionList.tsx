/**
 * 会话列表：updated_at 倒序 + 归档折叠分组 + 搜索过滤 + 行内重命名 /
 * 归档切换 / 删除（确认弹窗，与技术区删共用 ConfirmDialog）；当前会话用
 * nav-item active token 高亮。
 *
 * 两种用法（照 jiuwenswarm `ConversationSidebar` 的 `__group-list` 语义）：
 * - **整体**：不传 `sessions`，用 store 全量（搜索态跨全部会话的扁平列表）；
 * - **分组**：传一组会话 + `indent`，作为某个工作区展开后的嵌套列表，
 *   此时不做「展开其余 N 个」折叠（分组内会话本就不多）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Session } from '@/types'
import { useRouterStore } from '@/routing/router'
import { useSessionsStore } from '@/stores/sessions'
import ConfirmDialog from './ConfirmDialog'
import { toast } from '@/stores/toasts'
import { formatRelativeTime } from '@/utils/format'
import { ArchiveIcon, DeleteIcon, EditIcon, MoreIcon } from './icons'

/** 默认显示的最近会话数（超出折叠为"展开其余 N 个会话"，DSH 式）。 */
const COLLAPSE_AFTER = 8

interface SessionItemProps {
  /** 会话文档。 */
  session: Session
  /** 是否当前选中。 */
  active: boolean
  /** 嵌套模式：左内边距 32px（工作区展开后的子项，照 jiuwen nested）。 */
  indent?: boolean
  /** 选中会话（抽屉模式同时关闭抽屉）。 */
  onSelect: () => void
}

/** 单条会话项：主体按钮 + hover "⋯" 操作菜单（重命名/归档/删除）。 */
function SessionItem({ session, active, indent = false, onSelect }: SessionItemProps) {
  const rename = useSessionsStore((s) => s.rename)
  const archive = useSessionsStore((s) => s.archive)
  const remove = useSessionsStore((s) => s.remove)
  const [menuOpen, setMenuOpen] = useState(false)
  /** 删除确认弹窗（与工作区删共用 ConfirmDialog，照 jiuwen 的一个 DeleteDialog）。 */
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(session.title)
  const menuRef = useRef<HTMLDivElement>(null)

  // 菜单外点击关闭
  useEffect(() => {
    if (!menuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
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
    try {
      await archive(session._id, !session.archived)
      toast('success', session.archived ? '已取消归档' : '已归档')
    } catch (err) {
      toast('error', `归档失败：${(err as Error).message}`)
    }
  }

  /** 删除（在确认弹窗里执行）。 */
  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError(null)
    try {
      // 删的是当前会话时，删完回 /chat/new 草稿态（URL 不再指向已删会话）
      const wasCurrent = useSessionsStore.getState().currentId === session._id
      await remove(session._id)
      if (wasCurrent) useRouterStore.getState().navigate({ kind: 'chat-new' })
      setConfirmOpen(false)
      toast('success', '会话已删除')
    } catch (err) {
      // 失败不关弹窗，便于用户看完原因重试
      setDeleteError((err as Error).message)
    } finally {
      setDeleting(false)
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
          className={`w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-button-ghost-active-border)] bg-[var(--sa-specific-input-major)] py-1.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none ${
            indent ? 'pl-8 pr-2.5' : 'px-2.5'
          }`}
        />
      ) : (
        <button
          type="button"
          onClick={onSelect}
          title={session.title}
          className={`relative flex w-full items-center rounded-[var(--sa-radius-sm)] py-[7px] text-left transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)] ${
            indent ? 'pl-8 pr-2.5' : 'px-2.5'
          } ${
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
          <span className="min-w-0 flex-1 truncate pr-2 text-[13px] text-[var(--sa-alias-label-primary)]">
            {session.title || '新对话'}
          </span>
          {/* 时间：悬停 / 键盘聚焦时淡出，把位置让给右侧「⋯」（两者交叉淡入淡出） */}
          <span className="shrink-0 text-[11px] text-[var(--sa-alias-label-caption)] transition-opacity duration-[var(--sa-duration-fast)] group-hover:opacity-0 group-focus-within:opacity-0">
            {formatRelativeTime(session.updated_at)}
          </span>
        </button>
      )}

      {/* hover 操作入口（重命名态隐藏）；与时间同位，靠上面那行的淡出腾出位置 */}
      {!editing && (
        <button
          type="button"
          aria-label={`会话操作：${session.title || '新对话'}`}
          onClick={() => setMenuOpen((v) => !v)}
          className={`absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-opacity duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] ${
            menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
          }`}
        >
          <MoreIcon className="h-4 w-4" />
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
            className="flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <EditIcon className="h-3.5 w-3.5 shrink-0" />
            重命名
          </button>
          <button
            type="button"
            onClick={() => void toggleArchive()}
            className="flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <ArchiveIcon className="h-3.5 w-3.5 shrink-0" />
            {session.archived ? '取消归档' : '归档'}
          </button>
          <button
            type="button"
            onClick={() => {
              setMenuOpen(false)
              setDeleteError(null)
              setConfirmOpen(true)
            }}
            className="flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] text-[var(--sa-alias-state-error-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-danger)]"
          >
            <DeleteIcon className="h-3.5 w-3.5 shrink-0" />
            删除
          </button>
        </div>
      )}

      {/* 删除确认弹窗（与工作区删共用，照 jiuwen 的 DeleteDialog） */}
      {confirmOpen && (
        <ConfirmDialog
          title="删除会话"
          description={
            <>
              确定要删除「
              <span className="inline-block max-w-[70%] truncate align-bottom text-[var(--sa-alias-label-primary)]">
                {session.title || '新对话'}
              </span>
              」吗？该会话的消息记录将一并删除，且无法恢复。
            </>
          }
          confirmLabel="删除"
          busy={deleting}
          error={deleteError}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void handleDelete()}
        />
      )}
    </div>
  )
}

interface SessionListProps {
  /** 搜索关键词（按标题过滤，空串不过滤）。 */
  query?: string
  /** 指定一组会话（缺省用 store 全量，配合搜索实现跨全部会话）。 */
  sessions?: Session[]
  /** 嵌套模式：作为工作区展开后的子列表（缩进 + 不折叠超出项）。 */
  indent?: boolean
  /** 空态文案（缺省「暂无会话」）。 */
  emptyText?: string
  /** 抽屉模式下选中会话后关闭抽屉。 */
  onNavigate?: () => void
}

/** 会话列表组件（左栏主体，DSH 式：平铺 + 折叠超出）。 */
export default function SessionList({
  query = '',
  sessions,
  indent = false,
  emptyText = '暂无会话',
  onNavigate,
}: SessionListProps) {
  const storeSessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [restOpen, setRestOpen] = useState(false)

  const source = sessions ?? storeSessions

  // 标题过滤 + updated_at 倒序（后端有序，patch 后本地重排兜底）
  const { active, archived } = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? source.filter((s) => s.title.toLowerCase().includes(q))
      : source
    const sorted = [...filtered].sort((a, b) => b.updated_at - a.updated_at)
    return {
      active: sorted.filter((s) => !s.archived),
      archived: sorted.filter((s) => s.archived),
    }
  }, [source, query])

  // DSH 式折叠：非搜索 / 非嵌套态默认只显示最近 COLLAPSE_AFTER 条。
  // canCollapse 决定展开后是否还该给出「收起」——若期间会话被删到阈值以内，
  // 已无被折叠的项，就不该再挂一个收不起东西的按钮。
  const searching = query.trim().length > 0
  const collapsible = !searching && !indent && active.length > COLLAPSE_AFTER
  const visible = searching || restOpen || indent ? active : active.slice(0, COLLAPSE_AFTER)
  const restCount = active.length - visible.length

  // 搜索态自动展开归档组，便于全库检索
  const showArchived = archivedOpen || searching

  // 空态左对齐（照 jiuwen `__empty`）；嵌套态与分组内会话左缘对齐
  const emptyClass = `py-3 text-xs text-[var(--sa-alias-label-caption)] ${indent ? 'pl-8 pr-2.5' : 'px-2.5'}`

  if (source.length === 0) {
    return <div className={emptyClass}>{emptyText}</div>
  }

  return (
    <div className="flex min-h-0 flex-col gap-0.5">
      {active.length === 0 && archived.length === 0 && <div className={emptyClass}>无匹配会话</div>}
      {visible.map((s) => (
        <SessionItem
          key={s._id}
          session={s}
          active={s._id === currentId}
          indent={indent}
          onSelect={() => {
            // 选中会话 = 导航到 /chat/<id>（URL 是唯一事实源，AppShell 同步 store）
            useRouterStore.getState().navigate({ kind: 'chat-session', sessionId: s._id })
            onNavigate?.()
          }}
        />
      ))}

      {/* 展开其余会话 / 收起（DSH 式文字链接，二态互斥） */}
      {collapsible && restOpen ? (
        <button
          type="button"
          aria-expanded
          onClick={() => setRestOpen(false)}
          className="px-2.5 py-1.5 text-left text-[12px] text-[var(--sa-alias-label-caption)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-secondary)]"
        >
          收起
        </button>
      ) : restCount > 0 ? (
        <button
          type="button"
          aria-expanded={false}
          onClick={() => setRestOpen(true)}
          className="px-2.5 py-1.5 text-left text-[12px] text-[var(--sa-alias-label-caption)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-secondary)]"
        >
          展开其余 {restCount} 个会话
        </button>
      ) : null}

      {/* 归档折叠组 */}
      {archived.length > 0 && (
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setArchivedOpen((v) => !v)}
            aria-expanded={showArchived}
            className={`flex w-full items-center gap-1 rounded-[var(--sa-radius-sm)] py-1 text-xs text-[var(--sa-alias-label-caption)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)] ${
              indent ? 'pl-8 pr-2' : 'px-2'
            }`}
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
                indent={indent}
                onSelect={() => {
                  useRouterStore.getState().navigate({ kind: 'chat-session', sessionId: s._id })
                  onNavigate?.()
                }}
              />
            ))}
        </div>
      )}
    </div>
  )
}
