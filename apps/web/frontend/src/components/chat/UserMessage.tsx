/**
 * 用户消息：右对齐黑底白字气泡（primary 黑白极简主色）。
 */

interface UserMessageProps {
  /** 消息文本。 */
  text: string
}

/** 用户消息气泡组件。 */
export default function UserMessage({ text }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-[var(--sa-radius-lg)] bg-[var(--sa-alias-button-primary-fill)] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--sa-alias-label-primary-foreground)]">
        {text}
      </div>
    </div>
  )
}
