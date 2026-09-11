/**
 * 助手消息：文档流排版（头像 + 全宽内容，无气泡底）——现代 AI 对话产品的
 * 正文呈现方式：长 markdown 内容按文档阅读，气泡只留给用户侧。
 *
 * 代码块处理：覆盖 pre 为「横幅 + 代码区」容器，并从子 <code> 提取纯文本
 * 自行渲染（AST 的块级 code 因此不再经过行内 code 覆盖，避免双重样式）。
 */
import { isValidElement, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Avatar from './Avatar'

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

interface AssistantMessageProps {
  /** 消息 markdown 文本。 */
  content: string
  /** 流式进行中：尾部渲染渐变脉冲光标。 */
  streaming?: boolean
  /** 助手头像字符（emoji 或首字）。 */
  avatar?: string
}

/** 助手消息组件（头像 + 文档流排版 + markdown 渲染）。 */
export default function AssistantMessage({ content, streaming, avatar }: AssistantMessageProps) {
  return (
    <div className="flex items-start gap-2.5">
      <Avatar char={avatar?.trim() || '科'} />
      <div className="min-w-0 flex-1 pt-0.5 text-[15px] text-[var(--sa-alias-label-primary)]">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
          {content}
        </ReactMarkdown>
        {streaming && (
          <span
            aria-hidden="true"
            className="ml-0.5 inline-block h-[16px] w-[7px] translate-y-[2px] rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-brand-primary)] opacity-80 [animation:sa-blink_1s_ease-in-out_infinite]"
          />
        )}
      </div>
    </div>
  )
}
