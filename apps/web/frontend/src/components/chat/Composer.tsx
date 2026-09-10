/**
 * 底部输入区：圆角大输入框（自适应高度，max 6 行）、Enter 发送 /
 * Shift+Enter 换行（IME 组合中的 Enter 不发送）、streaming 时输入禁用且
 * 发送按钮变停止按钮、错误提示条（含 429 话术）、输入框左下角会话级
 * 模型选择器（ModelPicker）。
 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useChatStore } from '@/stores/chat'
import ModelPicker from './ModelPicker'

/** 输入框最大高度（px，约 6 行：行高 22 + 上下 padding 12×2 + 边框）。 */
const MAX_INPUT_HEIGHT = 148

/** 输入框组件（中间列下部）。 */
export default function Composer() {
  const streaming = useChatStore((s) => s.streaming)
  const error = useChatStore((s) => s.error)
  const send = useChatStore((s) => s.send)
  const stop = useChatStore((s) => s.stop)
  const clearError = useChatStore((s) => s.clearError)
  const [value, setValue] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  // 自适应高度：先归零再量 scrollHeight，钳制到最大值
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`
    el.style.overflowY = el.scrollHeight > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
  }, [value])

  /** 发送当前输入（空串/流式中忽略）。 */
  const submit = () => {
    const text = value.trim()
    if (!text || streaming) return
    setValue('')
    void send(text)
  }

  /** Enter 发送 / Shift+Enter 换行；中文输入法组合中的 Enter 不发送。 */
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  };

  return (
    <div className="shrink-0 px-4 pb-4 sm:px-6">
      <div className="mx-auto max-w-3xl">
        {/* 错误提示条（429 / 网络错误等；消息保留可重发） */}
        {error && (
          <div
            role="alert"
            className="mb-2 flex items-start gap-2 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-state-error-primary)] bg-[var(--sa-alias-interactive-bg-hover-danger)] px-3 py-2 text-[13px] text-[var(--sa-alias-state-error-primary)]"
          >
            <span className="min-w-0 flex-1 break-words">{error}</span>
            <button
              type="button"
              aria-label="关闭错误提示"
              onClick={clearError}
              className="shrink-0 rounded-[var(--sa-radius-sm)] p-0.5 transition-colors hover:bg-[var(--sa-alias-interactive-bg-hover)]"
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </button>
          </div>
        )}

        <div className="flex items-end gap-2 rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-3.5 py-2.5 transition-colors duration-[var(--sa-duration-base)] focus-within:border-[var(--sa-alias-border-l4)]">
          <textarea
            ref={ref}
            rows={1}
            value={value}
            disabled={streaming}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={streaming ? '回复生成中…' : '输入消息，Enter 发送，Shift+Enter 换行'}
            aria-label="消息输入框"
            className="max-h-[148px] min-h-[24px] w-full resize-none bg-transparent py-0.5 text-[14px] leading-[22px] text-[var(--sa-alias-label-primary)] placeholder:text-[var(--sa-alias-label-caption)] outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />

          {/* 发送 / 停止按钮 */}
          {streaming ? (
            <button
              type="button"
              aria-label="停止生成"
              title="停止生成"
              onClick={() => void stop()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-button-primary-fill)] text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)]"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              aria-label="发送"
              title="发送（Enter）"
              onClick={submit}
              disabled={!value.trim()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-button-primary-fill)] text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M8 13V3M3.8 7.2 8 3l4.2 4.2" />
              </svg>
            </button>
          )}
        </div>

        {/* 输入框左下角：会话级模型选择器（无会话/未配置模型时自行隐藏） */}
        <div className="mt-1.5 flex items-center">
          <ModelPicker />
        </div>
      </div>
    </div>
  )
}
