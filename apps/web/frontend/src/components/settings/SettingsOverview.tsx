import { PageTopBar, ToastHost } from '@/components/layout'
import { appRoutePath, type AdminTab, type AppRoute } from '@/routing/route'
import { useRouterStore } from '@/routing/router'
import { useAuthStore } from '@/stores/auth'

type SettingLink = { label: string; route: AppRoute }

const ADMIN_LINKS: Array<{ label: string; tab: AdminTab }> = [
  { label: '常规设置', tab: 'general' },
  { label: '模型服务', tab: 'models' },
  { label: '助手管理', tab: 'assistants' },
  { label: 'Skills', tab: 'skills' },
  { label: 'Plugins', tab: 'plugins' },
  { label: 'MCP', tab: 'mcp' },
]

/** 设置总览：为既有个人、能力中心和管理深链接提供统一入口。 */
export default function SettingsOverview() {
  const user = useAuthStore((state) => state.user)
  const navigate = useRouterStore((state) => state.navigate)
  const personalLinks: SettingLink[] = [
    { label: '访问令牌', route: { kind: 'api-keys' } },
  ]
  const capabilityLinks: SettingLink[] = [
    { label: 'Expert', route: { kind: 'capabilities', capabilityKind: 'expert' } },
    { label: 'Skill', route: { kind: 'capabilities', capabilityKind: 'skill' } },
    { label: 'Plugin', route: { kind: 'capabilities', capabilityKind: 'plugin' } },
  ]
  const renderLinks = (links: SettingLink[]) => (
    <ul className="mt-2 divide-y divide-[var(--sa-alias-border-l1)] border-y border-[var(--sa-alias-border-l1)]">
      {links.map(({ label, route }) => (
        <li key={label}>
          <a
            href={appRoutePath(route)}
            onClick={(event) => {
              event.preventDefault()
              navigate(route)
            }}
            className="flex min-h-11 items-center justify-between gap-4 py-2 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors hover:text-[var(--sa-alias-label-primary)]"
          >
            <span>{label}</span>
            <span aria-hidden="true" className="text-[var(--sa-alias-label-caption)]">→</span>
          </a>
        </li>
      ))}
    </ul>
  )

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <PageTopBar title="设置" />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[880px] px-5 pb-12 pt-8 sm:px-8 sm:pt-10">
          <header className="mb-8">
            <h1 className="text-[24px] font-semibold leading-8">设置</h1>
            <p className="mt-1 text-[13px] text-[var(--sa-alias-label-tertiary)]">账户、能力与管理入口</p>
          </header>

          <section className="mb-8">
            <h2 className="text-[14px] font-semibold">个人设置</h2>
            <dl className="mt-3 grid gap-x-8 gap-y-2 border-t border-[var(--sa-alias-border-l1)] pt-3 text-[13px] sm:grid-cols-2">
              <div>
                <dt className="text-[12px] text-[var(--sa-alias-label-caption)]">账户</dt>
                <dd className="mt-0.5">{user?.username ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-[12px] text-[var(--sa-alias-label-caption)]">角色</dt>
                <dd className="mt-0.5">{user?.role === 'admin' ? '管理员' : '用户'}</dd>
              </div>
            </dl>
            {renderLinks(personalLinks)}
          </section>

          <section className="mb-8">
            <h2 className="text-[14px] font-semibold">能力中心</h2>
            <p className="mt-1 text-[12px] text-[var(--sa-alias-label-tertiary)]">已安装能力和可用扩展</p>
            {renderLinks(capabilityLinks)}
          </section>

          {user?.role === 'admin' && (
            <section>
              <h2 className="text-[14px] font-semibold">管理设置</h2>
              <p className="mt-1 text-[12px] text-[var(--sa-alias-label-tertiary)]">模型、助手、Skills、Plugins 与 MCP</p>
              {renderLinks(ADMIN_LINKS.map(({ label, tab }) => ({ label, route: { kind: 'admin', tab } })))}
            </section>
          )}
        </div>
      </main>
      <ToastHost />
    </div>
  )
}
