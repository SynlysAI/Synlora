/** 个人 yxkey 访问令牌设置页：列表、创建、启停、删除与一次性复制。 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/api/client'
import { PageTopBar, ToastHost } from '@/components/layout'
import { Switch } from '@/components/admin/shared'
import { errorText, inputClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import ConfirmDialog from '@/components/sidebar/ConfirmDialog'
import { formatRelativeTime } from '@/utils/format'
import { copyText } from '@/utils/clipboard'
import type { ApiKey, ApiKeyCreateResponse } from '@/types'

/** 把 Unix 秒格式化为日期文本；空值保持短横线。 */
function formatDate(value: number | null): string {
  if (!value) return '—'
  const date = new Date(value * 1000)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/** 个人访问令牌设置页。 */
export default function ApiKeySettings() {
  const [items, setItems] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [createdKey, setCreatedKey] = useState('')
  const [copied, setCopied] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api<{ items: ApiKey[] }>('/api/v1/user/apikeys')
      setItems(data.items)
    } catch (err) {
      setError(errorText(err, '访问令牌加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const expiryTimestamp = useMemo(() => {
    if (!expiresAt) return null
    const timestamp = new Date(expiresAt).getTime()
    return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : null
  }, [expiresAt])

  const create = async () => {
    if (!name.trim() || saving) return
    setSaving(true)
    setError('')
    try {
      const data = await api<ApiKeyCreateResponse>('/api/v1/user/apikeys', {
        method: 'POST',
        body: {
          name: name.trim(),
          expires_at: expiryTimestamp ? new Date(expiryTimestamp * 1000).toISOString() : null,
        },
      })
      setCreatedKey(data.key)
      setCopied(false)
      setName('')
      setExpiresAt('')
      await load()
    } catch (err) {
      setError(errorText(err, '访问令牌创建失败'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (item: ApiKey, next: boolean) => {
    setItems((previous) => previous.map((entry) => entry.id === item.id ? { ...entry, is_enabled: next } : entry))
    try {
      await api(`/api/v1/user/apikeys/${item.id}`, { method: 'PATCH', body: { is_enabled: next } })
    } catch (err) {
      setError(errorText(err, '访问令牌状态更新失败'))
      await load()
    }
  }

  const remove = async () => {
    if (!deleteId) return
    setSaving(true)
    try {
      await api(`/api/v1/user/apikeys/${deleteId}`, { method: 'DELETE' })
      setDeleteId(null)
      await load()
    } catch (err) {
      setError(errorText(err, '访问令牌删除失败'))
    } finally {
      setSaving(false)
    }
  }

  const copy = async () => {
    setCopied(await copyText(createdKey))
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <PageTopBar title="个人访问令牌" />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[880px] px-8 pt-10 pb-12">
          <header className="mb-6">
            <h1 className="text-2xl font-semibold leading-9">访问令牌</h1>
            <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
              用于 CLI、Plane 或其他客户端访问 Synlora API。令牌只在创建时显示一次。
            </p>
          </header>

          {error && (
            <p role="alert" className="mb-4 text-[13px] text-[var(--sa-alias-state-error-primary)]">{error}</p>
          )}

          {createdKey && (
            <section aria-label="新访问令牌" className="mb-6 rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-5">
              <h2 className="text-[14px] font-medium">新令牌已创建</h2>
              <p className="mt-1 text-[13px] text-[var(--sa-alias-label-tertiary)]">
                请立即复制保存；关闭或刷新页面后，系统只能显示前缀，无法找回完整令牌。
              </p>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                <code className="min-w-0 flex-1 truncate rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-3 py-2 text-[13px]">
                  {createdKey}
                </code>
                <div className="flex gap-2">
                  <button type="button" className={primaryButtonClass} onClick={() => void copy()}>{copied ? '已复制' : '复制'}</button>
                  <button type="button" className={secondaryButtonClass} onClick={() => setCreatedKey('')}>关闭</button>
                </div>
              </div>
            </section>
          )}

          <section className="mb-6 rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-5">
            <h2 className="text-[14px] font-medium">创建令牌</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_190px_auto] sm:items-end">
              <label className="flex flex-col gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
                名称
                <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 Plane 集成" maxLength={100} />
              </label>
              <label className="flex flex-col gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]">
                过期日期（可选）
                <input className={inputClass} type="date" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} />
              </label>
              <button type="button" className={primaryButtonClass} disabled={!name.trim() || saving} onClick={() => void create()}>
                {saving ? '创建中…' : '创建令牌'}
              </button>
            </div>
          </section>

          <section className="rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
            <div className="border-b border-[var(--sa-alias-border-l1)] px-5 py-4">
              <h2 className="text-[14px] font-medium">已有令牌</h2>
            </div>
            {loading ? (
              <p className="px-5 py-8 text-center text-[13px] text-[var(--sa-alias-label-tertiary)]">加载中…</p>
            ) : items.length === 0 ? (
              <p className="px-5 py-8 text-center text-[13px] text-[var(--sa-alias-label-tertiary)]">暂无访问令牌。</p>
            ) : (
              <table className="w-full table-fixed border-collapse text-left text-[13px]">
                <thead>
                  <tr className="text-[12px] text-[var(--sa-alias-label-tertiary)]">
                    <th className="w-[28%] px-5 py-3 font-medium">名称</th>
                    <th className="w-[22%] px-3 py-3 font-medium">前缀</th>
                    <th className="w-[13%] px-3 py-3 font-medium">状态</th>
                    <th className="w-[14%] px-3 py-3 font-medium">最近使用</th>
                    <th className="w-[13%] px-3 py-3 font-medium">过期</th>
                    <th className="w-[10%] px-5 py-3 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} className="border-t border-[var(--sa-alias-border-l1)]">
                      <td className="truncate px-5 py-3" title={item.name}>{item.name}</td>
                      <td className="px-3 py-3 font-mono text-[12px] text-[var(--sa-alias-label-secondary)]">{item.key_prefix}…</td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <Switch checked={item.is_enabled} label={`${item.name} 启用状态`} onChange={(next) => void toggle(item, next)} />
                          <span className="text-[12px] text-[var(--sa-alias-label-tertiary)]">{item.is_enabled ? '启用' : '停用'}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3 text-[var(--sa-alias-label-secondary)]">{item.last_used_at ? formatRelativeTime(item.last_used_at) : '未使用'}</td>
                      <td className="px-3 py-3 text-[var(--sa-alias-label-secondary)]">{formatDate(item.expires_at)}</td>
                      <td className="px-5 py-3 text-right">
                        <button type="button" className={secondaryButtonClass} onClick={() => setDeleteId(item.id)}>删除</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      </main>
      <ToastHost />
      {deleteId && (
        <ConfirmDialog
          title="删除访问令牌"
          description="删除后使用该令牌的客户端会立即失去访问权限。此操作不可恢复。"
          confirmLabel="删除"
          busy={saving}
          onCancel={() => setDeleteId(null)}
          onConfirm={() => void remove()}
        />
      )}
    </div>
  )
}
