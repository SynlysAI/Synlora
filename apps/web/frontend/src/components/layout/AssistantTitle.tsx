/**
 * 顶栏中部：当前助手名（sessions + assistants 联查）。
 * 新建会话入口统一在左栏（DSH 式），顶栏不再重复。
 */
import { useAssistantsStore } from '@/stores/assistants'
import { useSessionsStore } from '@/stores/sessions'

/** 顶栏中部组件（当前助手名）。 */
export function AssistantTitle() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const assistants = useAssistantsStore((s) => s.assistants)

  const session = sessions.find((s) => s._id === currentId)
  const assistant = assistants.find((a) => a._id === session?.assistant_id)

  return (
    <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
      {assistant && (
        <span aria-hidden="true" className="text-[14px] leading-none">
          {assistant.avatar?.trim() || assistant.name.slice(0, 1)}
        </span>
      )}
      <span className="min-w-0 truncate text-center text-[13px] font-medium text-[var(--sa-alias-label-secondary)]">
        {assistant?.name ?? 'SynlysAgent'}
      </span>
    </div>
  )
}
