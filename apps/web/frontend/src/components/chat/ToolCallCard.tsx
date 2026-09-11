/**
 * 工具调用展示（逐结构照抄 Jiuwen tool-tree-item）：
 * - 单行：分类图标 + 可读动作描述（执行中=扫光，失败=暗红）+ hover 浮现的展开箭头
 * - 点击行内展开详情卡（浅卡片底，分块：参数 / 结果），grid rows 平滑下拉
 * - 与思考面板、正文同列对齐；默认收起，仅用户点击展开
 */
import { useState } from 'react'
import type { ToolCallPayload, ToolResultPayload } from '@/types'

/** 工具分类（决定行首图标，Jiuwen 五类）。 */
type Category = 'file' | 'search' | 'code' | 'system' | 'other'

/** 工具名 → 分类。 */
function categorize(name: string): Category {
  if (name.startsWith('file.')) return 'file'
  if (name === 'knowledge.search') return 'search'
  if (name === 'python.run') return 'code'
  if (name === 'http.request') return 'system'
  return 'other'
}

/** 分类图标（Jiuwen 同款五类线性 SVG）。 */
function CategoryIcon({ category }: { category: Category }) {
  return (
    <span className="flex h-[14px] w-[14px] shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)]" aria-hidden="true">
      {category === 'file' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5.5 3.5h5L15 8v8a.9.9 0 0 1-.9.9H5.5a.9.9 0 0 1-.9-.9V4.4a.9.9 0 0 1 .9-.9z" />
          <path d="M10.3 3.5V8H15" />
        </svg>
      ) : category === 'search' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="9" cy="9" r="4.3" />
          <path d="m12.3 12.3 3.4 3.4" />
        </svg>
      ) : category === 'code' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="m7.4 6.5-3.4 3.5 3.4 3.5" />
          <path d="m12.6 6.5 3.4 3.5-3.4 3.5" />
        </svg>
      ) : category === 'system' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3.5" y="4.5" width="13" height="11" rx="1.6" />
          <path d="m6.5 8.6 2.3 1.9-2.3 1.9" />
          <path d="M10.8 12.7h3" />
        </svg>
      ) : (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M13.4 4.6a2.7 2.7 0 0 0-3.3 3.4l-5 5a1.3 1.3 0 1 0 1.9 1.9l5-5a2.7 2.7 0 0 0 3.4-3.3l-2 2-1.9-.1-.1-1.9 2-2z" />
        </svg>
      )}
    </span>
  )
}

/** 参数值截断摘要（动作描述用）。 */
function brief(value: unknown, max = 24): string {
  const s = String(value ?? '').replace(/\s+/g, ' ').trim()
  return s.length > max ? `${s.slice(0, max)}…` : s
}

/** 工具名+参数 → 可读动作描述（Jiuwen describeToolCall 思路）。 */
function describe(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case 'file.read':
      return `读取 ${brief(args.path)}`
    case 'file.write':
      return `写入 ${brief(args.path)}`
    case 'file.list':
      return `列出 ${brief(args.path || '.')} 目录`
    case 'python.run':
      return `执行 Python 代码`
    case 'knowledge.search':
      return `检索「${brief(args.query, 18)}」`
    case 'http.request':
      return `请求 ${brief(args.url, 28)}`
    default:
      return name
  }
}

interface ToolCallCardProps {
  /** tool/call 事件负载。 */
  call: ToolCallPayload
  /** tool/result 事件负载（未返回时展示执行中）。 */
  result?: ToolResultPayload
}

/** 工具调用行组件（单行 + 行内下拉详情）。 */
export default function ToolCallCard({ call, result }: ToolCallCardProps) {
  const [open, setOpen] = useState(false)

  const name = call.tool_call.name
  const args = (call.tool_call.arguments ?? {}) as Record<string, unknown>
  const running = !result
  const failed = !!result && !result.ok
  const label = describe(name, args)
  const lineText = running ? `执行中 · ${label}` : failed ? `${label} · 失败` : label

  return (
    <div className="group/tool min-w-0">
      {/* 工具行：分类图标 + 动作描述（执行中扫光/失败暗红）+ hover 箭头 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="-mx-2 flex w-[calc(100%+16px)] items-center gap-2 rounded-[7px] border-0 bg-transparent px-2 py-1 text-left transition-colors duration-150 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <CategoryIcon category={categorize(name)} />
        <span
          className={`min-w-0 flex-1 truncate text-[12px] font-medium leading-[1.3] ${
            running
              ? 'text-transparent [animation:sa-shimmer_2.8s_linear_infinite_reverse] [background:linear-gradient(100deg,var(--sa-alias-label-tertiary)_0%,var(--sa-alias-label-tertiary)_42%,var(--sa-alias-label-primary)_50%,var(--sa-alias-label-tertiary)_58%,var(--sa-alias-label-tertiary)_100%)] [background-size:220%_100%] [-webkit-background-clip:text] [background-clip:text] [-webkit-text-fill-color:transparent]'
              : failed
                ? 'text-[var(--sa-alias-state-error-primary)]'
                : 'text-[var(--sa-alias-label-tertiary)]'
          }`}
        >
          {lineText}
        </span>
        <span
          aria-hidden="true"
          className={`flex h-3 w-3 shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)] transition-[transform,opacity] duration-200 ${
            open ? 'rotate-90 opacity-100' : 'opacity-0 group-hover/tool:opacity-100'
          }`}
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="m8 6 4 4-4 4" />
          </svg>
        </span>
      </button>

      {/* 行内下拉详情卡（浅卡片底分块） */}
      <div className={`sa-collapse ${open ? 'is-open' : ''}`}>
        <div className="sa-collapse-inner">
          <div
            className={`my-[2px] mb-1 flex flex-col gap-2.5 rounded-[8px] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-2.5 transition-[opacity,transform] duration-200 ${
              open ? 'translate-y-0 opacity-100' : '-translate-y-1 opacity-0'
            }`}
          >
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[10.5px] text-[var(--sa-alias-label-caption)]">工具</span>
              <code className="truncate text-[12px] text-[var(--sa-alias-label-secondary)]" style={{ fontFamily: 'var(--sa-font-code)' }}>
                {name}
              </code>
            </div>
            {Object.keys(args).length > 0 && (
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[10.5px] text-[var(--sa-alias-label-caption)]">参数</span>
                <pre
                  className="max-h-56 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed text-[var(--sa-alias-label-secondary)]"
                  style={{ fontFamily: 'var(--sa-font-code)' }}
                >
                  {JSON.stringify(args, null, 2)}
                </pre>
              </div>
            )}
            {result && (
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="flex items-center gap-2 text-[10.5px] text-[var(--sa-alias-label-caption)]">
                  结果
                  {result.truncated && (
                    <span className="rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-state-warn-tertiary)] px-1.5 py-px text-[10px] text-[var(--sa-alias-state-warn-label)]">
                      已截断
                    </span>
                  )}
                </span>
                {!result.ok && result.error && (
                  <span className="text-[12px] text-[var(--sa-alias-state-error-primary)]">{result.error}</span>
                )}
                <pre
                  className="max-h-56 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed text-[var(--sa-alias-label-secondary)]"
                  style={{ fontFamily: 'var(--sa-font-code)' }}
                >
                  {(result.content || '（无输出）') + (result.truncated ? '\n…（内容已截断）' : '')}
                </pre>
              </div>
            )}
            {running && (
              <span className="text-[11px] text-[var(--sa-alias-label-caption)]">等待结果…</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
