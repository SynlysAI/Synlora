/**
 * 左栏：「+ 新会话」主按钮 → 会话搜索框 → 「工作区 / 会话」两级分组列表。
 *
 * 分组层级照抄 jiuwenswarm `multi-session/sidebar/ConversationSidebar.tsx`
 * 1363-1461 行（`__body` 内两个 `conversation-sidebar__group`）：
 * - 「工作区」组只列**非默认**工作区（默认工作区由「会话」组代表，对应参考项目
 *   的 `regularProjects` vs `conversationSessions`，见 948-960 行）；
 * - 「会话」组 = 默认工作区下的会话，标题右侧「+」新建会话（1434-1459 行）；
 * - 分组标题的「+」默认隐藏、标题 hover 才显现（`__section-action`）。
 *
 * 会话归属纯前端推导（后端 `GET /api/v1/sessions` 已带 `project_id`）：
 * `project_id` 指向某工作区即归入该组；为空（C5 之前的旧会话）或指向已不存在的
 * 工作区时归入默认工作区。搜索框有内容时退化为跨全部会话的扁平列表。
 *
 * 本轮去掉助手选择区：专家改为可选（未选 = 平台默认提示词），选择入口收敛到
 * 输入框的「+ → 专家」。
 */
import { useMemo, useState } from 'react'
import type { Session } from '@/types'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { DEFAULT_PROJECT_DIR, useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
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
  const create = useSessionsStore((s) => s.create)
  const projects = useProjectsStore((s) => s.projects)
  const projectsLoaded = useProjectsStore((s) => s.loaded)
  /** 当前选中的工作区（null = 未选 → 新会话由后端回落默认工作区）。 */
  const currentProjectId = useProjectsStore((s) => s.currentId)
  const createProject = useProjectsStore((s) => s.create)
  const [query, setQuery] = useState('')
  /** 工作区展开态（纯 UI 状态，不落 store）。 */
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  // 默认工作区不单独成行（由「会话」组代表），其余全部列在「工作区」组
  const defaultProject = projects.find((p) => p.dir_name === DEFAULT_PROJECT_DIR) ?? null
  const workspaces = useMemo(
    () => projects.filter((p) => p._id !== defaultProject?._id),
    [projects, defaultProject],
  )

  /** 按 project_id 把会话归入各工作区；无归属的（旧会话/工作区已删）归默认工作区。 */
  const { sessionsByProject, defaultSessions } = useMemo(() => {
    const validIds = new Set(projects.map((p) => p._id))
    const byProject: Record<string, Session[]> = {}
    const orphans: Session[] = []
    const defaultId = defaultProject?._id ?? null
    for (const session of sessions) {
      const pid = session.project_id
      if (!pid || pid === defaultId || !validIds.has(pid)) orphans.push(session)
      else (byProject[pid] ??= []).push(session)
    }
    return { sessionsByProject: byProject, defaultSessions: orphans }
  }, [sessions, projects, defaultProject])

  const searching = query.trim().length > 0

  /**
   * 新建会话：用「新对话默认专家」（可能为空 = 不使用专家）建到**当前选中的
   * 工作区**（输入框工作区行里选的，见 WorkspacePicker）；未选则不传 project_id，
   * 由后端回落到默认工作区。主按钮与「会话」组 `+` 共走此路径。
   */
  const handleNew = async () => {
    const assistant = pickSelectedAssistant(useAssistantsStore.getState())
    try {
      await create(assistant?._id ?? null, { projectId: currentProjectId ?? undefined })
      onNavigate?.()
    } catch (err) {
      toast('error', `新建会话失败：${(err as Error).message}`)
    }
  }

  /** 新建工作区：prompt 取名字（与 WorkspacePicker 同做法），成功后展开它。 */
  const handleNewWorkspace = async () => {
    const name = window.prompt('新建工作区名称')?.trim()
    if (!name) return
    try {
      const project = await createProject(name)
      setExpanded((m) => ({ ...m, [project._id]: true }))
      toast('success', `已创建工作区 ${project.name}`)
    } catch (err) {
      toast('error', `新建工作区失败：${(err as Error).message}`)
    }
  }

  /** 切换工作区展开态（点工作区行 = 展开/收起，照 jiuwen ProjectEntityRow）。 */
  const toggleWorkspace = (projectId: string) =>
    setExpanded((m) => ({ ...m, [projectId]: !m[projectId] }))

  return (
    <div className="flex h-full flex-col gap-2 p-2.5">
      {/* 新会话主按钮（DSH 式：描边低调按钮） */}
      <button
        type="button"
        onClick={() => void handleNew()}
        className="flex shrink-0 items-center justify-center gap-1.5 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-[7px] text-[13px] font-medium text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-base)] hover:border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
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
          className="w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] py-1.5 pl-7.5 pr-2 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
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
              onAction={() => void handleNewWorkspace()}
            />
            <WorkspaceGroup
              projects={workspaces}
              loaded={projectsLoaded}
              sessionsByProject={sessionsByProject}
              expanded={expanded}
              onToggle={toggleWorkspace}
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
    </div>
  )
}
