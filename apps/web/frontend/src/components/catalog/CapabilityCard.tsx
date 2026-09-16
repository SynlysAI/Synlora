/**
 * 能力卡片（市场与「我的」共用）。
 *
 * 尺寸与视觉逐值照抄 jiuwenswarm `components/ui/PageCard/PageCard.css`：
 * 卡片 160px 高 / padding 24px / 圆角 16px / 1px 描边，hover 描边转透明并加
 * `0 8px 24px rgba(0,0,0,.08)` 阴影；左上 48×48 圆角 10px 头像；标题 14px/600；
 * 标签 chip 20px 高 12px 字；描述 12px 两行截断；右上 32×32 圆角 8px 动作按钮。
 *
 * 点卡片本体进详情页；动作按钮 stopPropagation（照 PageCard 的处理）。
 */
import type { MouseEvent, ReactNode } from 'react'

interface CapabilityCardProps {
  /** 卡片标题（能力名）。 */
  title: string
  /** 描述（两行截断）。 */
  description: string
  /** 头像 emoji（专家有则用）；缺省用标题首字母色块。 */
  avatar?: string
  /** 状态徽标（可多个）。 */
  badges?: ReactNode
  /** 右上动作按钮图标（null = 不渲染按钮）。 */
  actionIcon?: ReactNode
  /** 动作按钮的无障碍标签与 tooltip。 */
  actionLabel?: string
  /** 动作按钮点击（已内部 stopPropagation）。 */
  onAction?: () => void
  /** 动作进行中：按钮禁用。 */
  actionBusy?: boolean
  /** 点卡片本体。 */
  onClick: () => void
}

/** 能力卡片。 */
export default function CapabilityCard({
  title,
  description,
  avatar,
  badges,
  actionIcon,
  actionLabel,
  onAction,
  actionBusy = false,
  onClick,
}: CapabilityCardProps) {
  /** 动作按钮点击：不冒泡到卡片（否则会同时触发进详情）。 */
  const handleAction = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    onAction?.()
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        // 子元素按钮的按键会冒泡上来，不能当成卡片被激活（否则动作按钮的
        // Enter/Space 会被 preventDefault 掉，并误触发进详情）。
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className="flex h-40 cursor-pointer flex-col gap-[18px] rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-6 transition-[border-color,box-shadow] duration-[var(--sa-duration-base)] hover:border-transparent hover:shadow-[0_8px_24px_0_rgba(0,0,0,0.08)] focus-visible:border-transparent focus-visible:shadow-[0_8px_24px_0_rgba(0,0,0,0.08)] focus-visible:outline-none"
    >
      {/* 头部：头像 + 标题/标签 + 动作按钮 */}
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-[var(--sa-radius-md)] text-[20px] font-bold ${
            avatar ? '' : 'bg-[var(--sa-alias-state-business-tertiary)] text-[var(--sa-alias-link)]'
          }`}
        >
          {avatar || (title.trim().slice(0, 1).toUpperCase() || '?')}
        </span>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate text-sm font-semibold text-[var(--sa-alias-label-primary)]">
              {title}
            </span>
          </div>
          {/* 徽标行：卡片定高 160px，参考实现也是单行裁切，不换行溢出卡外 */}
          {badges && <div className="flex min-w-0 items-center gap-1.5 overflow-hidden">{badges}</div>}
        </div>

        {actionIcon && (
          <button
            type="button"
            title={actionLabel}
            aria-label={actionLabel}
            disabled={actionBusy}
            onClick={handleAction}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-[var(--sa-alias-interactive-bg-active)] text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] hover:text-[var(--sa-alias-link)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {actionIcon}
          </button>
        )}
      </div>

      {/* 描述：两行截断（照 PageCard 的 -webkit-line-clamp:2） */}
      <div className="line-clamp-2 text-xs leading-[22px] text-[var(--sa-alias-label-secondary)]">
        {description || '无描述'}
      </div>
    </div>
  )
}

/** 卡片徽标：尺寸照抄参考实现 PageCard 的 tag（20px 高 / 12px 字 / 圆角 sm）。 */
export function CardBadge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-5 shrink-0 items-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2 text-xs leading-5 text-[var(--sa-alias-label-primary)]">
      {children}
    </span>
  )
}
