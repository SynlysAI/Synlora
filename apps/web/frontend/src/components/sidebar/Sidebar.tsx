/**
 * 左栏：「+ 新会话」主按钮 → 会话搜索框 → 「工作区 / 会话」两级分组列表。
 *
 * 分组层级照抄 jiuwenswarm `multi-session/sidebar/ConversationSidebar.tsx`
 * 1363-1461 行（`__body` 内两个 `conversation-sidebar__group`）：
 * - 「工作区」组列出全部工作区（对应参考项目的 `regularProjects`）；
 * - 「会话」组 = 未绑定工作区的会话（照 jiuwen 不选项目的普通会话列表，
 *   会话目录自身即工作区），标题右侧「+」新建会话（1434-1459 行）；
 * - 分组标题的「+」默认隐藏、标题 hover 才显现（`__section-action`）。
 *
 * 会话归属纯前端推导（后端 `GET /api/v1/sessions` 已带 `project_id`）：
 * `project_id` 指向某工作区即归入该组；为空（不选工作区的新会话）或指向已不
 * 存在的工作区（后端发消息时已清为 null）归入「会话」未分组区。
 * 搜索框有内容时退化为跨全部会话的扁平列表。
 */
import { useMemo, useState } from 'react'
import type { Session } from '@/types'
import { useRouterStore } from '@/routing/router'
import { useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
import InputDialog from './InputDialog'
import SectionHeading from './SectionHeading'
import SessionList from './SessionList'
import WorkspaceGroup from './WorkspaceGroup'

interface SidebarProps {
  /** 抽屉模式下点击导航项后关闭抽屉。 */
  onNavigate?: () => void
}

/** 左栏组件（AppShell 左侧列 / compact 抽屉）。 */
export default function Sidebar({ onNavigate }: SidebarProps) {
  const sessions = useSessionsStore((s) => s.sessions)
  const projects = useProjectsStore((s) => s.projects)
  const projectsLoaded = useProjectsStore((s) => s.loaded)
  /** 切换「新会话目标工作区」（null = 不使用工作区，会话目录即工作区）。 */
  const setCurrentProject = useProjectsStore((s) => s.setCurrent)
  const createProject = useProjectsStore((s) => s.create)
  const [query, setQuery] = useState('')
  /** 工作区展开态（纯 UI 状态，不落 store）。 */
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const workspaces = useMemo(() => projects, [projects])

  /** 按 project_id 把会话归入各工作区；无归属的（不选工作区/工作区已删）归「会话」组。 */
  const { sessionsByProject, defaultSessions } = useMemo(() => {
    const validIds = new Set(projects.map((p) => p._id))
    const byProject: Record<string, Session[]> = {}
    const orphans: Session[] = []
    for (const session of sessions) {
      const pid = session.project_id
      if (!pid || !validIds.has(pid)) orphans.push(session)
      else (byProject[pid] ??= []).push(session)
    }
    return { sessionsByProject: byProject, defaultSessions: orphans }
  }, [sessions, projects])

  const searching = query.trim().length > 0

  /**
   * 新会话：**只进草稿态，不落库**——首次真正发送时才由 chat store 建会话，
   * 否则点一次就留一条空会话。
   *
   * 同时把「新会话目标工作区」（输入框下方 WorkspacePicker 显示的就是它）切准：
   * - 工作区行「+」传了 projectId → 切到**那个**工作区；
   * - 顶部「新会话」不传 → 切回**不使用工作区**（会话目录即工作区，
   *   而不是沿用上次的选择，否则从别的会话点新会话，输入框下方还停在上个工作区）。
   */
  const handleNew = (projectId?: string) => {
    setCurrentProject(projectId ?? null)
    // 导航到 /chat/new 草稿态（AppShell 据此把 currentId 置 null 并重置聊天态）
    useRouterStore.getState().navigate({ kind: 'chat-new' })
    onNavigate?.()
  }

  /** 新建工作区弹窗（InputDialog，照删除确认弹窗同款样式）；失败不关弹窗便于重试。 */
  const [wsDialogOpen, setWsDialogOpen] = useState(false)
  const [creatingWs, setCreatingWs] = useState(false)
  const [createWsError, setCreateWsError] = useState<string | null>(null)

  /** 新建工作区确认：创建后展开它（重名由 store/后端报行内错误）。 */
  const handleCreateWorkspace = async (name: string) => {
    setCreatingWs(true)
    setCreateWsError(null)
    try {
      const project = await createProject(name)
      setWsDialogOpen(false)
      setExpanded((m) => ({ ...m, [project._id]: true }))
      toast('success', `已创建工作区 ${project.name}`)
    } catch (err) {
      setCreateWsError((err as Error).message)
    } finally {
      setCreatingWs(false)
    }
  }

  /** 切换工作区展开态（点工作区行 = 展开/收起，照 jiuwen ProjectEntityRow）。 */
  const toggleWorkspace = (projectId: string) =>
    setExpanded((m) => ({ ...m, [projectId]: !m[projectId] }))

  return (
    <div className="flex h-full flex-col gap-2 px-3 py-1.5">
      {/* 新会话主按钮（DSH 式：描边低调按钮） */}
      <button
        type="button"
        onClick={() => void handleNew()}
        className="flex h-9.5 shrink-0 items-center justify-center gap-1.5 rounded-[12px] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-4 text-sm font-medium text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-base)] hover:border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="M8 3v10M3 8h10" />
        </svg>
        新会话
      </button>

      {/* 会话搜索 */}
      <div className="relative shrink-0">
        <svg
          width="13"
          height="13"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--sa-alias-label-caption)]"
          aria-hidden="true"
        >
          <circle cx="7" cy="7" r="4.5" />
          <path d="m10.5 10.5 3 3" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索会话"
          aria-label="搜索会话"
          className="h-7.5 w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] pl-7.5 pr-2 text-sm text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
        />
      </div>

      {/* 分组滚动区：搜索态退化为跨全部会话的扁平列表 */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-1">
        {searching ? (
          <SessionList query={query} onNavigate={onNavigate} />
        ) : (
          <>
            <SectionHeading
              label="工作区"
              actionLabel="新建工作区"
              onAction={() => {
                setCreateWsError(null)
                setWsDialogOpen(true)
              }}
            />
            <WorkspaceGroup
              projects={workspaces}
              loaded={projectsLoaded}
              sessionsByProject={sessionsByProject}
              expanded={expanded}
              onToggle={toggleWorkspace}
              onNewSession={(projectId) => void handleNew(projectId)}
              onNavigate={onNavigate}
            />

            <SectionHeading
              label="会话"
              actionLabel="新建会话"
              onAction={() => void handleNew()}
              className="mt-4"
            />
            <SessionList sessions={defaultSessions} onNavigate={onNavigate} />
          </>
        )}
      </div>

      {/* 新建工作区弹窗（样式同删除确认弹窗） */}
      {wsDialogOpen && (
        <InputDialog
          title="新建工作区"
          placeholder="输入工作区名称"
          confirmLabel="创建"
          busy={creatingWs}
          error={createWsError}
          onCancel={() => setWsDialogOpen(false)}
          onConfirm={(name) => void handleCreateWorkspace(name)}
        />
      )}
    </div>
  )
}
