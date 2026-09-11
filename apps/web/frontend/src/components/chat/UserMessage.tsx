/**
 * 用户消息：右对齐轻量气泡（max-w 收窄 + 不对称圆角贴近对话流），
 * 配用户名首字头像建立身份（黑白极简点缀，不做大色块）。
 */
import { useAuthStore } from '@/stores/auth'
import Avatar from './Avatar'

interface UserMessageProps {
  /** 消息文本。 */
  text: string
}

/** 用户消息组件（轻量气泡 + 头像）。 */
export default function UserMessage({ text }: UserMessageProps) {
  const username = useAuthStore((s) => s.user?.username)
  return (
    <div className="flex items-end justify-end gap-2.5">
      <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-[var(--sa-radius-lg)] rounded-br-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-active)] px-3.5 py-2 text-[15px] leading-relaxed text-[var(--sa-alias-label-primary)]">
        {text}
      </div>
      <Avatar char={username?.[0] ?? '你'} variant="user" />
    </div>
  )
}
