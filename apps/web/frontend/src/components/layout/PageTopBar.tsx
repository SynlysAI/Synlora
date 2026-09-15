/**
 * 全屏页面共用顶栏：返回工作台 + 页面标题 + 主题切换 + 用户名。
 *
 * 结构照 AdminLayout 原顶栏（返回工作台链接 + 分隔符 + 标题 + 右侧主题切换 +
 * 用户名）；管理后台与用户侧能力中心两个整页共用，保证两页顶栏视觉一致。
 */
import { appRoutePath } from '@/routing/route'
import { useRouterStore } from '@/routing/router'
import { useAuthStore } from '@/stores/auth'
import { useDarkTheme } from '@/utils/theme'

interface PageTopBarProps {
  /** 页面标题（顶栏分隔符右侧文字）。 */
  title: string
}

/** 全屏页面共用顶栏。 */
export default function PageTopBar({ title }: PageTopBarProps) {
  const user = useAuthStore((s) => s.user)
  const navigate = useRouterStore((s) => s.navigate)
  const [dark, toggleDark] = useDarkTheme()

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-bg-layer-1)] px-4">
      <a
        href={appRoutePath({ kind: 'chat-new' })}
        onClick={(e) => {
          e.preventDefault()
          navigate({ kind: 'chat-new' })
        }}
        className="flex items-center gap-1.5 rounded-[var(--sa-radius-sm)] px-2 py-1.5 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6.5 3 3 8l3.5 5M13 8H3.2" />
        </svg>
        返回工作台
      </a>
      <span className="text-[13px] text-[var(--sa-alias-border-l3)]" aria-hidden="true">
        |
      </span>
      <h1 className="text-[14px] font-medium tracking-tight">{title}</h1>

      <div className="flex-1" />
      <button
        type="button"
        title={dark ? '切换浅色主题' : '切换深色主题'}
        onClick={toggleDark}
        className="flex h-8 w-8 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        {dark ? (
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <circle cx="8" cy="8" r="3.25" />
            <path d="M8 1.5v1.4M8 13.1v1.4M1.5 8h1.4M13.1 8h1.4M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" />
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z" />
          </svg>
        )}
      </button>
      {user && (
        <span
          className="hidden max-w-[140px] truncate text-[13px] text-[var(--sa-alias-label-secondary)] sm:inline"
          title={user.username}
        >
          {user.username} / {user.role}
        </span>
      )}
    </header>
  )
}
