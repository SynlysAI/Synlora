/**
 * 侧栏「工作区」组：列出**非默认**工作区，逐行可展开查看该工作区下的会话。
 *
 * 结构与样式照抄 jiuwenswarm `multi-session/sidebar/ConversationSidebar.tsx`
 * 343-513 行的 `ProjectEntityRow`（文件夹图标 + 名称 + 展开/收起箭头）与
 * 1226-1274 行的 `renderProject`（`__group` → 行 → `__group-list` / `__empty`）：
 * - 图标随展开态切换 `FolderFoldIcon` / `FolderIcon`（440 行）；
 * - 箭头随展开态切换 `CollapseIcon` / `ArrowRightIcon`（452 行）；
 * - 展开后的子列表缩进 32px、空态文案「暂无会话」
 *   （`ConversationSidebar.css` 601-603 行 nested 内边距 + `__empty`）。
 *
 * 与参考项目的差异（本轮范围外，有意省略）：行内不做「置顶 / 更多操作」菜单。
 * 点行即展开/收起（同 jiuwen `ProjectEntityRow`）。默认工作区不在此渲染
 * （由「会话」组代表）。
 */
import type { Project, Session } from '@/types'
import SessionList from './SessionList'
import { ArrowRightIcon, CollapseIcon, FolderFoldIcon, FolderIcon, PlusIcon } from './icons'

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
  /** 在指定工作区内新建会话（行右侧「+」，照 jiuwen `__plus`）。 */
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
      {projects.map((project) => {
        const isExpanded = expanded[project._id] ?? false
        return (
          <div key={project._id} className="group/ws flex flex-col">
            {/* 工作区行：文件夹图标 + 名称 + 展开箭头，点行展开/收起。
                「+」绝对定位在行右侧、默认透明，hover 或键盘聚焦时**原地替代**箭头出现
                （照 jiuwen `.conversation-entity-row__plus`：opacity 0 → 行 hover 时 1）。
                两者位置重叠、交叉淡入淡出，所以**不给「+」预留右内边距**——预留会把
                箭头从行右缘往里推 36px，短名字时看着像悬在半空。
                两枚按钮必须是兄弟节点——不能把「+」嵌进行按钮里（HTML 不允许按钮嵌套）。 */}
            <div className="relative">
              <button
                type="button"
                onClick={() => onToggle(project._id)}
                aria-expanded={isExpanded}
                title={project.name}
                className="flex min-h-9 w-full items-center gap-2 rounded-[var(--sa-radius-md)] px-2 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]"
              >
                {isExpanded ? (
                  <FolderFoldIcon className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <FolderIcon className="h-3.5 w-3.5 shrink-0" />
                )}
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                <span className="shrink-0 transition-opacity duration-[var(--sa-duration-fast)] group-hover/ws:opacity-0">
                  {isExpanded ? (
                    <CollapseIcon className="h-3.5 w-3.5 text-[var(--sa-alias-label-tertiary)]" />
                  ) : (
                    <ArrowRightIcon className="h-3.5 w-3.5 text-[var(--sa-alias-label-tertiary)]" />
                  )}
                </span>
              </button>
              <button
                type="button"
                onClick={() => onNewSession(project._id)}
                title={`在「${project.name}」中新建会话`}
                aria-label={`在「${project.name}」中新建会话`}
                className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] opacity-0 transition-opacity duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] focus-visible:opacity-100 group-hover/ws:opacity-100"
              >
                <PlusIcon className="h-4 w-4" />
              </button>
            </div>

            {/* 展开后：该工作区下的会话（缩进 32px），空则「暂无会话」 */}
            {isExpanded && (
              <SessionList
                sessions={sessionsByProject[project._id] ?? []}
                indent
                emptyText="暂无会话"
                onNavigate={onNavigate}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
