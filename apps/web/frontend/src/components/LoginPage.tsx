/**
 * 登录页：居中卡片（DSH 风格），支持双身份源登录与开发模式快捷进入。
 *
 * 身份源切换：AI4MS 账号（用户名 + 密码，POST /auth/login）与 Plane 账号
 * （对外品牌名 OpenEVA；邮箱 + 密码，POST /auth/plane-login，后端代理 Plane
 * 验证后签发本方 token）。
 *
 * dev 快捷入口仅在 Vite dev 模式且显式配置 VITE_DEV_TOKEN 时展示（sqlite +
 * DEV_AUTH_TOKEN 模式），生产构建永不渲染。
 */
import { useState, type FormEvent } from 'react'
import { ApiError } from '@/api/client'
import { VersionTag } from '@/components/layout'
import BrandMark from '@/components/layout/BrandMark'
import { useAuthStore } from '@/stores/auth'

/** dev 模式 token（仅本地显式配置才有效，须与后端 DEV_AUTH_TOKEN 一致）。 */
const DEV_TOKEN = import.meta.env.DEV && import.meta.env.VITE_DEV_TOKEN
  ? import.meta.env.VITE_DEV_TOKEN
  : ''

/** dev 快捷按钮是否展示（生产构建与未配置 token 时均隐藏）。 */
const SHOW_DEV_ENTRY = import.meta.env.DEV && !!import.meta.env.VITE_DEV_TOKEN

/** 身份源：ai4ms = 组织共享 AI4MS 账号；plane = Plane 门户账号。 */
type LoginMode = 'ai4ms' | 'plane'

/** 错误归一为用户可读文案（按身份源区分 401 话术）。 */
function toMessage(err: unknown, mode: LoginMode): string {
  if (err instanceof ApiError) {
    // 401 统一话术，不区分"用户不存在/密码错误"（后端本也不区分）
    return err.status === 401
      ? mode === 'plane'
        ? '邮箱或密码错误'
        : '用户名或密码错误'
      : err.message
  }
  return '网络错误，请稍后重试'
}

/** 输入框样式常量。 */
const inputClass =
  'w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] ' +
  'bg-[var(--sa-specific-input-major)] px-3 py-2 text-[14px] text-[var(--sa-alias-label-primary)] ' +
  'placeholder:text-[var(--sa-alias-label-caption)] outline-none transition-colors ' +
  'duration-[var(--sa-duration-base)] focus:border-[var(--sa-alias-border-l4)]'

/** 身份源分段按钮样式常量（激活段用悬浮底 + 主文字，非激活用次级文字）。 */
const segmentClass = (active: boolean): string =>
  'flex-1 rounded-[calc(var(--sa-radius-sm)-2px)] px-2 py-1.5 text-[13px] transition-colors ' +
  'duration-[var(--sa-duration-base)] ' +
  (active
    ? 'bg-[var(--sa-alias-interactive-bg-hover)] text-[var(--sa-alias-label-primary)]'
    : 'text-[var(--sa-alias-label-secondary)] hover:text-[var(--sa-alias-label-primary)]')

/** 登录页组件。 */
export default function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const loginWithPlane = useAuthStore((s) => s.loginWithPlane)
  const loginWithToken = useAuthStore((s) => s.loginWithToken)
  const [mode, setMode] = useState<LoginMode>('ai4ms')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [devLoading, setDevLoading] = useState(false)
  const [error, setError] = useState('')

  /** 表单是否可提交（plane 模式要求邮箱格式）。 */
  const formValid =
    mode === 'plane'
      ? !!username.trim() && username.includes('@') && !!password
      : !!username.trim() && !!password

  /** 账号密码登录（按身份源分流）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (loading || !formValid) return
    setLoading(true)
    setError('')
    try {
      if (mode === 'plane') {
        await loginWithPlane(username.trim().toLowerCase(), password)
      } else {
        await login(username.trim(), password)
      }
    } catch (err) {
      setError(toMessage(err, mode))
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
      setError(`开发模式进入失败：${toMessage(err, mode)}`)
    } finally {
      setDevLoading(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--sa-alias-bg-base)] px-4">
      <div className="w-full max-w-[380px] rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-8 shadow-sm">
        <div className="flex flex-col items-center gap-2 pb-6">
          <BrandMark size={40} />
          <h1 className="text-[18px] font-medium tracking-tight text-[var(--sa-alias-label-primary)]">
            登录 Synlora
          </h1>
        </div>

        <div
          role="tablist"
          aria-label="账号类型"
          className="mb-5 flex rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] p-0.5"
        >
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'ai4ms'}
            className={segmentClass(mode === 'ai4ms')}
            onClick={() => setMode('ai4ms')}
          >
            AI4MS 账号
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'plane'}
            className={segmentClass(mode === 'plane')}
            onClick={() => setMode('plane')}
          >
            OpenEVA 账号
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--sa-alias-label-secondary)]">
              {mode === 'plane' ? '邮箱' : '用户名'}
            </span>
            <input
              type={mode === 'plane' ? 'email' : 'text'}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete={mode === 'plane' ? 'email' : 'username'}
              autoFocus
              placeholder={mode === 'plane' ? '请输入 OpenEVA 登录邮箱' : '请输入用户名'}
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
            disabled={loading || !formValid}
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

        <div className="flex justify-center pt-5">
          <VersionTag />
        </div>
      </div>
    </div>
  )
}
