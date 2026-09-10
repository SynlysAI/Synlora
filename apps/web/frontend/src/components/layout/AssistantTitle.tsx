/**
 * 顶栏中部：当前助手名（sessions + assistants 联查）与 "+ 新对话" 按钮。
 * 新会话沿用当前会话的助手，无会话时取第一个助手。
 */
import { useAssistantsStore } from '@/stores/assistants'
import { useSessionsStore } from '@/stores/sessions'

/** 顶栏中部组件（助手名 + 新对话入口）。 */
export function AssistantTitle() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const create = useSessionsStore((s) => s.create)
  const assistants = useAssistantsStore((s) => s.assistants)

  const session = sessions.find((s) => s._id === currentId)
  const name = assistants.find((a) => a._id === session?.assistant_id)?.name

  /** 新建会话：优先沿用当前助手。 */
  const handleNew = () => {
    const assistantId = session?.assistant_id ?? assistants[0]?._id
    if (assistantId) void create(assistantId)
  }

  return (
    <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
      <button
        type="button"
        onClick={handleNew}
        disabled={!assistants.length}
        className="shrink-0 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-2 py-1 text-xs text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        + 新对话
      </button>
      <span className="min-w-0 truncate text-center text-[13px] text-[var(--sa-alias-label-tertiary)]">
        {name ? `当前助手：${name}` : 'SynlysAgent'}
      </span>
    </div>
  )
}
