/**
 * 用户消息：右对齐轻量气泡（max-w 收窄 + 不对称圆角贴近对话流）。
 * 不配头像——对话里只有助手一侧有身份标识，用户侧靠气泡右对齐即可辨认。
 */
interface UserMessageProps {
  /** 消息文本。 */
  text: string
}

/** 用户消息组件（轻量气泡）。 */
export default function UserMessage({ text }: UserMessageProps) {
  return (
    <div className="flex items-end justify-end">
      <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-[var(--sa-radius-lg)] rounded-br-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-active)] px-3.5 py-2 text-[15px] leading-relaxed text-[var(--sa-alias-label-primary)]">
        {text}
      </div>
    </div>
  )
}
