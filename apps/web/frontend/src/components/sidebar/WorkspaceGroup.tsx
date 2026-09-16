/**
 * 侧栏「工作区」组：列出**非默认**工作区，逐行可展开查看该工作区下的会话。
 *
 * 结构与样式照抄 jiuwenswarm `multi-session/sidebar/ConversationSidebar.tsx`
 * 343-513 行的 `ProjectEntityRow` 与 1226-1274 行的 `renderProject`
 * （`__group` → 行 → `__group-list` / `__empty`）：
 * - 行内三段：`[文件夹图标] [名称] [展开箭头]` —— 箭头**紧跟名称**，不靠右
 *   （jiuwen 如此；靠右时会与右侧操作按钮争位置，短名字下还会显得悬空）；
 * - 右侧两个操作按钮 `⋯` / `+` 绝对定位在行右缘、**悬停或键盘聚焦时才显现**
 *   （`__plus` 的 opacity 0 → 行 hover 时 1），故主按钮留 `pr-14` 给它们；
 * - 展开后的子列表缩进 32px、空态文案「暂无会话」。
 *
 * `⋯` 菜单：重命名 / 删除（jiuwen 还有「置顶」，本项目无置顶概念故不搬）。
 * 删除**不级联删会话**——与 jiuwen 一致（其文案：「项目下未置顶会话会临时归入
 * 默认项目」），落单的会话由 Sidebar 归入默认工作区。默认工作区不在此渲染。
 */
import { useEffect, useRef, useState } from 'react'
import type { Project, Session } from '@/types'
import { useProjectsStore } from '@/stores/projects'
import { toast } from '@/stores/toasts'
import ConfirmDialog from './ConfirmDialog'
import InputDialog from './InputDialog'
import SessionList from './SessionList'
import {
  ArrowRightIcon,
  CollapseIcon,
  DeleteIcon,
  EditIcon,
  FolderFoldIcon,
  FolderIcon,
  MoreIcon,
  PlusIcon,
} from './icons'

interface WorkspaceRowProps {
  /** 该行的工作区。 */
  project: Project
  /** 是否展开。 */
  expanded: boolean
  /** 该工作区下的会话。 */
  sessions: Session[]
  /** 切换展开态。 */
  onToggle: () => void
  /** 在该工作区内新建会话。 */
  onNewSession: () => void
  /** 抽屉模式下选中会话后关闭抽屉。 */
  onNavigate?: () => void
}

/** 单个工作区行：主按钮（图标/名称/箭头）+ 悬停显现的「⋯」「+」。 */
function WorkspaceRow({
  project,
  expanded,
  sessions,
  onToggle,
  onNewSession,
  onNavigate,
}: WorkspaceRowProps) {
  const rename = useProjectsStore((s) => s.rename)
  const remove = useProjectsStore((s) => s.remove)
  const [menuOpen, setMenuOpen] = useState(false)
  /** 删除确认弹窗是否打开（照 jiuwen：弹窗确认，不是菜单内二次点击）。 */
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
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

  /** 重命名弹窗（InputDialog 预填现名，样式同删除确认弹窗）。 */
  const [renameOpen, setRenameOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)

  /** 重命名确认：后端同步改磁盘目录名；失败不关弹窗便于重试。 */
  const handleRename = async (name: string) => {
    if (name === project.name) {
      setRenameOpen(false)
      return
    }
    setRenaming(true)
    setRenameError(null)
    try {
      const updated = await rename(project._id, name)
      setRenameOpen(false)
      toast('success', `已重命名为 ${updated.name}`)
    } catch (err) {
      setRenameError((err as Error).message)
    } finally {
      setRenaming(false)
    }
  }

  /** 删除（确认弹窗里执行）；其下会话不删，由 Sidebar 归入默认工作区。 */
  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError(null)
    try {
      await remove(project._id)
      setConfirmOpen(false)
      toast('success', `已删除工作区 ${project.name}`)
    } catch (err) {
      // 失败不关弹窗，便于用户看完原因重试
      setDeleteError((err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="group/ws flex flex-col">
      <div className="relative">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          title={project.name}
          className="flex h-8.5 w-full items-center gap-1.5 rounded-[var(--sa-radius-md)] px-2 pr-14 text-left text-sm text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]"
        >
          {expanded ? (
            <FolderFoldIcon className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <FolderIcon className="h-3.5 w-3.5 shrink-0" />
          )}
          <span className="min-w-0 truncate">{project.name}</span>
          <span className="shrink-0">
            {expanded ? (
              <CollapseIcon className="h-3.5 w-3.5 text-[var(--sa-alias-label-tertiary)]" />
            ) : (
              <ArrowRightIcon className="h-3.5 w-3.5 text-[var(--sa-alias-label-tertiary)]" />
            )}
          </span>
        </button>

        {/* 「⋯」「+」：悬停或键盘聚焦时显现（透明时也禁用指针，避免点到看不见的按钮）。
            与主按钮必须是兄弟节点——HTML 不允许按钮嵌套。 */}
        <div className="pointer-events-none absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity duration-[var(--sa-duration-fast)] focus-within:pointer-events-auto focus-within:opacity-100 group-hover/ws:pointer-events-auto group-hover/ws:opacity-100">
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={`工作区操作：${project.name}`}
            title="更多操作"
            onClick={() => setMenuOpen((v) => !v)}
            className="flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)]"
          >
            <MoreIcon className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onNewSession}
            title={`在「${project.name}」中新建会话`}
            aria-label={`在「${project.name}」中新建会话`}
            className="flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)]"
          >
            <PlusIcon className="h-4 w-4" />
          </button>
        </div>

        {/* 「⋯」菜单：重命名 / 删除（删除为两步内联确认，照 SessionList） */}
        {menuOpen && (
          <div
            ref={menuRef}
            role="menu"
            className="absolute right-1 top-8 z-30 w-32 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false)
                setRenameError(null)
                setRenameOpen(true)
              }}
              className="flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-sm text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
            >
              <EditIcon className="h-3.5 w-3.5 shrink-0" />
              重命名
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false)
                setDeleteError(null)
                setConfirmOpen(true)
              }}
              className="flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-sm text-[var(--sa-alias-state-error-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-danger)]"
            >
              <DeleteIcon className="h-3.5 w-3.5 shrink-0" />
              删除
            </button>
          </div>
        )}
      </div>

      {/* 重命名弹窗（预填现名，打开时全选便于直接覆盖） */}
      {renameOpen && (
        <InputDialog
          title="重命名工作区"
          initialValue={project.name}
          confirmLabel="重命名"
          busy={renaming}
          error={renameError}
          onCancel={() => setRenameOpen(false)}
          onConfirm={(name) => void handleRename(name)}
        />
      )}

      {/* 删除确认弹窗（照 jiuwen DeleteDialog：遮罩 + 面板 + 取消/危险确认） */}
      {confirmOpen && (
        <ConfirmDialog
          title="删除工作区"
          description={
            <>
              确定要移除「
              <span className="inline-block max-w-[70%] truncate align-bottom text-[var(--sa-alias-label-primary)]">
                {project.name}
              </span>
              」吗？其下的会话不会被删除，会临时归入默认工作区。
            </>
          }
          confirmLabel="删除"
          busy={deleting}
          error={deleteError}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void handleDelete()}
        />
      )}

      {expanded && (
        <SessionList
          sessions={sessions}
          indent
          emptyText="暂无会话"
          onNavigate={onNavigate}
        />
      )}
    </div>
  )
}

interface WorkspaceGroupProps {
  /** 非默认工作区列表（展示序与 projects store 一致）。 */
  projects: Project[]
  /** projects store 的 load() 是否完成（区分空态与加载中）。 */
  loaded: boolean
  /** 工作区 id → 该工作区下的会话（已在 Sidebar 按 project_id 归组）。 */
  sessionsByProject: Record<string, Session[]>
  /** 工作区 id → 是否展开。 */
  expanded: Record<string, boolean>
  /** 切换某工作区的展开态。 */
  onToggle: (projectId: string) => void
  /** 在指定工作区内新建会话（行右侧「+」）。 */
  onNewSession: (projectId: string) => void
  /** 抽屉模式下选中会话后关闭抽屉。 */
  onNavigate?: () => void
}

/** 工作区组组件（左栏分组列表中的一组）。 */
export default function WorkspaceGroup({
  projects,
  loaded,
  sessionsByProject,
  expanded,
  onToggle,
  onNewSession,
  onNavigate,
}: WorkspaceGroupProps) {
  if (projects.length === 0) {
    return (
      <div className="px-2.5 py-3 text-xs text-[var(--sa-alias-label-caption)]">
        {loaded ? '暂无工作区' : '加载中…'}
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      {projects.map((project) => (
        <WorkspaceRow
          key={project._id}
          project={project}
          expanded={expanded[project._id] ?? false}
          sessions={sessionsByProject[project._id] ?? []}
          onToggle={() => onToggle(project._id)}
          onNewSession={() => onNewSession(project._id)}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  )
}
