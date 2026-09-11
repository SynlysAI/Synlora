/**
 * 认证状态 store：token 持久化、登录/登出、初始化恢复。
 *
 * token 生命周期：
 * - 门户跳转：URL hash `#token=xxx` 提取（AI4MS 免登录）
 * - 本地登录：POST /api/v1/auth/login
 * - 恢复校验：启动时 localStorage 有 token 则 GET /auth/me 校验
 * - 失效：任意请求 401 -> api/client 派发 sa:unauthorized -> 本 store 登出
 */
import { create } from 'zustand'
import type { AuthUser, LoginResponse, MeResponse } from '@/types'
import { api, getToken, setToken, UNAUTHORIZED_EVENT } from '@/api/client'
import { useSessionsStore } from './sessions'
import { useChatStore } from './chat'

/** URL hash 中门户 token 的提取正则。 */
const HASH_TOKEN_RE = /token=([^&]+)/

interface AuthState {
  /** 当前 token（null 未登录）。 */
  token: string | null
  /** 当前用户信息（null 未登录）。 */
  user: AuthUser | null
  /** init() 是否完成（启动 loading 依据）。 */
  ready: boolean
  /** 启动初始化：提取 hash token / 校验 localStorage token。 */
  init: () => Promise<void>
  /** 账号密码登录。 */
  login: (username: string, password: string) => Promise<void>
  /** 用既有 token 登录并经 me 校验（dev 快捷入口等）。 */
  loginWithToken: (token: string) => Promise<void>
  /** 登出：清 token 与用户态。 */
  logout: () => void
}

/** 校验 token 并加载用户信息；失败抛错且不残留 token。 */
async function fetchMe(token: string): Promise<AuthUser> {
  setToken(token)
  try {
    const me = await api<MeResponse>('/api/v1/auth/me')
    return { sub: me.sub, username: me.username, role: me.role }
  } catch (err) {
    setToken(null)
    throw err
  }
}

/** init 的去重 promise（StrictMode 双调用 / 多处触发只跑一次）。 */
let initPromise: Promise<void> | null = null

/** init 实际逻辑：hash token 提取 -> 本地 token 校验 -> 落定未登录态。 */
async function initAuth(set: (partial: Partial<AuthState>) => void): Promise<void> {
  // 1. 门户跳转：hash 中的 token 优先（一次性消费后清 hash）
  const hashMatch = location.hash.match(HASH_TOKEN_RE)
  if (hashMatch) {
    const token = decodeURIComponent(hashMatch[1])
    history.replaceState(null, '', location.pathname + location.search)
    try {
      const user = await fetchMe(token)
      set({ token, user, ready: true })
      return
    } catch {
      // 门户 token 无效：落回本地 token / 登录页（fetchMe 已清 token）
    }
  }
  // 2. 本地持久化 token 校验；无 token 或失效则保持未登录
  const saved = getToken()
  if (saved) {
    try {
      const user = await fetchMe(saved)
      set({ token: saved, user, ready: true })
      return
    } catch {
      setToken(null)
    }
  }
  set({ token: null, user: null, ready: true })
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  ready: false,

  init: () => {
    initPromise ??= initAuth((partial) => set(partial))
    return initPromise
  },

  login: async (username, password) => {
    const resp = await api<LoginResponse>('/api/v1/auth/login', {
      method: 'POST',
      body: { username, password },
    })
    setToken(resp.token)
    set({
      token: resp.token,
      user: { sub: '', username: resp.username, role: resp.role },
    })
    // login 响应不含 sub：补查 me 取全（失败则回滚为未登录）
    try {
      const me = await api<MeResponse>('/api/v1/auth/me')
      set({ user: { sub: me.sub, username: me.username, role: me.role } })
      resetWorkspaceStores()
    } catch {
      set({ token: null, user: null })
    }
  },

  loginWithToken: async (token) => {
    // fetchMe 失败时已清理 token，这里只需捕获后保持未登录
    const user = await fetchMe(token)
    set({ token, user, ready: true })
    resetWorkspaceStores()
  },

  logout: () => {
    setToken(null)
    set({ token: null, user: null })
    resetWorkspaceStores()
  },
}))

/** 清空与会话相关的 store（切换账号时防止上一账号的会话被新账号引用）。 */
function resetWorkspaceStores() {
  useSessionsStore.getState().resetAll()
  useChatStore.getState().reset()
}

// 任意 API 请求 401（token 过期/被改坏）：自动登出回登录页
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  useAuthStore.getState().logout()
  useAuthStore.setState({ ready: true })
})
