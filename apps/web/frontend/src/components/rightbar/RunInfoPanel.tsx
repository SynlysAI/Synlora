/**
 * 运行信息面板（右栏"运行信息"页）：当前会话的事件流统计。
 *
 * 数据来自 chat store 的 RunStats 聚合（历史回放与 SSE 流共用同一
 * reducer，实时更新）：轮数 = turn/end + turn/aborted；token 用量
 * 后端 V1 未进事件流，展示步数/事件计数/运行状态。
 */
import { useAssistantsStore } from '@/stores/assistants'
import { useChatStore } from '@/stores/chat'
import { useSessionsStore } from '@/stores/sessions'
import { TOOL_LABELS } from '@/components/chat/toolLabels'
import { formatRelativeTime } from '@/utils/format'

/** 统计单元格：数值 + 说明。 */
function StatCell({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] px-2.5 py-2">
      <div className="text-[15px] font-medium leading-tight text-[var(--sa-alias-label-primary)]">
        {value}
      </div>
      <div className="pt-0.5 text-xs text-[var(--sa-alias-label-caption)]">{label}</div>
    </div>
  )
}

/** 运行信息面板组件。 */
export default function RunInfoPanel() {
  const sessionId = useChatStore((s) => s.sessionId)
  const stats = useChatStore((s) => s.stats)
  const streaming = useChatStore((s) => s.streaming)
  const activeRunId = useChatStore((s) => s.activeRunId)
  const sessions = useSessionsStore((s) => s.sessions)
  const assistants = useAssistantsStore((s) => s.assistants)

  const session = sessions.find((s) => s._id === sessionId)
  const assistantName = assistants.find((a) => a._id === session?.assistant_id)?.name
  const toolRows = Object.entries(stats.byTool)
  const toolTotal = toolRows.reduce((n, [, t]) => n + t.calls, 0)

  // 无会话空态
  if (!session) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
        <svg
          width="28"
          height="28"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-[var(--sa-alias-label-caption)]"
          aria-hidden="true"
        >
          <path d="m3 9 2.2 2.2L8.5 7.9M3 4.2l1.5 1.5M7.8 4.2 6.3 5.7M9.8 4.2l1.5 1.5M13 4.2l-1.5 1.5" opacity="0.6" />
          <path d="M9.8 9.6 12 11.8" />
        </svg>
        <div className="text-[13px] text-[var(--sa-alias-label-secondary)]">暂无会话</div>
        <div className="text-xs text-[var(--sa-alias-label-caption)]">
          新建对话后可查看轮数、事件与工具调用统计
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {/* 会话概要 */}
      <div>
        <div className="truncate text-[13px] font-medium text-[var(--sa-alias-label-primary)]">
          {session.title || '新对话'}
        </div>
        <div className="flex items-center gap-1.5 pt-1 text-xs text-[var(--sa-alias-label-caption)]">
          <span className="truncate">{assistantName ?? '未关联助手'}</span>
          <span aria-hidden="true">·</span>
          <span>更新于 {formatRelativeTime(session.updated_at)}</span>
        </div>
      </div>

      {/* 流状态 */}
      <div className="flex items-center justify-between rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] px-2.5 py-2">
        <span className="text-[13px] text-[var(--sa-alias-label-secondary)]">当前流状态</span>
        <span
          className={`flex items-center gap-1.5 text-[13px] ${
            streaming ? 'text-[var(--sa-alias-state-business-primary)]' : 'text-[var(--sa-alias-label-tertiary)]'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              streaming ? 'animate-pulse bg-[var(--sa-alias-state-business-primary)]' : 'bg-[var(--sa-alias-label-caption)]'
            }`}
            aria-hidden="true"
          />
          {streaming ? '接收中' : '空闲'}
          {streaming && activeRunId && (
            <span className="text-xs text-[var(--sa-alias-label-caption)]" title={`run ${activeRunId}`}>
              run
            </span>
          )}
        </span>
      </div>

      {/* 统计格：轮数 / 事件总数 / 消息数 */}
      <div className="grid grid-cols-3 gap-2">
        <StatCell value={stats.turns} label={`轮数${stats.aborted ? `（中止 ${stats.aborted}）` : ''}`} />
        <StatCell value={stats.events} label="事件总数" />
        <StatCell value={session.message_count} label="消息数" />
      </div>

      {/* 工具调用明细 */}
      <div className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
        <div className="flex items-center justify-between px-2.5 py-2 text-[13px]">
          <span className="text-[var(--sa-alias-label-secondary)]">工具调用</span>
          <span className="text-[var(--sa-alias-label-primary)]">{toolTotal} 次</span>
        </div>
        {toolRows.length === 0 ? (
          <div className="px-2.5 pb-2.5 text-xs text-[var(--sa-alias-label-caption)]">
            本会话暂无工具调用
          </div>
        ) : (
          <div className="border-t border-[var(--sa-alias-border-l1)]">
            <div className="grid grid-cols-[1fr_2.5rem_2.5rem_2.5rem] gap-1 px-2.5 py-1.5 text-xs text-[var(--sa-alias-label-caption)]">
              <span>工具</span>
              <span className="text-right">调用</span>
              <span className="text-right">成功</span>
              <span className="text-right">失败</span>
            </div>
            {toolRows.map(([name, t]) => (
              <div
                key={name}
                className="grid grid-cols-[1fr_2.5rem_2.5rem_2.5rem] gap-1 border-t border-[var(--sa-alias-border-l1)] px-2.5 py-1.5 text-xs"
              >
                <span className="truncate text-[var(--sa-alias-label-primary)]" title={name}>
                  {TOOL_LABELS[name] ?? name}
                </span>
                <span className="text-right text-[var(--sa-alias-label-secondary)]">{t.calls}</span>
                <span className="text-right text-[var(--sa-alias-state-success-primary)]">{t.ok}</span>
                <span className={`text-right ${t.fail ? 'text-[var(--sa-alias-state-error-primary)]' : 'text-[var(--sa-alias-label-caption)]'}`}>
                  {t.fail}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 错误计数（无错误不展示，避免噪音） */}
      {stats.errors > 0 && (
        <div className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] px-2.5 py-2 text-[13px]">
          <span className="text-[var(--sa-alias-label-secondary)]">错误事件</span>
          <span className="pl-2 text-[var(--sa-alias-state-error-primary)]">{stats.errors} 次</span>
        </div>
      )}
    </div>
  )
}
