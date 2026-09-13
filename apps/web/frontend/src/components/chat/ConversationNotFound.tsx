/**
 * 会话/页面不存在提示卡：标题 + 说明 + 「新建对话」按钮（照 jiuwen 的
 * multiSession.notFound 视图：居中卡片，按钮回新对话草稿态）。
 */
import { useRouterStore } from '@/routing/router'

interface ConversationNotFoundProps {
  /** 说明文案（路由 not-found 与会话缺失两种场景措辞不同）。 */
  description: string
}

/** 不存在提示卡组件（App 根路由与 AppShell 会话缺失共用）。 */
export function ConversationNotFound({ description }: ConversationNotFoundProps) {
  const navigate = useRouterStore((s) => s.navigate)

  return (
    <div className="flex max-w-[360px] flex-col items-center gap-3 rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-8 py-10 text-center">
      <svg
        width="28"
        height="28"
        viewBox="0 0 16 16"
        fill="none"
        stroke="var(--sa-alias-label-caption)"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="7" cy="7" r="4.5" />
        <path d="m10.5 10.5 3 3" />
      </svg>
      <h2 className="text-[15px] font-medium text-[var(--sa-alias-label-primary)]">对话不存在</h2>
      <p className="text-[13px] text-[var(--sa-alias-label-tertiary)]">{description}</p>
      <button
        type="button"
        onClick={() => navigate({ kind: 'chat-new' })}
        className="mt-1 rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-1.5 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)]"
      >
        新建对话
      </button>
    </div>
  )
}
