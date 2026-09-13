/**
 * 路由状态 store：`location.pathname` 是唯一事实源。
 *
 * 机制照抄 jiuwenswarm `multi-session/routing/useChatRoute.ts`（popstate 监听 +
 * pushState/replaceState 导航），只是状态容器换成 zustand——本项目所有跨组件
 * 状态都走 store，侧栏/管理页等深处组件可直接 `navigate` 而不必层层传回调。
 *
 * 注意：认证的 `#token=` 门户跳转（auth store）也用 replaceState 清 hash，
 * 与 pathname 路由互不干扰。
 */
import { create } from 'zustand'
import { appRoutePath, parseAppRoute, type AppRoute } from './route'

interface RouterState {
  /** 当前路由（初始从 pathname 解析，缺省新对话）。 */
  route: AppRoute
  /**
   * 导航到目标路由。
   *
   * @param next 目标路由。
   * @param options.replace 用 replaceState 替换当前历史记录（懒创建落库后用，
   *   后退键不回到 /chat/new 草稿，照 jiuwen）；目标与当前路径相同时不产生
   *   重复历史记录。
   */
  navigate: (next: AppRoute, options?: { replace?: boolean }) => void
}

export const useRouterStore = create<RouterState>((set) => ({
  route: parseAppRoute(location.pathname),

  navigate: (next, options) => {
    const path = appRoutePath(next)
    if (path !== location.pathname || options?.replace) {
      const method = options?.replace ? 'replaceState' : 'pushState'
      history[method](null, '', path)
    }
    set({ route: next })
  },
}))

// 浏览器前进/后退（以及 auth 清 hash 之外的任何历史变化）→ 重新解析路径
window.addEventListener('popstate', () => {
  useRouterStore.setState({ route: parseAppRoute(location.pathname) })
})
