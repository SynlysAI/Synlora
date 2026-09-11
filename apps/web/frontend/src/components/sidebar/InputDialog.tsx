/**
 * 输入弹窗（命名类操作：新建/重命名工作区）。
 *
 * 壳结构与 ConfirmDialog 同源，均照抄 jiuwenswarm `multi-session/dialogs/Dialogs.tsx`
 * 的 `DialogShell` 与同目录 `dialogs.css`：420px 面板 / 22px 内距 / 18px 圆角、
 * 整屏遮罩点击取消、右上角关闭钮、右对齐「取消 + 确认」两按钮、行内报错不关弹窗。
 * 输入框与主确认按钮同样按该 css 映射：input 40px 高 / radius 10 / 面板同底色，
 * `is-primary` 即 label-primary 反白底（对应 `--sa-alias-button-primary-*`）。
 */
import { useEffect, useRef, useState } from 'react'

interface InputDialogProps {
  /** 弹窗标题（如「新建工作区」）。 */
  title: string
  /** 输入框占位提示。 */
  placeholder?: string
  /** 预填值（重命名场景），打开时自动聚焦并全选便于直接覆盖。 */
  initialValue?: string
  /** 确认按钮文案（如「创建」）。 */
  confirmLabel: string
  /** 确认操作进行中：禁用确认按钮。 */
  busy?: boolean
  /** 操作失败时的行内错误文案（有值才渲染，不关弹窗便于重试）。 */
  error?: string | null
  /** 取消（点遮罩 / 关闭 / 取消按钮 / Esc 都走它）。 */
  onCancel: () => void
  /** 确认执行（入参为 trim 后的输入值；空值时按钮禁用不会触发）。 */
  onConfirm: (value: string) => void
}

/** 居中输入弹窗（标题 + 输入框 + 取消/主确认按钮）。 */
export default function InputDialog({
  title,
  placeholder,
  initialValue = '',
  confirmLabel,
  busy = false,
  error = null,
  onCancel,
  onConfirm,
}: InputDialogProps) {
  const [value, setValue] = useState(initialValue)
  const inputRef = useRef<HTMLInputElement>(null)
  const trimmed = value.trim()

  // 打开即聚焦并全选预填值
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.focus()
    el.select()
  }, [])

  /** Enter 确认 / Esc 取消；IME 组合中的 Enter（选词）不当作确认。 */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing && trimmed && !busy) {
      onConfirm(trimmed)
    } else if (e.key === 'Escape') {
      onCancel()
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center p-5"
    >
      {/* 整屏遮罩：点击即取消（按钮语义，键盘也能触发） */}
      <button
        type="button"
        aria-label="取消"
        onClick={onCancel}
        className="absolute inset-0 cursor-default border-0 bg-[var(--sa-alias-bg-mask-1)]"
      />
      <div className="relative w-[min(420px,100%)] rounded-[18px] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-[22px] shadow-2xl">
        <button
          type="button"
          aria-label="关闭"
          onClick={onCancel}
          className="absolute right-4 top-4 flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>

        <h2 className="m-0 pr-8 text-[20px] font-medium text-[var(--sa-alias-label-primary)]">
          {title}
        </h2>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label={title}
          className="mt-4 h-10 w-full rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
        />
        {error && (
          <div className="mt-2.5 text-xs text-[var(--sa-alias-state-error-primary)]">{error}</div>
        )}

        <div className="mt-4 flex justify-end gap-2.5">
          <button
            type="button"
            onClick={onCancel}
            className="h-[34px] rounded-[9px] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3.5 text-[13px] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            取消
          </button>
          <button
            type="button"
            disabled={busy || !trimmed}
            onClick={() => onConfirm(trimmed)}
            className="h-[34px] rounded-[9px] border-0 bg-[var(--sa-alias-button-primary-fill)] px-3.5 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
