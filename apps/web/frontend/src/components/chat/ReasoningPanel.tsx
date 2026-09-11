/**
 * 思考折叠面板（参考 Jiuwen 展示模式）：
 * 灯泡图标 + "思考中/已思考" + 展开箭头；流式进行中自动展开并跟随滚动，
 * 完成后延迟自动收起（用户手动切换过则尊重用户选择）。
 */
import { useEffect, useRef, useState } from 'react'

/** 完成后自动收起的延迟（ms）。 */
const AUTO_COLLAPSE_DELAY = 900

interface ReasoningPanelProps {
  /** 思考全文。 */
  text: string
  /** 是否仍在思考中（流式进行）。 */
  running: boolean
}

/** 思考折叠面板组件。 */
export default function ReasoningPanel({ text, running }: ReasoningPanelProps) {
  const [open, setOpen] = useState(running)
  const userToggledRef = useRef(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  const wasRunningRef = useRef(running)

  // running -> 完成：未手动切换过则延迟自动收起
  useEffect(() => {
    if (wasRunningRef.current && !running && !userToggledRef.current) {
      const timer = window.setTimeout(() => {
        if (!userToggledRef.current) setOpen(false)
      }, AUTO_COLLAPSE_DELAY)
      wasRunningRef.current = running
      return () => window.clearTimeout(timer)
    }
    wasRunningRef.current = running
    return undefined
  }, [running])

  // 展开且流式中：跟随滚动
  useEffect(() => {
    if (!open || !running) return
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [open, running, text])

  const body = text.replace(/\n{3,}/g, '\n\n').trim()
  if (!body) return null

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => {
          userToggledRef.current = true
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        className="flex items-center gap-1.5 py-1 text-[12.5px] transition-colors duration-[var(--sa-duration-fast)]"
      >
        {/* 灯泡图标 */}
        <svg
          width="13"
          height="13"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={running ? 'text-[var(--sa-alias-link)]' : 'text-[var(--sa-alias-label-tertiary)]'}
        >
          <path d="M10 3.2a4.4 4.4 0 0 0-2.6 7.95v1.6a.9.9 0 0 0 .9.9h3.4a.9.9 0 0 0 .9-.9v-1.6A4.4 4.4 0 0 0 10 3.2z" />
          <path d="M8.3 16.2h3.4" />
        </svg>
        <span
          className={
            running
              ? 'text-[var(--sa-alias-link)] [animation:sa-pulse_1.4s_ease-in-out_infinite]'
              : 'text-[var(--sa-alias-label-tertiary)]'
          }
        >
          {running ? '思考中' : '已思考'}
        </span>
        <svg
          width="10"
          height="10"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={`ml-0.5 text-[var(--sa-alias-label-tertiary)] transition-transform duration-[var(--sa-duration-base)] ${open ? 'rotate-90' : ''}`}
        >
          <path d="M6 3.5 10.5 8 6 12.5" />
        </svg>
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="mt-1 max-h-64 overflow-y-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-markdown-code-block)] px-3.5 py-2.5 text-[12.5px] leading-relaxed whitespace-pre-wrap text-[var(--sa-alias-label-secondary)]"
        >
          {body}
        </div>
      )}
    </div>
  )
}
