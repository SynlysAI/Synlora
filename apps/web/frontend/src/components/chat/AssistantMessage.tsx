/**
 * 助手消息：文档流排版（无气泡，头像在 turn 头部行——见 MessageList），
 * 尾部 hover 操作行：复制原文 / 任务用时 / token 用量（Jiuwen 展示模式）。
 *
 * 代码块处理：覆盖 pre 为「横幅（语言 + 复制）+ 代码区」容器，并从子 <code>
 * 提取纯文本自行渲染（AST 的块级 code 因此不再经过行内 code 覆盖）。
 */
import { isValidElement, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** 递归提取 React 子树的纯文本（代码块内容还原）。 */
function nodeText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (isValidElement(node)) {
    return nodeText((node.props as { children?: ReactNode }).children)
  }
  return ''
}

/** 从 <pre> 的子 <code> 上提取语言标识（无语言围栏返回空串）。 */
function extractLang(children: ReactNode): string {
  const child = Array.isArray(children) ? children[0] : children
  if (!isValidElement(child)) return ''
  const className = (child.props as { className?: string }).className ?? ''
  return /language-([\w+-]+)/.exec(className)?.[1] ?? ''
}

/** 代码块头部：语言名 + 复制按钮（复制成功短暂变为 ✓）。 */
function CodeHeader({ lang, text }: { lang: string; text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* 剪贴板不可用（非安全上下文）时静默 */
    }
  }
  return (
    <div className="flex items-center justify-between bg-[var(--sa-alias-markdown-code-block-banner)] pl-3 pr-1.5 py-1">
      <span
        className="text-[11px] tracking-wide text-[var(--sa-alias-label-caption)]"
        style={{ fontFamily: 'var(--sa-font-code)' }}
      >
        {lang || 'text'}
      </span>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label="复制代码"
        className="flex items-center gap-1 rounded-[var(--sa-radius-sm)] px-1.5 py-0.5 text-[11px] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-secondary)]"
      >
        {copied ? (
          '已复制'
        ) : (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
            <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
            <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
          </svg>
        )}
      </button>
    </div>
  )
}

/** markdown 元素渲染映射（token 化样式，明暗主题自适应）。 */
const mdComponents: Components = {
  // pre 改造为「横幅（语言 + 复制）+ 代码块」容器；children 即内部 <code>
  pre: ({ children }) => {
    const lang = extractLang(children)
    const text = nodeText(children)
    return (
      <div className="my-2.5 overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
        <CodeHeader lang={lang} text={text} />
        <pre
          className="overflow-x-auto bg-[var(--sa-alias-markdown-code-block)] px-3.5 py-3 text-[13px] leading-relaxed text-[var(--sa-alias-label-primary)]"
          style={{ fontFamily: 'var(--sa-font-code)' }}
        >
          <code>{text}</code>
        </pre>
      </div>
    )
  },
  // 行内代码（块级 code 已由 pre 覆盖提取，不会进入此分支）
  code: ({ children }) => (
    <code
      className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-markdown-inline-code)] px-1.5 py-0.5 text-[13px] text-[var(--sa-alias-label-primary)]"
      style={{ fontFamily: 'var(--sa-font-code)' }}
    >
      {children}
    </code>
  ),
  p: ({ children }) => <p className="my-2 leading-[1.75]">{children}</p>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-[var(--sa-alias-link)] underline">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 leading-[1.75]">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 leading-[1.75]">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="my-2.5 border-l-2 border-[var(--sa-alias-border-l3)] pl-3.5 text-[var(--sa-alias-label-secondary)]">
      {children}
    </blockquote>
  ),
  h1: ({ children }) => <h1 className="mb-2 mt-4 text-[18px] font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1.5 mt-4 text-[17px] font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-3 text-[15px] font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-2.5 text-[14px] font-semibold">{children}</h4>,
  table: ({ children }) => (
    <div className="my-2.5 overflow-x-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-markdown-code-block-banner)] px-3 py-1.5 text-left font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-[var(--sa-alias-border-l1)] px-3 py-1.5 align-top">{children}</td>
  ),
}

/** 秒数格式化（Jiuwen 风格：3.2s / 1m05s）。 */
export function formatElapsed(ms: number): string {
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(2)}s`
  const m = Math.floor(s / 60)
  const rest = Math.round(s % 60)
  return `${m}m${String(rest).padStart(2, '0')}s`
}

/** 完成时间戳格式化（HH:mm:ss，Jiuwen 消息尾部同款）。 */
function formatClock(tsSeconds: number): string {
  const d = new Date(tsSeconds * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** token 数千分位。 */
function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

interface AssistantMessageProps {
  /** 消息 markdown 文本。 */
  content: string
  /** 流式进行中：尾部渲染渐变脉冲光标。 */
  streaming?: boolean
  /**
   * 中间解说（非本轮最终回答）：不渲染尾部时间/复制/用量。
   *
   * 照 jiuwen `buildTurnTimeline` 的 `hideMeta` 与 DSH 的 turn-process——
   * 一轮里只要有后续正文，前面的正文都是工具调用之间的过程解说，
   * 收进「任务用时」折叠区，不该有自己的复制按钮。
   */
  hideMeta?: boolean
  /** 本轮 token 用量（turn/end payload）。 */
  usage?: { prompt_tokens: number; completion_tokens: number }
  /** 本轮完成时间戳（秒，尾部首个展示）。 */
  finishedTs?: number
}

/** 助手消息组件（文档流排版 + 尾部操作行）。 */
export default function AssistantMessage({
  content, streaming, hideMeta, usage, finishedTs,
}: AssistantMessageProps) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* 剪贴板不可用时静默 */
    }
  }

  return (
    <div className="group/assistant min-w-0">
      <div className="text-[15px] text-[var(--sa-alias-label-primary)]">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
          {content}
        </ReactMarkdown>
        {streaming && (
          <span
            aria-hidden="true"
            className="ml-0.5 inline-block h-[16px] w-[7px] translate-y-[2px] rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-label-primary)] opacity-80 [animation:sa-blink_1s_ease-in-out_infinite]"
          />
        )}
      </div>
      {/* 尾部操作行（Jiuwen 顺序）：完成时间 → 复制（hover 显现）→ token 用量。
          中间解说（hideMeta）不渲染，操作行一轮只有最终回答这一份。 */}
      {!streaming && !hideMeta && (
        <div className="flex items-center gap-3 pt-0.5 text-[11.5px] text-[var(--sa-alias-label-caption)]">
          {finishedTs != null && (
            <span className="tabular-nums">{formatClock(finishedTs)}</span>
          )}
          <button
            type="button"
            onClick={() => void copy()}
            aria-label="复制回复"
            className="flex items-center gap-1 rounded-[var(--sa-radius-sm)] px-1 py-0.5 transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-secondary)]"
          >
            {copied ? (
              '已复制'
            ) : (
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
                <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
                <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
              </svg>
            )}
          </button>
          {usage && (
            <span className="tabular-nums">
              {formatTokens(usage.prompt_tokens)} in / {formatTokens(usage.completion_tokens)} out
            </span>
          )}
        </div>
      )}
    </div>
  )
}
