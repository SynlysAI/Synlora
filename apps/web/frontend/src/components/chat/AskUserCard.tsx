/**
 * ask_user 问答卡（交互照抄 jiuwen InteractionPrompt：勾选点击式 + 多题翻页）：
 * - 勾选式：点选项只本地标选（单选 radio / 多选 check 视觉）**不立即发送**，
 *   底部主按钮（下一步 / 确认）统一提交，最后一页一次交全部答案；
 * - 多问题（questions，≤4 题）：头部翻页器 ‹ n/N ›，底部 取消 / 跳过 / 主按钮；
 * - 有选项的单选题自动追加「其他」选项（勾选后展开自由输入，未填字禁提交）；
 *   多选题与纯输入题常驻自由输入行；
 * - 答案回传为拼接文本（多题「n. 题干：答案」逐行），经 answer API 回流工具；
 * - 已回答（tool/result 回填 answer）后整卡只读；回放（非流式）同样只读；
 * - 审批模式（approval，管线 Permission.ASK_USER）不走勾选流程：允许/拒绝两按钮；
 * - 位置（照 jiuwen InteractionSlot）：**待作答**时由吸附槽浮在输入框上方（不占
 *   消息流），**作答后**仍作为过程条目留在消息流、随「任务用时」chip 折叠。
 */
import { useState } from 'react'
import { useChatStore } from '@/stores/chat'

/** 「其他」选项固定文案（勾选后展开自由输入）。 */
const OTHER = '其他'

/** 归一化题结构（单题 query 模式也折成一题数组，统一走勾选/翻页流程）。 */
interface Q {
  question: string
  header?: string
  options: { label: string; description?: string }[]
  multiSelect?: boolean
}

/** 单题作答草稿（勾选 labels + 自由输入；skipped = 用户点了跳过）。 */
interface Draft {
  selected: string[]
  custom: string
  skipped?: boolean
}

interface AskUserCardProps {
  /** 配对的 tool_call_id。 */
  callId: string
  /** 问题文本（单题模式）。 */
  query: string
  /** 预置选项（单题模式；空 = 纯自由输入）。 */
  options: { label: string; description?: string }[]
  /** 多问题模式题组（存在则忽略 query/options）。 */
  questions?: Q[]
  /** 管线强制审批信息（存在即审批模式）。 */
  approval?: { tool: string; argsPreview?: string }
  /** 已回答（live 由 tool/result 回填；回放同样来自事件投影）。 */
  answer?: string
  /** 浮层形态（吸附槽内）：加投影与消息流内联卡区分。 */
  floating?: boolean
}

/**
 * ask_user 问题卡组件。
 *
 * 两种形态共用同一份内容：待作答时由吸附槽以浮层形态渲染（`floating`，输入框
 * 正上方），作答后的回显由消息流常驻渲染。
 *
 * @param props 见 AskUserCardProps。
 */
export default function AskUserCard({ callId, query, options, questions, approval, answer, floating }: AskUserCardProps) {
  const streaming = useChatStore((s) => s.streaming)
  const answerAsk = useChatStore((s) => s.answerAsk)
  const qs: Q[] = questions?.length ? questions : [{ question: query, options }]
  const [page, setPage] = useState(0)
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [submitting, setSubmitting] = useState(false)
  const answered = answer != null
  const interactive = streaming && !answered && !submitting
  const total = qs.length
  const cur = qs[Math.min(page, total - 1)]
  const curOpts = cur.options ?? []
  const hasOptions = curOpts.length > 0
  const withOther = hasOptions && !cur.multiSelect
  const allOpts = withOther ? [...curOpts, { label: OTHER }] : curOpts
  const draft: Draft = drafts[page] ?? { selected: [], custom: '' }
  const otherChosen = draft.selected.includes(OTHER)
  // 「其他」勾选后必须填字才可提交（照抄 jiuwen incompleteCustom）
  const customIncomplete = otherChosen && !draft.custom.trim()
  const isLast = page >= total - 1

  /** 勾选/取消勾选一个选项（单选覆盖、多选切换；跳过后重新作答清除跳过标记）。 */
  const toggle = (label: string) => {
    setDrafts((prev) => {
      const d = prev[page] ?? { selected: [], custom: '' }
      if (cur.multiSelect) {
        const selected = d.selected.includes(label)
          ? d.selected.filter((x) => x !== label)
          : [...d.selected, label]
        return { ...prev, [page]: { ...d, selected, skipped: false } }
      }
      return { ...prev, [page]: { ...d, selected: d.selected[0] === label ? [] : [label], skipped: false } }
    })
  }

  /** 单题答案文本：勾选项（其他→自由输入）拼接；未作答兜底取首项（照抄 jiuwen）。 */
  const answerTextOf = (q: Q, d: Draft): string => {
    if (d.skipped) return '（用户跳过此题）'
    const sel = d.selected.filter((s) => s !== OTHER)
    const custom = d.custom.trim()
    if ((q.options ?? []).length) {
      const parts = [...sel]
      if (custom) parts.push(custom)
      return parts.length ? parts.join('、') : q.options[0].label
    }
    return custom || '（未作答）'
  }

  /** 提交回答（确认按钮：一次交全部题；单题为纯答案文本，多题逐行带题干）。 */
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

  const submitAll = () => {
    const lines = qs.map((q, i) => {
      const d = drafts[i] ?? { selected: [], custom: '' }
      return total > 1 ? `${i + 1}. ${q.question}：${answerTextOf(q, d)}` : answerTextOf(q, d)
    })
    void submit(lines.join('\n'))
  }

  /** 卡片根样式（两种模式共用；浮层形态加投影，照 jiuwen `.ix-prompt` 浮卡）。 */
  const rootClass =
    'my-1 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3.5 py-3' +
    (floating ? ' shadow-md' : '')

  // ---- 审批模式：不走勾选流程 ----
  const approvalView = approval && (
    <>
      {approval.argsPreview && (
        <pre className="mb-2 max-h-40 overflow-auto rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all text-[var(--sa-alias-label-secondary)]">
          {approval.argsPreview}
        </pre>
      )}
      {answered ? (
        <div className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
          {answer}
        </div>
      ) : (
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
      )}
    </>
  )
  if (approval) {
    return (
      <div className={rootClass} data-call-id={callId}>
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
        {approvalView}
      </div>
    )
  }

  // ---- 问答模式：勾选 + 翻页 ----
  const questionView = answered ? (
    <div className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-1.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[var(--sa-alias-label-secondary)]">
      {answer}
    </div>
  ) : (
    <>
      {/* 题干 */}
      <p className="m-0 pb-2 text-[13.5px] leading-relaxed whitespace-pre-wrap break-words text-[var(--sa-alias-label-primary)]">
        {cur.question}
      </p>

      {/* 选项（勾选标记：单选圆点 / 多选对勾；点击只标选不发送） */}
      {allOpts.length > 0 && (
        <div className="flex flex-col gap-1.5 pb-2">
          {allOpts.map((opt) => {
            const chosen = draft.selected.includes(opt.label)
            return (
              <button
                key={opt.label}
                type="button"
                disabled={!interactive || draft.skipped}
                onClick={() => toggle(opt.label)}
                title={opt.description || opt.label}
                className={`flex items-center gap-2.5 rounded-[var(--sa-radius-sm)] border px-3 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] disabled:cursor-not-allowed disabled:opacity-70 ${
                  chosen
                    ? 'border-[var(--sa-alias-border-l4)] bg-[var(--sa-alias-interactive-bg-hover)] text-[var(--sa-alias-label-primary)]'
                    : 'border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] text-[var(--sa-alias-label-primary)] enabled:hover:border-[var(--sa-alias-border-l3)] enabled:hover:bg-[var(--sa-alias-interactive-bg-hover)]'
                }`}
              >
                <span
                  className={`flex h-4 w-4 shrink-0 items-center justify-center border transition-colors ${
                    cur.multiSelect ? 'rounded-[4px]' : 'rounded-full'
                  } ${chosen ? 'border-[var(--sa-alias-border-l4)]' : 'border-[var(--sa-alias-border-l3)]'}`}
                  aria-hidden="true"
                >
                  {chosen && !cur.multiSelect && (
                    <span className="h-2 w-2 rounded-full bg-[var(--sa-alias-border-l4)]" />
                  )}
                  {chosen && cur.multiSelect && (
                    <svg viewBox="0 0 12 12" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="m2.5 6.5 2.3 2.3 4.7-5" className="text-[var(--sa-alias-border-l4)]" />
                    </svg>
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {opt.label}
                  {opt.description && (
                    <span className="ml-2 text-xs text-[var(--sa-alias-label-caption)]">{opt.description}</span>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {/* 自由输入：纯输入/多选题常驻；单选题勾选「其他」后展开（未填字禁主按钮） */}
      {(otherChosen || !hasOptions || cur.multiSelect) && (
        <input
          type="text"
          value={draft.custom}
          disabled={!interactive || draft.skipped}
          onChange={(e) =>
            setDrafts((prev) => ({
              ...prev,
              [page]: { ...(prev[page] ?? { selected: [], custom: '' }), custom: e.target.value, skipped: false },
            }))
          }
          placeholder={otherChosen ? '请输入其他内容…' : '补充说明（可选）…'}
          aria-label="自由输入"
          className="mb-2 h-8 w-full min-w-0 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-2.5 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)] disabled:cursor-not-allowed disabled:opacity-60"
        />
      )}

      {/* 底部操作行：取消 | 跳过 | 下一步/确认（统一提交，勾选不再即点即发） */}
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          disabled={!interactive}
          onClick={() => void submit('用户已取消本次问答，未作答。')}
          className="h-8 rounded-[var(--sa-radius-sm)] border-0 bg-transparent px-2.5 text-[13px] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] enabled:hover:text-[var(--sa-alias-label-secondary)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          取消
        </button>
        <button
          type="button"
          disabled={!interactive}
          onClick={() => {
            setDrafts((prev) => ({
              ...prev,
              [page]: { ...(prev[page] ?? { selected: [], custom: '' }), skipped: true },
            }))
            if (!isLast) setPage(page + 1)
          }}
          className="h-8 rounded-[var(--sa-radius-sm)] border-0 bg-transparent px-2.5 text-[13px] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] enabled:hover:text-[var(--sa-alias-label-secondary)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          跳过
        </button>
        <button
          type="button"
          disabled={!interactive || customIncomplete}
          onClick={() => (isLast ? submitAll() : setPage(page + 1))}
          className="h-8 shrink-0 rounded-[var(--sa-radius-sm)] border-0 bg-[var(--sa-alias-button-primary-fill)] px-3.5 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? '提交中…' : isLast ? '确认' : '下一步'}
        </button>
      </div>
    </>
  )

  return (
    <div className={rootClass} data-call-id={callId}>
      {/* 头部行：图标 + 标题（多题优先 header）+ 翻页器 */}
      <div className="flex items-center gap-2 pb-2">
        <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)]" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 3.2a4.4 4.4 0 0 0-2.6 7.95v1.6a.9.9 0 0 0 .9.9h3.4a.9.9 0 0 0 .9-.9v-1.6A4.4 4.4 0 0 0 10 3.2z" />
            <path d="M8.3 16.2h3.4" />
          </svg>
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--sa-alias-label-primary)]">
          {total > 1 ? cur.header || `问题 ${page + 1}` : '请回答'}
        </span>
        {total > 1 && (
          <div className="flex shrink-0 items-center gap-1 text-xs text-[var(--sa-alias-label-caption)]">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
              aria-label="上一题"
              className="flex h-5 w-5 items-center justify-center rounded-[var(--sa-radius-sm)] border-0 bg-transparent text-[var(--sa-alias-label-secondary)] disabled:opacity-40"
            >
              <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m10 4-4 4 4 4" />
              </svg>
            </button>
            <span className="tabular-nums">{page + 1}/{total}</span>
            <button
              type="button"
              disabled={page >= total - 1}
              onClick={() => setPage(page + 1)}
              aria-label="下一题"
              className="flex h-5 w-5 items-center justify-center rounded-[var(--sa-radius-sm)] border-0 bg-transparent text-[var(--sa-alias-label-secondary)] disabled:opacity-40"
            >
              <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m6 4 4 4-4 4" />
              </svg>
            </button>
          </div>
        )}
      </div>
      {questionView}
    </div>
  )
}
