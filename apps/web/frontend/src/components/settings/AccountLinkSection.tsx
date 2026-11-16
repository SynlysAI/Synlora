/** 设置页「账户绑定」区块：双向关联另一侧账户（按登录方定核心方）。 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/api/client'
import ConfirmDialog from '@/components/sidebar/ConfirmDialog'
import { errorText, inputClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'

/** GET /api/v1/me/account-link 响应。 */
interface AccountLinkStatus {
  /** 当前登录身份类型：plane = OpenEVA 网页登录；ai4ms = 本方账户登录。 */
  identity_kind: 'plane' | 'ai4ms'
  bound: boolean
  /** 核心方（绑定后数据以谁为准）：plane | ai4ms；未绑定为空。 */
  core: string
  plane_user_id: string | null
  plane_email: string
  linked_username: string
  linked_display_name: string
  linked_plane_user_id: string
}

/** 按绑定方向生成的确认弹窗文案。 */
function bindDialogCopy(identityKind: 'plane' | 'ai4ms', label: string) {
  if (identityKind === 'plane') {
    return {
      title: '关联 Synlora 账户',
      description: `绑定后将以当前 OpenEVA 身份为核心：两边历史会话将在会话列表合并展示；文件工作区、访问令牌、能力/技能安装保持各自独立，当前身份设置不受影响。将关联账户：${label}`,
    }
  }
  return {
    title: '关联 OpenEVA 账号',
    description: `绑定后将以当前 Synlora 账户为核心：OpenEVA 登录与云端智能体将直接进入当前账户身份；两边会话合并展示；文件工作区、访问令牌、能力/技能沿用当前账户。将关联账号：${label}`,
  }
}

/** 账户绑定区块（挂在设置页个人设置下方）。 */
export default function AccountLinkSection() {
  const [status, setStatus] = useState<AccountLinkStatus | null>(null)
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  /** 待确认的绑定（弹窗打开中）；null = 关闭。 */
  const [confirming, setConfirming] = useState(false)
  /** 待确认的解绑。 */
  const [confirmingUnbind, setConfirmingUnbind] = useState(false)

  const load = useCallback(async () => {
    try {
      setStatus(await api<AccountLinkStatus>('/api/v1/me/account-link'))
    } catch (err) {
      setError(errorText(err, '绑定状态加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const identityKind = status?.identity_kind ?? 'ai4ms'

  const bind = async () => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await api('/api/v1/me/account-link', {
        method: 'POST',
        body: identityKind === 'plane'
          ? { kind: 'ai4ms', username: username.trim(), password }
          : { kind: 'plane', email: email.trim().toLowerCase(), password },
      })
      setConfirming(false)
      setUsername('')
      setEmail('')
      setPassword('')
      await load()
    } catch (err) {
      // 409/401/422 等业务错误在弹窗内展示，弹窗保持打开
      setError(errorText(err, '绑定失败'))
    } finally {
      setSaving(false)
    }
  }

  const unbind = async () => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await api('/api/v1/me/account-link', { method: 'DELETE' })
      setConfirmingUnbind(false)
      await load()
    } catch (err) {
      setError(errorText(err, '解绑失败'))
    } finally {
      setSaving(false)
    }
  }

  const bindTargetLabel = identityKind === 'plane' ? username.trim() : email.trim()
  const formValid = identityKind === 'plane'
    ? !!username.trim() && !!password
    : !!email.trim() && email.includes('@') && !!password

  const dialog = confirming
    ? bindDialogCopy(identityKind, bindTargetLabel || '—')
    : null

  return (
    <section className="mb-8">
      <h2 className="text-[14px] font-semibold">账户绑定</h2>
      <p className="mt-1 text-[12px] text-[var(--sa-alias-label-tertiary)]">
        关联另一侧账户：绑定后两边的会话历史合并展示，数据以绑定时登录的一方为核心。
      </p>

      {error && (
        <p role="alert" className="mt-3 text-[13px] text-[var(--sa-alias-state-error-primary)]">{error}</p>
      )}

      {loading ? (
        <p className="mt-3 text-[13px] text-[var(--sa-alias-label-tertiary)]">加载中…</p>
      ) : status?.bound ? (
        <div className="mt-3 border-t border-[var(--sa-alias-border-l1)] pt-3">
          <dl className="grid gap-x-8 gap-y-2 text-[13px] sm:grid-cols-2">
            <div>
              <dt className="text-[12px] text-[var(--sa-alias-label-caption)]">
                {identityKind === 'plane' ? '已关联 Synlora 账户' : '已关联 OpenEVA 账号'}
              </dt>
              <dd className="mt-0.5">
                {identityKind === 'plane'
                  ? (status.linked_display_name || status.linked_username || '—')
                  : (status.plane_email || status.linked_plane_user_id || '—')}
              </dd>
            </div>
            <div>
              <dt className="text-[12px] text-[var(--sa-alias-label-caption)]">核心身份</dt>
              <dd className="mt-0.5">
                {status.core === 'plane' ? 'OpenEVA 账号（当前登录方式不变）' : 'Synlora 账户'}
              </dd>
            </div>
          </dl>
          <button
            type="button"
            className={`${secondaryButtonClass} mt-4`}
            onClick={() => setConfirmingUnbind(true)}
          >
            解除绑定
          </button>
        </div>
      ) : (
        <div className="mt-3 border-t border-[var(--sa-alias-border-l1)] pt-4">
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="flex flex-col gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
              {identityKind === 'plane' ? 'Synlora 用户名' : 'OpenEVA 邮箱'}
              <input
                className={inputClass}
                type={identityKind === 'plane' ? 'text' : 'email'}
                value={identityKind === 'plane' ? username : email}
                onChange={(event) =>
                  identityKind === 'plane'
                    ? setUsername(event.target.value)
                    : setEmail(event.target.value)
                }
                placeholder={identityKind === 'plane' ? '要关联的 Synlora 用户名' : '要关联的 OpenEVA 登录邮箱'}
                autoComplete={identityKind === 'plane' ? 'username' : 'email'}
              />
            </label>
            <label className="flex flex-col gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
              密码
              <input
                className={inputClass}
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="该账户的密码"
                autoComplete="current-password"
              />
            </label>
            <button
              type="button"
              className={primaryButtonClass}
              disabled={!formValid || saving}
              onClick={() => setConfirming(true)}
            >
              绑定
            </button>
          </div>
          <p className="mt-2 text-[12px] text-[var(--sa-alias-label-tertiary)]">
            需输入另一侧账户的密码以验证所有权；绑定不影响两侧账户的独立登录。
          </p>
        </div>
      )}

      {dialog && (
        <ConfirmDialog
          title={dialog.title}
          description={dialog.description}
          confirmLabel="确认绑定"
          busy={saving}
          error={error || null}
          onCancel={() => {
            setConfirming(false)
            setError('')
          }}
          onConfirm={() => void bind()}
        />
      )}
      {confirmingUnbind && (
        <ConfirmDialog
          title="解除账户绑定"
          description="解绑后将不再合并展示两边的会话；已产生的数据不受影响，可随时重新绑定。"
          confirmLabel="解绑"
          busy={saving}
          error={error || null}
          onCancel={() => {
            setConfirmingUnbind(false)
            setError('')
          }}
          onConfirm={() => void unbind()}
        />
      )}
    </section>
  )
}
