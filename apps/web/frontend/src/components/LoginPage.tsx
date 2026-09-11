/**
 * 登录页：居中卡片（DSH 风格），支持账号密码登录与开发模式快捷进入。
 *
 * dev 快捷入口仅在本地开发流程使用（sqlite + DEV_AUTH_TOKEN 模式），
 * 生产构建中 VITE_DEV_TOKEN 未定义时按钮隐藏。
 */
import { useState, type FormEvent } from 'react'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

/** dev 模式 token（.env 的 VITE_DEV_TOKEN 或默认 devtok，须与后端 DEV_AUTH_TOKEN 一致）。 */
const DEV_TOKEN = import.meta.env.VITE_DEV_TOKEN || 'devtok'

/** dev 快捷按钮是否展示（显式置 "0" 时隐藏）。 */
const SHOW_DEV_ENTRY = import.meta.env.VITE_DEV_TOKEN !== '0'

/** Logo 标记：与 AppShell 顶栏一致的黑色圆角方块 + S 弧线。 */
function LogoMark() {
  return (
    <svg width="28" height="28" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="1" y="1" width="18" height="18" rx="5" fill="var(--sa-alias-button-primary-fill)" />
      <path
        d="M12.9 6.3a4 4 0 1 0 1.3 5.2"
        stroke="var(--sa-alias-label-primary-foreground)"
        strokeWidth="1.8"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  )
}

/** 错误归一为用户可读文案。 */
function toMessage(err: unknown): string {
  if (err instanceof ApiError) {
    // 401 统一话术，不区分"用户不存在/密码错误"（后端本也不区分）
    return err.status === 401 ? '用户名或密码错误' : err.message
  }
  return '网络错误，请稍后重试'
}

/** 输入框样式常量。 */
const inputClass =
  'w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] ' +
  'bg-[var(--sa-specific-input-major)] px-3 py-2 text-[14px] text-[var(--sa-alias-label-primary)] ' +
  'placeholder:text-[var(--sa-alias-label-caption)] outline-none transition-colors ' +
  'duration-[var(--sa-duration-base)] focus:border-[var(--sa-alias-border-l4)]'

/** 登录页组件。 */
export default function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const loginWithToken = useAuthStore((s) => s.loginWithToken)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [devLoading, setDevLoading] = useState(false)
  const [error, setError] = useState('')

  /** 账号密码登录。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (loading || !username.trim() || !password) return
    setLoading(true)
    setError('')
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(toMessage(err))
    } finally {
      setLoading(false)
    }
  }

  /** 开发模式快捷进入：DEV_AUTH_TOKEN 直登。 */
  const handleDevEnter = async () => {
    if (devLoading) return
    setDevLoading(true)
    setError('')
    try {
      await loginWithToken(DEV_TOKEN)
    } catch (err) {
      setError(`开发模式进入失败：${toMessage(err)}`)
    } finally {
      setDevLoading(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--sa-alias-bg-base)] px-4">
      <div className="w-full max-w-[380px] rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-8 shadow-sm">
        <div className="flex flex-col items-center gap-2 pb-6">
          <LogoMark />
          <h1 className="text-[18px] font-medium tracking-tight text-[var(--sa-alias-label-primary)]">
            登录 Synlora
          </h1>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--sa-alias-label-secondary)]">用户名</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              placeholder="请输入用户名"
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--sa-alias-label-secondary)]">密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="请输入密码"
              className={inputClass}
            />
          </label>

          {error && (
            <div role="alert" className="text-[13px] text-[var(--sa-alias-state-error-primary)]">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-2 text-[14px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? '登录中…' : '登录'}
          </button>
        </form>

        {SHOW_DEV_ENTRY && (
          <button
            type="button"
            onClick={handleDevEnter}
            disabled={devLoading}
            className="mt-3 w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-3 py-2 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {devLoading ? '进入中…' : '开发模式进入'}
          </button>
        )}

        <p className="pt-5 text-center text-xs text-[var(--sa-alias-label-caption)]">
          通过 AI4MS 门户跳转可免登录
        </p>
      </div>
    </div>
  )
}
