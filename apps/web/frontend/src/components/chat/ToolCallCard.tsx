/**
 * 工具调用卡片：头行（状态点 + 中文标签 + 等宽工具名）+ 参数/结果折叠区
 * （默认收起保持消息流安静；结果含 truncated 标记与错误文案）。
 */
import { useState, type ReactNode } from 'react'
import type { ToolCallPayload, ToolResultPayload } from '@/types'
import { TOOL_LABELS } from './toolLabels'

/** 代码风格文本块（参数 JSON / 结果 content 共用）。 */
function CodeBlock({ text }: { text: string }) {
  return (
    <pre
      className="max-h-72 overflow-auto rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-markdown-code-block)] px-3 py-2.5 text-[12.5px] leading-relaxed text-[var(--sa-alias-label-primary)]"
      style={{ fontFamily: 'var(--sa-font-code)' }}
    >
      {text}
    </pre>
  )
}

interface SectionProps {
  /** 区块标题（参数/结果）。 */
  title: string
  /** 是否展开。 */
  open: boolean
  /** 标题行右侧附加标记（如截断提示）。 */
  badge?: ReactNode
  /** 点击标题行切换展开。 */
  onToggle: () => void
  children: ReactNode
}

/** 可折叠区块：标题行（旋转箭头）+ 展开内容。 */
function Section({ title, open, badge, onToggle, children }: SectionProps) {
  return (
    <div className="border-t border-[var(--sa-alias-border-l1)]">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3.5 py-1.5 text-[12px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`transition-transform duration-[var(--sa-duration-base)] ${open ? 'rotate-90' : ''}`}
          aria-hidden="true"
        >
          <path d="M6 3.5 10.5 8 6 12.5" />
        </svg>
        {title}
        {badge}
      </button>
      {open && <div className="px-3.5 pb-2.5">{children}</div>}
    </div>
  )
}

/** 状态点：色点 + 文案（running 时脉冲呼吸）。 */
function StatusDot({ tone }: { tone: 'running' | 'ok' | 'error' }) {
  const color =
    tone === 'ok'
      ? 'var(--sa-alias-state-success-primary)'
      : tone === 'error'
        ? 'var(--sa-alias-state-error-primary)'
        : 'var(--sa-alias-label-tertiary)'
  return (
    <span
      aria-hidden="true"
      className={`h-[7px] w-[7px] shrink-0 rounded-[var(--sa-radius-full)] ${tone === 'running' ? '[animation:sa-pulse_1.2s_ease-in-out_infinite]' : ''}`}
      style={{ background: color }}
    />
  )
}

interface ToolCallCardProps {
  /** tool/call 事件负载。 */
  call: ToolCallPayload
  /** tool/result 事件负载（未返回时展示执行中）。 */
  result?: ToolResultPayload
}

/** 工具调用卡片组件（安静的窄卡片：状态点 + 标签 + 等宽名 + 折叠详情）。 */
export default function ToolCallCard({ call, result }: ToolCallCardProps) {
  const [paramsOpen, setParamsOpen] = useState(false)
  const [resultOpen, setResultOpen] = useState(false)

  const name = call.tool_call.name
  const label = TOOL_LABELS[name] ?? name
  const args = call.tool_call.arguments ?? {}
  const hasArgs = Object.keys(args).length > 0
  const tone: 'running' | 'ok' | 'error' = !result ? 'running' : result.ok ? 'ok' : 'error'
  const statusText = !result ? '执行中' : result.ok ? '完成' : '失败'

  return (
    <div className="ml-[42px] overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-bg-layer-1)] transition-colors duration-[var(--sa-duration-base)] hover:border-[var(--sa-alias-border-l2)]">
      {/* 头行：状态点 + 中文标签 + 等宽工具名 + 状态文案 */}
      <div className="flex items-center gap-2 px-3.5 py-[7px]">
        <StatusDot tone={tone} />
        <span className="text-[13px] font-medium text-[var(--sa-alias-label-primary)]">{label}</span>
        <span
          className="truncate text-[11.5px] text-[var(--sa-alias-label-caption)]"
          style={{ fontFamily: 'var(--sa-font-code)' }}
          title={name}
        >
          {name}
        </span>
        <span
          className={`ml-auto shrink-0 text-[12px] ${
            tone === 'ok'
              ? 'text-[var(--sa-alias-state-success-primary)]'
              : tone === 'error'
                ? 'text-[var(--sa-alias-state-error-primary)]'
                : 'text-[var(--sa-alias-label-tertiary)]'
          }`}
        >
          {statusText}
        </span>
      </div>

      {/* 参数：默认收起 */}
      <Section title="参数" open={paramsOpen} onToggle={() => setParamsOpen((v) => !v)}>
        {hasArgs ? (
          <CodeBlock text={JSON.stringify(args, null, 2)} />
        ) : (
          <div className="text-xs text-[var(--sa-alias-label-caption)]">（无参数）</div>
        )}
      </Section>

      {/* 结果：默认收起 */}
      {result && (
        <Section
          title="结果"
          open={resultOpen}
          onToggle={() => setResultOpen((v) => !v)}
          badge={
            result.truncated ? (
              <span className="ml-1 rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-state-warn-tertiary)] px-1.5 py-px text-[10px] text-[var(--sa-alias-state-warn-label)]">
                已截断
              </span>
            ) : undefined
          }
        >
          {!result.ok && result.error && (
            <div className="pb-1.5 text-xs text-[var(--sa-alias-state-error-primary)]">
              {result.error}
            </div>
          )}
          {result.content ? (
            <CodeBlock text={result.content + (result.truncated ? '\n…（内容已截断）' : '')} />
          ) : (
            <div className="text-xs text-[var(--sa-alias-label-caption)]">（无输出）</div>
          )}
        </Section>
      )}
    </div>
  )
}
