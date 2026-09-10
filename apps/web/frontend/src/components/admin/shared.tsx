/**
 * 管理页共享 UI 组件：模态框、开关、加载转圈、徽标、行内错误提示。
 *
 * 仅导出组件（表单样式常量与 errorText 见 ./form.ts）；
 * ModelsAdmin 与 AssistantsAdmin 共用，保持两页交互一致。
 */
import type { ReactNode } from 'react'

/** 12px 旋转转圈。 */
export function Spinner({ className = '' }: { className?: string }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 16 16"
      fill="none"
      className={`animate-spin ${className}`}
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <path d="M14 8A6 6 0 0 0 8 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

interface SwitchProps {
  /** 当前开/关。 */
  checked: boolean
  /** 无障碍名称（列表行内切换语义）。 */
  label: string
  /** 切换回调（组件不做请求，由父级处理）。 */
  onChange: (next: boolean) => void
  /** 禁用（请求进行中）。 */
  disabled?: boolean
}

/** 开关控件（role=switch）：管理列表行内布尔切换。 */
export function Switch({ checked, label, onChange, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-[var(--sa-radius-full)] transition-colors duration-[var(--sa-duration-base)] disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? 'bg-[var(--sa-alias-button-primary-fill)]' : 'bg-[var(--sa-alias-border-l3)]'
      }`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-[var(--sa-radius-full)] bg-white shadow-sm transition-[left] duration-[var(--sa-duration-base)] ${
          checked ? 'left-[18px]' : 'left-0.5'
        }`}
      />
    </button>
  )
}

interface ModalProps {
  /** 模态标题。 */
  title: string
  /** 关闭回调（遮罩点击 / 关闭按钮）。 */
  onClose: () => void
  /** 内容（表单）。 */
  children: ReactNode
}

/** 居中模态框：遮罩点击关闭，内容区滚动防溢出。 */
export function Modal({ title, onClose, children }: ModalProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--sa-alias-bg-mask-1)] p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[85dvh] w-full max-w-[520px] overflow-y-auto rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4">
          <h2 className="text-[15px] font-medium text-[var(--sa-alias-label-primary)]">{title}</h2>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
              <path d="m4 4 8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** 灰底小徽标（"内置"/"已配置密钥"等中性标记）。 */
export function GrayBadge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-markdown-tag)] px-1.5 py-px text-[10px] leading-4 text-[var(--sa-alias-label-tertiary)]">
      {children}
    </span>
  )
}

/** 行内错误提示（表单提交失败 / 测试失败共用）。 */
export function FormError({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="text-[13px] text-[var(--sa-alias-state-error-primary)]">
      {children}
    </div>
  )
}
