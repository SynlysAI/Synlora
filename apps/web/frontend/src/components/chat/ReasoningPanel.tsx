/**
 * 思考折叠面板（逐样式照抄 Jiuwen reasoning-panel）：
 * - 头部：灯泡图标 + "思考中/已思考"（执行中标题用亮带扫光，不转圈）+
 *   紧贴文字的展开箭头（hover 浮现、展开转 90°）
 * - 正文：无背景，左侧 2px 半透明竖线，字体暗一档，11.5px，
 *   max-h 220px 滚动，grid rows 平滑展开 + 渐入位移
 * - 流式进行中自动展开并跟随滚动；完成后延迟自动收起（用户手动切换过则尊重）
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
    <div className="group/think min-w-0">
      <button
        type="button"
        onClick={() => {
          userToggledRef.current = true
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        className="flex w-full items-center gap-[6px] border-0 bg-transparent p-0 py-[2px] text-left"
      >
        {/* 灯泡图标 */}
        <span className="flex h-[14px] w-[14px] shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)]">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M10 3.2a4.4 4.4 0 0 0-2.6 7.95v1.6a.9.9 0 0 0 .9.9h3.4a.9.9 0 0 0 .9-.9v-1.6A4.4 4.4 0 0 0 10 3.2z" />
            <path d="M8.3 16.2h3.4" />
          </svg>
        </span>
        {/* 标题：执行中亮带扫过灰字（shimmer），完成后静态暗字 */}
        <span
          className={`min-w-0 text-[12px] font-medium leading-[1.3] ${
            running
              ? 'text-transparent [animation:sa-shimmer_2.8s_linear_infinite_reverse] [background:linear-gradient(100deg,var(--sa-alias-label-tertiary)_0%,var(--sa-alias-label-tertiary)_42%,var(--sa-alias-label-primary)_50%,var(--sa-alias-label-tertiary)_58%,var(--sa-alias-label-tertiary)_100%)] [background-size:220%_100%] [-webkit-background-clip:text] [background-clip:text] [-webkit-text-fill-color:transparent]'
              : 'text-[var(--sa-alias-label-tertiary)]'
          }`}
        >
          {running ? '思考中' : '已思考'}
        </span>
        {/* 紧贴文字的展开箭头：hover 浮现，展开转 90° */}
        <span
          aria-hidden="true"
          className={`flex h-3 w-3 shrink-0 items-center justify-center text-[var(--sa-alias-label-tertiary)] transition-[transform,opacity] duration-200 ${
            open ? 'rotate-90 opacity-100' : 'opacity-0 group-hover/think:opacity-100'
          }`}
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="m8 6 4 4-4 4" />
          </svg>
        </span>
      </button>
      <div className={`sa-collapse ${open ? 'is-open' : ''}`}>
        <div className="sa-collapse-inner">
          <div
            ref={bodyRef}
            className={`my-[2px] ml-5 max-h-[220px] overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-[var(--sa-alias-border-l2)] py-1.5 pl-2.5 pr-2 text-[11.5px] leading-[1.55] text-[var(--sa-alias-label-secondary)] transition-[opacity,transform] duration-200 ${
              open ? 'translate-y-0 opacity-100' : '-translate-y-1 opacity-0'
            }`}
          >
            {body}
          </div>
        </div>
      </div>
    </div>
  )
}
