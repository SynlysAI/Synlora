/**
 * 助手消息：react-markdown 渲染（代码块横幅 + code-block 背景 token、
 * 表格简单样式、行内代码）；流式进行中尾部闪烁光标。
 *
 * 代码块处理：覆盖 pre 为「横幅 + 代码区」容器，并从子 <code> 提取纯文本
 * 自行渲染（AST 的块级 code 因此不再经过行内 code 覆盖，避免双重样式）。
 */
import { isValidElement, type ReactNode } from 'react'
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

/** markdown 元素渲染映射（token 化样式，明暗主题自适应）。 */
const mdComponents: Components = {
  // pre 改造为「横幅 + 代码块」容器；children 即内部 <code>
  pre: ({ children }) => {
    const lang = extractLang(children)
    return (
      <div className="my-2 overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
        <div className="bg-[var(--sa-alias-markdown-code-block-banner)] px-3 py-1.5 text-xs text-[var(--sa-alias-label-caption)]">
          {lang || 'text'}
        </div>
        <pre
          className="overflow-x-auto bg-[var(--sa-alias-markdown-code-block)] px-3 py-2.5 text-[13px] leading-relaxed text-[var(--sa-alias-label-primary)]"
          style={{ fontFamily: 'var(--sa-font-code)' }}
        >
          <code>{nodeText(children)}</code>
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
  p: ({ children }) => <p className="my-1.5 leading-relaxed">{children}</p>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-[var(--sa-alias-link)] underline">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="my-1.5 list-disc pl-5 leading-relaxed">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal pl-5 leading-relaxed">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-[var(--sa-alias-border-l3)] pl-3 text-[var(--sa-alias-label-secondary)]">
      {children}
    </blockquote>
  ),
  h1: ({ children }) => <h1 className="mb-1.5 mt-3 text-[18px] font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1.5 mt-3 text-[16px] font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 mt-2 text-[15px] font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-2 text-[14px] font-semibold">{children}</h4>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
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
  /** 流式进行中：尾部渲染闪烁光标。 */
  streaming?: boolean
}

/** 助手消息气泡组件（左对齐浅底 + markdown 渲染）。 */
export default function AssistantMessage({ content, streaming }: AssistantMessageProps) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-[var(--sa-radius-lg)] bg-[var(--sa-specific-bubble)] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--sa-alias-label-primary)]">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
          {content}
        </ReactMarkdown>
        {streaming && (
          <span
            aria-hidden="true"
            className="ml-0.5 inline-block h-[15px] w-[2px] translate-y-[2px] animate-pulse bg-[var(--sa-alias-label-primary)]"
          />
        )}
      </div>
    </div>
  )
}
