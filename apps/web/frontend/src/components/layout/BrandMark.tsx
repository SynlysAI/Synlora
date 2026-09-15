/**
 * 品牌标记：Synlora 科学智能体图标（S 缎带 + 分子连接节点，public/logo.svg）。
 *
 * 与字标（`BrandWordmark`）配对：侧栏品牌区与消息流 turn 头部共用同一个标记，
 * 保证「平台身份」在界面上只出现一种图形。图标自带浅色圆角底板与品牌渐变，
 * 明暗主题下均按原样呈现（不随 token 反色）。
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
    <img
      src="/logo.svg"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      className={`shrink-0 rounded-[22%] ${className ?? ''}`}
    />
  )
}
