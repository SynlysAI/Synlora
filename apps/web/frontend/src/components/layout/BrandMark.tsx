/**
 * 品牌标记：Synlora 科学智能体图标（S 缎带 + 分子连接节点，public/logo.svg）。
 *
 * 与字标（`BrandWordmark`）配对：侧栏品牌区与消息流 turn 头部共用同一个标记，
 * 保证「平台身份」在界面上只出现一种图形。配色为**墨色系**（深墨→中灰的
 * 缎带 + 灰阶节点，白底圆角底板），与平台黑白基调及墨色字标同调。
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
      // ?v 与 favicon 同步升版：图标更新后强制浏览器重取（绕开 img 缓存）
      src="/logo.svg?v=5"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      className={`shrink-0 rounded-[22%] ${className ?? ''}`}
    />
  )
}
