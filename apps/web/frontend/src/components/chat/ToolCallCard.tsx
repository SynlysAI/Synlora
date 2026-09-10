/**
 * 工具调用卡片：头行（工具名 + 状态）+ 参数折叠块（默认展开，JSON 展示）
 * + 结果折叠块（默认收起；content 代码风格文本，truncated 尾部截断标记）。
 */
import { useState, type ReactNode } from 'react'
import type { ToolCallPayload, ToolResultPayload } from '@/types'

/** 工具名 → 中文标签映射（未收录回退原名）。 */
const TOOL_LABELS: Record<string, string> = {
  'python.run': 'Python 执行',
  'file.read': '读取文件',
  'file.write': '写入文件',
  'file.list': '列出文件',
  'knowledge.search': '知识库搜索',
  'http.request': 'HTTP 请求',
}

/** 代码风格文本块（参数 JSON / 结果 content 共用）。 */
function CodeBlock({ text }: { text: string }) {
  return (
    <pre
      className="max-h-72 overflow-auto rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-markdown-code-block)] px-3 py-2 text-[12.5px] leading-relaxed text-[var(--sa-alias-label-primary)]"
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
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-xs text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
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
      {open && <div className="px-3 pb-2.5">{children}</div>}
    </div>
  )
}

interface ToolCallCardProps {
  /** tool/call 事件负载。 */
  call: ToolCallPayload
  /** tool/result 事件负载（未返回时展示执行中）。 */
  result?: ToolResultPayload
}

/** 工具调用卡片组件（左对齐占满、边框 l2 圆角容器）。 */
export default function ToolCallCard({ call, result }: ToolCallCardProps) {
  const [paramsOpen, setParamsOpen] = useState(true)
  const [resultOpen, setResultOpen] = useState(false)

  const name = call.tool_call.name
  const label = TOOL_LABELS[name] ?? name
  const args = call.tool_call.arguments ?? {}
  const hasArgs = Object.keys(args).length > 0

  return (
    <div className="overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
      {/* 头行：图标 + 名称 + 状态 */}
      <div className="flex items-center gap-2 px-3 py-2">
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0 text-[var(--sa-alias-label-secondary)]"
          aria-hidden="true"
        >
          <path d="M9.3 3.2a3.2 3.2 0 0 1 4.4 4.4l-1.4 1.4-4.4-4.4 1.4-1.4Z" />
          <path d="m7.5 5 1.5 1.5M6 6.5 2.9 9.6a2.3 2.3 0 0 0 3.2 3.2L9.3 9.7" />
        </svg>
        <span className="text-[13px] font-medium text-[var(--sa-alias-label-primary)]">{label}</span>
        <span className="truncate text-xs text-[var(--sa-alias-label-caption)]" title={name}>
          {name}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1 text-xs">
          {!result ? (
            <span className="flex items-center gap-1 text-[var(--sa-alias-label-tertiary)]">
              <svg className="h-3 w-3 animate-spin" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" opacity="0.25" />
                <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              执行中
            </span>
          ) : result.ok ? (
            <span className="flex items-center gap-1 text-[var(--sa-alias-state-success-primary)]">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m3 8.5 3.2 3L13 4.5" />
              </svg>
              成功
            </span>
          ) : (
            <span className="flex items-center gap-1 text-[var(--sa-alias-state-error-primary)]">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
              失败
            </span>
          )}
        </span>
      </div>

      {/* 参数：默认展开 */}
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
