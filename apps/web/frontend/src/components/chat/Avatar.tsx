/**
 * 头像：圆形底 + emoji 或首字符（助手与用户共用）。
 *
 * 助手用 bubble 浅调底 + secondary 字色（安静），用户用 primary 黑白极简。
 */

interface AvatarProps {
  /** 展示字符（emoji 或首字符）。 */
  char: string
  /** 尺寸档位。 */
  size?: 'sm' | 'md'
  /** 变体：assistant（浅底）/ user（黑白极简）。 */
  variant?: 'assistant' | 'user'
}

/** 头像组件。 */
export default function Avatar({ char, size = 'md', variant = 'assistant' }: AvatarProps) {
  const box = size === 'sm' ? 'h-6 w-6 text-[12px]' : 'h-8 w-8 text-[15px]'
  const tone =
    variant === 'user'
      ? 'bg-[var(--sa-alias-interactive-bg-active)] text-[var(--sa-alias-label-primary)]'
      : 'bg-[var(--sa-specific-bubble)] text-[var(--sa-alias-label-primary)]'
  return (
    <div
      aria-hidden="true"
      className={`flex shrink-0 select-none items-center justify-center rounded-[var(--sa-radius-full)] font-medium leading-none ${box} ${tone}`}
    >
      {char || '?'}
    </div>
  )
}
