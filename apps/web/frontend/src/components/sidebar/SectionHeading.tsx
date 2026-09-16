/**
 * 侧栏分组标题行（「工作区」/「会话」）。
 *
 * 结构与交互照抄 jiuwenswarm `multi-session/sidebar/ConversationSidebar.tsx`
 * 1392-1411 行（`__section-heading` + `__section-actions` + `__section-action`）：
 * 左侧为 12px 次级色标签，右侧「+」默认 `opacity:0` + `pointer-events:none`，
 * 仅在标题行 hover / 键盘聚焦时才显现（对应 `ConversationSidebar.css`
 * 291-357 行）。参考项目的「+」依赖 `:has([aria-expanded='true'])` 保持常显，
 * 本项目的「+」是即时动作（无展开面板），故只在 hover / focus 时显现。
 */
import { PlusIcon } from './icons'

interface SectionHeadingProps {
  /** 分组标题文案。 */
  label: string
  /** 右侧「+」的无障碍标签与 tooltip 文案。 */
  actionLabel: string
  /** 点击「+」触发的动作。 */
  onAction: () => void
  /** 追加在标题行上的布局类（如分组间距）。 */
  className?: string
}

/** 分组标题行组件。 */
export default function SectionHeading({ label, actionLabel, onAction, className = '' }: SectionHeadingProps) {
  return (
    <div className={`group/section flex h-8 shrink-0 items-center justify-between px-2 ${className}`}>
      <span className="truncate text-sm leading-none text-[var(--sa-alias-label-caption)]">{label}</span>
      <button
        type="button"
        onClick={onAction}
        aria-label={actionLabel}
        title={actionLabel}
        className="pointer-events-none flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-caption)] opacity-0 transition-opacity duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] focus-visible:pointer-events-auto focus-visible:opacity-100 group-hover/section:pointer-events-auto group-hover/section:opacity-100"
      >
        <PlusIcon className="h-4 w-4" />
      </button>
    </div>
  )
}
