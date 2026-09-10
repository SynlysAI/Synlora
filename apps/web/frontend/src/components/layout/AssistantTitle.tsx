/**
 * 顶栏中部：当前助手名（sessions + assistants 联查）与 "+ 新对话" 按钮。
 * 新建会话统一用左栏选择的新对话默认助手（无显式选择回退第一个）。
 */
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { useSessionsStore } from '@/stores/sessions'

/** 顶栏中部组件（助手名 + 新对话入口）。 */
export function AssistantTitle() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const create = useSessionsStore((s) => s.create)
  const assistants = useAssistantsStore((s) => s.assistants)

  const session = sessions.find((s) => s._id === currentId)
  const name = assistants.find((a) => a._id === session?.assistant_id)?.name

  /** 新建会话：用左栏选择的新对话默认助手。 */
  const handleNew = () => {
    const assistant = pickSelectedAssistant(useAssistantsStore.getState())
    if (assistant) void create(assistant._id)
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
