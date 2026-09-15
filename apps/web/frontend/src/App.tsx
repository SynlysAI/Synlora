/**
 * 应用根组件：认证守卫 + 路径路由（工作台 / 管理页 / not-found）。
 *
 * 路由模型照抄 jiuwenswarm：pathname 为唯一事实源（见 `routing/`），刷新/直开
 * `/chat/<id>` 恢复对应会话，`/` 与 `/chat` 一律归一化到 `/chat/new`（不恢复
 * 上次会话）。
 *
 * ready 前（init 校验 token 中）全屏 loading；未登录渲染登录页。
 */
import { useEffect } from 'react'
import { AdminLayout, GeneralAdmin, ModelsAdmin, AssistantsAdmin, SkillsAdmin, PluginsAdmin } from '@/components/admin'
import { AppShell } from '@/components/layout'
import { ConversationNotFound } from '@/components/chat'
import LoginPage from '@/components/LoginPage'
import { useRouterStore } from '@/routing/router'
import { useAuthStore } from '@/stores/auth'

/** 启动初始化期间的全屏 loading。 */
function FullscreenLoading() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--sa-alias-bg-base)]">
      <div className="text-[13px] text-[var(--sa-alias-label-caption)]">加载中…</div>
    </div>
  )
}

/** 应用根组件。 */
function App() {
  const ready = useAuthStore((s) => s.ready)
  const user = useAuthStore((s) => s.user)
  const init = useAuthStore((s) => s.init)
  const route = useRouterStore((s) => s.route)
  const navigate = useRouterStore((s) => s.navigate)

  useEffect(() => {
    void init()
  }, [init])

  // 路径归一化（照 jiuwen）：'/' 与 '/chat' 用 replace 改写为 /chat/new，
  // 不留历史记录——打开根地址永远落在「新对话」而不是恢复上次会话
  useEffect(() => {
    if (route.kind === 'chat-new' && location.pathname !== '/chat/new') {
      navigate({ kind: 'chat-new' }, { replace: true })
    }
  }, [navigate, route])

  if (!ready) return <FullscreenLoading />
  if (!user) return <LoginPage />
  // key=用户 id：切换账号时整树重挂载，杜绝任何跨账号残留
  if (route.kind === 'admin') {
    return (
      <AdminLayout key={user.sub} tab={route.tab}>
        {route.tab === 'general' ? (
          <GeneralAdmin />
        ) : route.tab === 'models' ? (
          <ModelsAdmin />
        ) : route.tab === 'assistants' ? (
          <AssistantsAdmin />
        ) : route.tab === 'skills' ? (
          <SkillsAdmin />
        ) : (
          <PluginsAdmin />
        )}
      </AdminLayout>
    )
  }
  if (route.kind === 'not-found') {
    return (
      <div className="flex h-dvh items-center justify-center bg-[var(--sa-alias-bg-base)]">
        <ConversationNotFound description="页面不存在或已被移除。" />
      </div>
    )
  }
  return <AppShell key={user.sub} />
}

export default App
