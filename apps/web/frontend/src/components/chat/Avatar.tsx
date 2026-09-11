/**
 * 助手头像：圆形浅底 + emoji 或首字符（消息流 turn 头部用）。
 *
 * 用户侧不展示头像（靠气泡右对齐辨认），故不带变体与尺寸档位。
 */

interface AvatarProps {
  /** 展示字符（emoji 或首字符）。 */
  char: string
}

/** 助手头像组件。 */
export default function Avatar({ char }: AvatarProps) {
  return (
    <div
      aria-hidden="true"
      className="flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-specific-bubble)] text-[15px] font-medium leading-none text-[var(--sa-alias-label-primary)]"
    >
      {char || '?'}
    </div>
  )
}
