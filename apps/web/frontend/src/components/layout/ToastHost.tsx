/**
 * 全局 toast 渲染宿主：固定右下角栈式展示（AppShell 根部挂载一次）。
 */
import { useToastsStore } from '@/stores/toasts'

/** toast 类型 → 样式（错误红底反白，其余走暗色 toast 底）。 */
const KIND_CLASS: Record<string, string> = {
  error: 'bg-[var(--sa-alias-state-error-primary)] text-white',
  success: 'bg-[var(--sa-alias-toast-bg)] text-[var(--sa-alias-bg-base)]',
  info: 'bg-[var(--sa-alias-toast-bg)] text-[var(--sa-alias-bg-base)]',
}

/** Toast 宿主组件。 */
export function ToastHost() {
  const toasts = useToastsStore((s) => s.toasts)
  const dismiss = useToastsStore((s) => s.dismiss)

  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-72 flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => dismiss(t.id)}
          className={`pointer-events-auto rounded-[var(--sa-radius-md)] px-3 py-2 text-left text-[13px] leading-snug shadow-lg ${KIND_CLASS[t.kind]}`}
        >
          {t.text}
        </button>
      ))}
    </div>
  )
}
