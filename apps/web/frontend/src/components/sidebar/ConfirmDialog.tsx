/**
 * 删除确认弹窗：结构与样式照抄 jiuwenswarm `multi-session/dialogs/Dialogs.tsx`
 * 的 `DialogShell` + `DeleteDialog` 与同目录 `dialogs.css`。
 *
 * 与参考实现的对应关系：
 * - `conversation-dialog`（fixed 全屏遮罩层、居中）→ 外层 fixed + 居中 flex；
 * - `__backdrop`（整屏按钮，点击即取消）→ 同；
 * - `__panel`（min(420px,100%)、padding 22、radius 18、xl 阴影）→ 同，色值换 `--sa-*`；
 * - `h2` 20px 标题 / `p` 13px 描述（对象名用 `__subject` 高亮并省略）/ `__actions`
 *   右对齐的「取消 + 危险确认」两按钮；
 * - `__error` 行内报错（不关弹窗，便于重试）。
 *
 * 遮罩与面板都在本组件内渲染（不做 portal）：jiuwen 也是就地渲染，本项目侧栏
 * 到 body 之间没有 transform/filter 祖先，`position: fixed` 不会被打破。
 */
import type { ReactNode } from 'react'

interface ConfirmDialogProps {
  /** 弹窗标题（如「删除工作区」）。 */
  title: string
  /** 描述内容（可含高亮的对象名）。 */
  description: ReactNode
  /** 确认按钮文案（如「删除」）。 */
  confirmLabel: string
  /** 确认操作进行中：禁用确认按钮。 */
  busy?: boolean
  /** 操作失败时的行内错误文案（有值才渲染）。 */
  error?: string | null
  /** 取消（点遮罩 / 关闭 / 取消按钮都走它）。 */
  onCancel: () => void
  /** 确认执行。 */
  onConfirm: () => void
}

/** 居中确认弹窗（危险操作默认按钮为红底）。 */
export default function ConfirmDialog({
  title,
  description,
  confirmLabel,
  busy = false,
  error = null,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
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
        <p className="mb-4 mt-1.5 text-[13px] leading-relaxed text-[var(--sa-alias-label-secondary)]">
          {description}
        </p>
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
            disabled={busy}
            onClick={onConfirm}
            className="h-[34px] rounded-[9px] border border-[var(--sa-alias-state-error-primary)] bg-[var(--sa-alias-state-error-primary)] px-3.5 text-[13px] text-white transition-opacity duration-[var(--sa-duration-fast)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
