/**
 * 应用路由表（纯函数，无副作用）：路径解析与构造。
 *
 * 路由模型照抄 jiuwenswarm `multi-session/routing/route.ts`（chat-new /
 * chat-session / not-found 三态），扩展了本项目的管理页路径：
 * - `/`、`/chat`、`/chat/new` → 新对话草稿态
 * - `/chat/<id>`             → 指定会话（URL 是会话选中态的唯一事实源）
 * - `/admin/<tab>`           → 管理后台三页签
 * - 其余                     → not-found
 */

/** 管理页页签（模型服务/助手管理/技能管理）。 */
export type AdminTab = 'models' | 'assistants' | 'skills'

/** 应用路由（判别联合，kind 即分支）。 */
export type AppRoute =
  | { kind: 'chat-new' }
  | { kind: 'chat-session'; sessionId: string }
  | { kind: 'admin'; tab: AdminTab }
  | { kind: 'not-found'; pathname: string }

/**
 * 解析 pathname 为应用路由。
 *
 * Args:
 *     pathname: location.pathname（尾斜杠容忍，多个也只去一层）。
 *
 * Returns:
 *     对应的 AppRoute；无法识别的路径返回 not-found（保留原路径）。
 */
export function parseAppRoute(pathname: string): AppRoute {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (path === '/' || path === '/chat' || path === '/chat/new') return { kind: 'chat-new' }
  const chatMatch = path.match(/^\/chat\/([^/]+)$/)
  if (chatMatch) return { kind: 'chat-session', sessionId: decodeURIComponent(chatMatch[1]) }
  const adminMatch = path.match(/^\/admin\/(models|assistants|skills)$/)
  if (adminMatch) return { kind: 'admin', tab: adminMatch[1] as AdminTab }
  return { kind: 'not-found', pathname }
}

/**
 * 构造路由对应的路径（navigate 写入 history 时用）。
 *
 * Args:
 *     route: 目标路由。
 *
 * Returns:
 *     可放入地址栏的路径字符串。
 */
export function appRoutePath(route: AppRoute): string {
  if (route.kind === 'chat-new') return '/chat/new'
  if (route.kind === 'chat-session') return `/chat/${encodeURIComponent(route.sessionId)}`
  if (route.kind === 'admin') return `/admin/${route.tab}`
  return route.pathname
}
