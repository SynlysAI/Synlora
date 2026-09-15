/**
 * 管理页布局：顶栏（返回工作台 + 管理后台 + 主题切换 + 用户名）+ 左导航 + 内容区。
 *
 * 路径路由 /admin/general | /admin/models | /admin/assistants | /admin/skills | /admin/plugins
 * 由 App.tsx 解析后传入 tab；滚动只发生在右侧内容区（照 DSH SettingsRoot）；
 * 非 admin 渲染无权限页（菜单入口已隐藏，此处为直链访问兜底）。
 */
import type { ReactNode } from 'react'
import { ToastHost } from '@/components/layout'
import { appRoutePath, type AdminTab } from '@/routing/route'
import { useRouterStore } from '@/routing/router'
import { useAuthStore } from '@/stores/auth'
import { useDarkTheme } from '@/utils/theme'

/** 页签定义：key + 路由 + 展示名。 */
const TABS: Array<{ key: AdminTab; label: string }> = [
  { key: 'general', label: '常规' },
  { key: 'models', label: '模型服务' },
  { key: 'assistants', label: '助手管理' },
  { key: 'skills', label: '技能管理' },
  { key: 'plugins', label: '插件' },
]

/** 无权限页：仅管理员可访问的直链兜底。 */
function NoPermission() {
  const navigate = useRouterStore((s) => s.navigate)
  return (
    <div className="flex flex-1 items-center justify-center p-6">
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
          <rect x="3" y="7" width="10" height="6.5" rx="1.5" />
          <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
        </svg>
        <h2 className="text-[15px] font-medium text-[var(--sa-alias-label-primary)]">无权限访问</h2>
        <p className="text-[13px] text-[var(--sa-alias-label-tertiary)]">
          管理页仅对管理员开放，如需管理模型服务与助手请联系管理员开通权限。
        </p>
        <a
          href="/chat/new"
          onClick={(e) => {
            e.preventDefault()
            navigate({ kind: 'chat-new' })
          }}
          className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-1.5 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)]"
        >
          返回工作台
        </a>
      </div>
    </div>
  )
}

interface AdminLayoutProps {
  /** 当前页签（App.tsx 按 hash 解析）。 */
  tab: AdminTab
  /** 页签内容（GeneralAdmin / ModelsAdmin / AssistantsAdmin / SkillsAdmin / PluginsAdmin）。 */
  children: ReactNode
}

/** 管理页布局组件（独立于 AppShell 的全屏壳）。 */
export default function AdminLayout({ tab, children }: AdminLayoutProps) {
  const user = useAuthStore((s) => s.user)
  const navigate = useRouterStore((s) => s.navigate)
  const [dark, toggleDark] = useDarkTheme()

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      {/* 顶栏 */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-bg-layer-1)] px-4">
        <a
          href="/chat/new"
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
        <h1 className="text-[14px] font-medium tracking-tight">管理后台</h1>

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

      {/* 内容区：左导航 + 右侧内容（滚动只在右侧，照 DSH SettingsRoot） */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {user?.role === 'admin' ? (
          <>
            <nav
              aria-label="管理页切换"
              className="flex w-[188px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--sa-alias-border-l1)] p-3"
            >
              {TABS.map((t) => (
                <a
                  key={t.key}
                  href={appRoutePath({ kind: 'admin', tab: t.key })}
                  aria-current={t.key === tab ? 'page' : undefined}
                  onClick={(e) => {
                    e.preventDefault()
                    navigate({ kind: 'admin', tab: t.key })
                  }}
                  className={`flex h-10 items-center truncate rounded-[var(--sa-radius-md)] px-3 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
                    t.key === tab
                      ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] font-medium text-[var(--sa-alias-label-primary)]'
                      : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]'
                  }`}
                >
                  {t.label}
                </a>
              ))}
            </nav>
            <main className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-4xl px-6 py-6">{children}</div>
            </main>
          </>
        ) : (
          <main className="min-h-0 flex-1 overflow-y-auto">
            <NoPermission />
          </main>
        )}
      </div>

      {/* 全局 toast */}
      <ToastHost />
    </div>
  )
}
