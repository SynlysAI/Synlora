import { useEffect } from 'react'
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

/**
 * 应用根组件：认证守卫 + 三栏工作台。
 *
 * ready 前（init 校验 token 中）全屏 loading；未登录渲染登录页；
 * 已登录渲染 AppShell（管理页路由 Task 6 再定方案）。
 */
function App() {
  const ready = useAuthStore((s) => s.ready)
  const user = useAuthStore((s) => s.user)
  const init = useAuthStore((s) => s.init)

  useEffect(() => {
    void init()
  }, [init])

  if (!ready) return <FullscreenLoading />
  if (!user) return <LoginPage />
  return <AppShell />
}

export default App
