/**
 * 应用根组件：认证守卫 + hash 路由（工作台 / 管理页）。
 *
 * 简易 hash 路由（无 react-router 依赖）：
 * - 默认（含空 hash）→ AppShell 三栏工作台
 * - #/admin/models | #/admin/assistants → AdminLayout 管理页（内部再守卫 admin 角色）
 *
 * ready 前（init 校验 token 中）全屏 loading；未登录渲染登录页。
 */
import { useEffect, useState } from 'react'
import { AdminLayout, ModelsAdmin, AssistantsAdmin, type AdminTab } from '@/components/admin'
import { AppShell } from '@/components/layout'
import LoginPage from '@/components/LoginPage'
import { useAuthStore } from '@/stores/auth'

/** 启动初始化期间的全屏 loading。 */
function FullscreenLoading() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--sa-alias-bg-base)]">
      <div className="text-[13px] text-[var(--sa-alias-label-caption)]">加载中…</div>
    </div>
  )
}

/** 管理页 hash → 页签映射（未匹配的 hash 一律回工作台）。 */
function adminTabFromHash(hash: string): AdminTab | null {
  if (hash === '#/admin/models') return 'models'
  if (hash === '#/admin/assistants') return 'assistants'
  return null
}

/** 订阅 location.hash 变化（hash 路由的唯一状态源）。 */
function useHash(): string {
  const [hash, setHash] = useState(() => location.hash)
  useEffect(() => {
    const onChange = () => setHash(location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return hash
}

/** 应用根组件。 */
function App() {
  const ready = useAuthStore((s) => s.ready)
  const user = useAuthStore((s) => s.user)
  const init = useAuthStore((s) => s.init)
  const adminTab = adminTabFromHash(useHash())

  useEffect(() => {
    void init()
  }, [init])

  if (!ready) return <FullscreenLoading />
  if (!user) return <LoginPage />
  // key=用户 id：切换账号时整树重挂载，杜绝任何跨账号残留
  if (adminTab) {
    return (
      <AdminLayout key={user.sub} tab={adminTab}>
        {adminTab === 'models' ? <ModelsAdmin /> : <AssistantsAdmin />}
      </AdminLayout>
    )
  }
  return <AppShell key={user.sub} />
}

export default App
