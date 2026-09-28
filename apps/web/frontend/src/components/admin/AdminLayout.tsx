/**
 * 管理页布局：顶栏（PageTopBar：返回工作台 + 管理后台 + 主题切换 + 用户名）
 * + 左导航 + 内容区。
 *
 * 路径路由 /admin/general | /admin/models | /admin/assistants | /admin/skills | /admin/plugins
 * 由 App.tsx 解析后传入 tab；滚动只发生在右侧内容区（照 DSH SettingsRoot）；
 * 非 admin 渲染无权限页（菜单入口已隐藏，此处为直链访问兜底）。
 */
import type { ReactNode } from 'react'
import { PageTopBar, ToastHost } from '@/components/layout'
import { appRoutePath, type AdminTab } from '@/routing/route'
import { useRouterStore } from '@/routing/router'
import { useAuthStore } from '@/stores/auth'

/** 页签定义：key + 路由 + 展示名。 */
const TABS: Array<{ key: AdminTab; label: string }> = [
  { key: 'general', label: '常规' },
  { key: 'models', label: '模型服务' },
  { key: 'assistants', label: '助手管理' },
  { key: 'skills', label: '技能管理' },
  { key: 'plugins', label: '扩展管理' },
  { key: 'mcp', label: 'MCP 服务' },
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

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      {/* 顶栏（与管理页外的整页共用） */}
      <PageTopBar title="管理后台" />

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
                  className={`flex h-10 items-center rounded-[var(--sa-radius-md)] px-3 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
                    t.key === tab
                      ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] font-medium text-[var(--sa-alias-label-primary)]'
                      : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-specific-sidebar-nav-item-hover)]'
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">{t.label}</span>
                </a>
              ))}
            </nav>
            <main className="min-h-0 flex-1 overflow-y-auto">
              {/* 内容线照参考项目的 .page-shell（限宽 1400、两侧 48px）+ .page-content
                  的上下留白，与用户侧能力中心保持一致，两个整页切换时骨架不割裂 */}
              <div className="mx-auto w-full max-w-[1400px] px-12 pt-8 pb-10">{children}</div>
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
