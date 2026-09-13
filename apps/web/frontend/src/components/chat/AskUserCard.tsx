/**
 * ask_user 问题卡（照抄 jiuwen InlineQuestionCard 的交互形态）：
 * - 单问题：带选项时点选项**立即提交**（jiuwen 单问题模式语义）；
 * - 始终保留自由输入行（输入 + 提交按钮，Enter 发送）；
 * - 审批模式（approval，管线 Permission.ASK_USER 强制打断）：展示工具名与
 *   参数摘要，仅"允许 / 拒绝"两个按钮（提交固定文案，管线按文本判定）；
 * - 已回答（tool/result 回填 answer）后整卡只读，展示答案；
 * - 回放（非流式）同样只读。
 */
import { useState } from 'react'
import { useChatStore } from '@/stores/chat'

interface AskUserCardProps {
  /** 配对的 tool_call_id。 */
  callId: string
  /** 问题文本。 */
  query: string
  /** 预置选项（可空 = 纯自由输入）。 */
  options: { label: string; description?: string }[]
  /** 管线强制审批信息（存在即审批模式）。 */
  approval?: { tool: string; argsPreview?: string }
  /** 已回答（live 由 tool/result 回填；回放同样来自事件投影）。 */
  answer?: string
}

/** ask_user 问题卡组件（对话流内联）。 */
export default function AskUserCard({ callId, query, options, approval, answer }: AskUserCardProps) {
  const streaming = useChatStore((s) => s.streaming)
  const answerAsk = useChatStore((s) => s.answerAsk)
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const answered = answer != null
  const interactive = streaming && !answered && !submitting

  /** 提交回答（选项点击 / 自由输入 Enter / 按钮）。 */
  const submit = async (value: string) => {
    const trimmed = value.trim()
    if (!trimmed || !interactive) return
    setSubmitting(true)
    try {
      await answerAsk(trimmed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="my-1 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3.5 py-3" data-call-id={callId}>
      <div className="flex items-start gap-2 pb-2">
        <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)]" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 3.2a4.4 4.4 0 0 0-2.6 7.95v1.6a.9.9 0 0 0 .9.9h3.4a.9.9 0 0 0 .9-.9v-1.6A4.4 4.4 0 0 0 10 3.2z" />
            <path d="M8.3 16.2h3.4" />
          </svg>
        </span>
        <p className="m-0 min-w-0 flex-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-[var(--sa-alias-label-primary)]">
          {query}
        </p>
      </div>

      {approval?.argsPreview && (
        <pre className="mb-2 max-h-40 overflow-auto rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all text-[var(--sa-alias-label-secondary)]">
          {approval.argsPreview}
        </pre>
      )}

      {options.length > 0 && (
        <div className="flex flex-col gap-1.5 pb-2">
          {options.map((opt) => (
            <button
              key={opt.label}
              type="button"
              disabled={!interactive}
              onClick={() => void submit(opt.label)}
              title={opt.description || opt.label}
              className="flex items-center justify-between gap-2 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-3 py-1.5 text-left text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] enabled:hover:border-[var(--sa-alias-border-l3)] enabled:hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-70"
            >
              <span className="min-w-0 truncate">{opt.label}</span>
              {opt.description && (
                <span className="shrink-0 text-xs text-[var(--sa-alias-label-caption)]">{opt.description}</span>
              )}
            </button>
          ))}
        </div>
      )}

      {answered ? (
        <div className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
          {answer}
        </div>
      ) : approval ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={!interactive}
            onClick={() => void submit('允许')}
            className="h-8 shrink-0 rounded-[var(--sa-radius-sm)] border-0 bg-[var(--sa-alias-button-primary-fill)] px-3 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? '提交中…' : '允许'}
          </button>
          <button
            type="button"
            disabled={!interactive}
            onClick={() => void submit('拒绝')}
            className="h-8 shrink-0 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-3 text-[13px] font-medium text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] enabled:hover:border-[var(--sa-alias-border-l3)] enabled:hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            拒绝
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={text}
            disabled={!interactive}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void submit(text)
              }
            }}
            placeholder="输入回答…"
            aria-label="回答输入框"
            className="h-8 min-w-0 flex-1 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-2.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)] disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type="button"
            disabled={!interactive || !text.trim()}
            onClick={() => void submit(text)}
            className="h-8 shrink-0 rounded-[var(--sa-radius-sm)] border-0 bg-[var(--sa-alias-button-primary-fill)] px-3 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? '提交中…' : '回答'}
          </button>
        </div>
      )}
    </div>
  )
}
