/**
 * 品牌标记：黑底圆角方块 + 单线「S」。
 *
 * 与字标（`BrandWordmark`）配对：侧栏品牌区与消息流 turn 头部共用同一个标记，
 * 保证「平台身份」在界面上只出现一种图形。
 *
 * 全部走语义 token——底色 `button-primary-fill`、笔画 `label-primary-foreground`，
 * 暗色主题下自动反色（黑底白字 ↔ 浅底深字），不需要单独的暗色资源。
 */

interface BrandMarkProps {
  /** 边长（px）。 */
  size?: number
  /** 附加类名（布局用）。 */
  className?: string
}

/** 品牌标记组件。 */
export default function BrandMark({ size = 28, className }: BrandMarkProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" className={className}>
      <rect x="1" y="1" width="18" height="18" rx="5" fill="var(--sa-alias-button-primary-fill)" />
      <path
        d="M13.6 7.4C13.6 5.7 11.9 4.5 10 4.5C8.1 4.5 6.5 5.6 6.5 7.1C6.5 8.5 7.7 9.2 10 9.7C12.3 10.2 13.7 10.9 13.7 12.5C13.7 14.1 12 15.5 10 15.5C8 15.5 6.3 14.3 6.3 12.6"
        stroke="var(--sa-alias-label-primary-foreground)"
        strokeWidth="1.7"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  )
}
