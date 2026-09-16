/**
 * 模型服务管理页（#/admin/models，仅 admin）。
 *
 * 列表：名称/base_url/model_id/启用开关（inline PATCH）/密钥徽标/操作
 * （编辑、连通性测试、删除二次确认）；新建与编辑共用模态表单
 * （编辑时 api_key 留空表示不修改）；测试结果行内展示 ✓ 延迟 / ✗ 错误。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import type { ModelProvider, ProviderTestResult } from '@/types'
import { toast } from '@/stores/toasts'
import { useAdminStore } from '@/stores/admin'
import { FormError, GrayBadge, Modal, Spinner, Switch } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 行内测试结果状态。 */
type TestState =
  | { status: 'loading' }
  | { status: 'ok'; latencyMs: number }
  | { status: 'error'; error: string }

/** 新建/编辑共用表单值（编辑时 apiKey 留空 = 不修改）。 */
interface ProviderForm {
  name: string
  baseUrl: string
  apiKey: string
  modelId: string
  enabled: boolean
}

/** 空表单初始值。 */
const EMPTY_FORM: ProviderForm = {
  name: '',
  baseUrl: '',
  apiKey: '',
  modelId: '',
  enabled: true,
}

/** 密钥徽标：已配置（灰）/ 未配置（弱红）。 */
function KeyBadge({ hasKey }: { hasKey: boolean }) {
  return hasKey ? (
    <GrayBadge>已配置密钥</GrayBadge>
  ) : (
    <span className="inline-flex shrink-0 items-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-interactive-bg-hover-danger)] px-1.5 py-px text-[10px] leading-4 text-[var(--sa-alias-state-error-primary)]">
      未配置
    </span>
  )
}

/** 表单模态：新建（editing=null）或编辑（editing=目标行）。 */
function ProviderFormModal({
  editing,
  onClose,
  onDone,
}: {
  editing: ModelProvider | null
  onClose: () => void
  /** 成功后回调（父级刷新列表并关模态）。 */
  onDone: (message: string) => void
}) {
  const [form, setForm] = useState<ProviderForm>(
    () =>
      editing
        ? {
            name: editing.name,
            baseUrl: editing.base_url,
            apiKey: '',
            modelId: editing.model_id,
            enabled: editing.enabled,
          }
        : EMPTY_FORM,
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交：新建 POST / 编辑 PATCH（api_key 非空才随请求发送）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      if (editing) {
        const body: Record<string, unknown> = {
          name: form.name.trim(),
          base_url: form.baseUrl.trim(),
          model_id: form.modelId.trim(),
          enabled: form.enabled,
        }
        if (form.apiKey.trim()) body.api_key = form.apiKey.trim()
        await api(`/api/v1/models/${editing._id}`, { method: 'PATCH', body })
        onDone(`已更新 ${form.name.trim()}`)
      } else {
        await api('/api/v1/models', {
          method: 'POST',
          body: {
            name: form.name.trim(),
            base_url: form.baseUrl.trim(),
            api_key: form.apiKey.trim(),
            model_id: form.modelId.trim(),
            enabled: form.enabled,
          },
        })
        onDone(`已创建 ${form.name.trim()}`)
      }
    } catch (err) {
      // 409 重名 / 422 base_url 非法等 detail 在表单内联展示
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? '编辑模型服务' : '新建模型服务'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          名称
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="如 deepseek-official"
            required
            autoFocus
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          Base URL
          <input
            type="text"
            value={form.baseUrl}
            onChange={(e) => setForm((f) => ({ ...f, baseUrl: e.target.value }))}
            placeholder="https://api.example.com/v1"
            required
            className={inputClass}
          />
          <span className="text-xs text-[var(--sa-alias-label-caption)]">须以 http:// 或 https:// 开头</span>
        </label>
        <label className={labelClass}>
          API Key
          <input
            type="password"
            value={form.apiKey}
            onChange={(e) => setForm((f) => ({ ...f, apiKey: e.target.value }))}
            placeholder={editing ? '留空保持不变' : 'sk-…'}
            autoComplete="new-password"
            className={inputClass}
          />
          <span className="text-xs text-[var(--sa-alias-label-caption)]">加密存储，任何接口不会回显明文</span>
        </label>
        <label className={labelClass}>
          模型 ID
          <input
            type="text"
            value={form.modelId}
            onChange={(e) => setForm((f) => ({ ...f, modelId: e.target.value }))}
            placeholder="如 deepseek-chat"
            required
            className={inputClass}
          />
        </label>
        <label className="flex items-center gap-2 text-[13px] text-[var(--sa-alias-label-secondary)]">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            className="h-4 w-4 accent-[var(--sa-alias-button-primary-fill)]"
          />
          启用（停用后普通用户不可见，助手关联显示"（已停用）"）
        </label>

        {error && <FormError>{error}</FormError>}

        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className={secondaryButtonClass}>
            取消
          </button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 模型服务管理页组件。 */
export default function ModelsAdmin() {
  const providers = useAdminStore((s) => s.providers)
  const loaded = useAdminStore((s) => s.providersLoaded)
  const loadProviders = useAdminStore((s) => s.loadProviders)
  /** 打开表单模态：null 关闭 / 'new' 新建 / 行数据编辑。 */
  const [editing, setEditing] = useState<ModelProvider | 'new' | null>(null)
  /** 行内测试状态（provider_id -> TestState）。 */
  const [tests, setTests] = useState<Record<string, TestState>>({})
  /** 行内启用开关请求中的行 id。 */
  const [togglingId, setTogglingId] = useState<string | null>(null)
  /** 删除二次确认中的行 id。 */
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 挂载时拉取一次；后续在增删改成功后手动刷新
  useEffect(() => {
    loadProviders().catch((err) => toast('error', `加载模型服务失败：${errorText(err)}`))
  }, [loadProviders])

  /** 启用开关：inline PATCH，失败回滚并 toast。 */
  const handleToggle = async (p: ModelProvider, next: boolean) => {
    setTogglingId(p._id)
    try {
      const updated = await api<ModelProvider>(`/api/v1/models/${p._id}`, {
        method: 'PATCH',
        body: { enabled: next },
      })
      useAdminStore.setState((s) => ({
        providers: s.providers.map((it) => (it._id === updated._id ? updated : it)),
      }))
    } catch (err) {
      toast('error', `切换失败：${errorText(err)}`)
    } finally {
      setTogglingId(null)
    }
  }

  /** 连通性测试：loading → ok 延迟 / error 文案（后端把上游异常转业务结果）。 */
  const handleTest = async (p: ModelProvider) => {
    setTests((s) => ({ ...s, [p._id]: { status: 'loading' } }))
    try {
      const r = await api<ProviderTestResult>(`/api/v1/models/${p._id}/test`, {
        method: 'POST',
      })
      setTests((s) => ({
        ...s,
        [p._id]: r.ok
          ? { status: 'ok', latencyMs: r.latency_ms ?? 0 }
          : { status: 'error', error: r.error ?? '未知错误' },
      }))
    } catch (err) {
      setTests((s) => ({ ...s, [p._id]: { status: 'error', error: errorText(err) } }))
    }
  }

  /** 删除（二次确认后执行；被助手引用时后端 409 并提示先解除关联）。 */
  const handleDelete = async (p: ModelProvider) => {
    if (confirmingId !== p._id) {
      setConfirmingId(p._id)
      return
    }
    setDeleting(true)
    try {
      await api(`/api/v1/models/${p._id}`, { method: 'DELETE' })
      toast('success', `已删除 ${p.name}`)
      setConfirmingId(null)
      await loadProviders()
    } catch (err) {
      toast('error', `删除失败：${errorText(err)}`)
    } finally {
      setDeleting(false)
    }
  }

  const modalOpen = editing !== null

  return (
    <div className="flex flex-col gap-4">
      {/* 页头 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">模型服务</h2>
          <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
            OpenAI 兼容服务配置：API Key 加密存储、永不明文回显；停用的服务对普通用户不可见。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing('new')}
          className={primaryButtonClass}
        >
          + 新建
        </button>
      </div>

      {/* 列表 */}
      <div className="overflow-x-auto rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        <table className="w-full min-w-[760px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-[var(--sa-alias-border-l2)] text-left text-xs text-[var(--sa-alias-label-caption)]">
              <th className="px-4 py-2.5 font-medium">名称</th>
              <th className="px-4 py-2.5 font-medium">Base URL</th>
              <th className="px-4 py-2.5 font-medium">模型 ID</th>
              <th className="px-4 py-2.5 font-medium">启用</th>
              <th className="px-4 py-2.5 font-medium">密钥</th>
              <th className="px-4 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => {
              const test = tests[p._id]
              return (
                <tr
                  key={p._id}
                  className="border-b border-[var(--sa-alias-border-l1)] last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{p.name}</span>
                      {!p.enabled && <GrayBadge>已停用</GrayBadge>}
                    </div>
                  </td>
                  <td className="max-w-[240px] px-4 py-3">
                    <span className="block truncate text-[var(--sa-alias-label-secondary)]" title={p.base_url}>
                      {p.base_url}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="block max-w-[160px] truncate text-[var(--sa-alias-label-secondary)]" title={p.model_id}>
                      {p.model_id}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <Switch
                      checked={p.enabled}
                      disabled={togglingId === p._id}
                      label={`启用 ${p.name}`}
                      onChange={(next) => void handleToggle(p, next)}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <KeyBadge hasKey={p.has_key} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2.5">
                      {/* 测试按钮 + 行内结果 */}
                      <span className="flex items-center gap-1.5">
                        <button
                          type="button"
                          disabled={test?.status === 'loading'}
                          onClick={() => void handleTest(p)}
                          className="flex items-center gap-1 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)] disabled:opacity-60"
                        >
                          {test?.status === 'loading' ? (
                            <>
                              <Spinner /> 测试中
                            </>
                          ) : (
                            '测试'
                          )}
                        </button>
                        {test?.status === 'ok' && (
                          <span className="text-xs text-[var(--sa-alias-state-success-primary)]">
                            ✓ {Math.round(test.latencyMs)}ms
                          </span>
                        )}
                        {test?.status === 'error' && (
                          <span
                            className="max-w-[180px] truncate text-xs text-[var(--sa-alias-state-error-primary)]"
                            title={test.error}
                          >
                            ✗ {test.error}
                          </span>
                        )}
                      </span>
                      <button
                        type="button"
                        onClick={() => setEditing(p)}
                        className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                      >
                        编辑
                      </button>
                      {confirmingId === p._id ? (
                        <span className="flex items-center gap-1 text-xs">
                          <button
                            type="button"
                            disabled={deleting}
                            onClick={() => void handleDelete(p)}
                            className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-state-error-primary)] px-1.5 py-0.5 text-white transition-opacity disabled:opacity-50"
                          >
                            确认删除
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmingId(null)}
                            className="rounded-[var(--sa-radius-sm)] px-1.5 py-0.5 text-[var(--sa-alias-label-tertiary)] transition-colors hover:bg-[var(--sa-alias-interactive-bg-hover)]"
                          >
                            取消
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmingId(p._id)}
                          className="text-[13px] text-[var(--sa-alias-state-error-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-state-error-primary)]"
                        >
                          删除
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
            {loaded && providers.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
                  暂无模型服务，点击右上角"新建"添加
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 新建/编辑模态 */}
      {modalOpen && (
        <ProviderFormModal
          editing={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onDone={async (message) => {
            setEditing(null)
            toast('success', message)
            await loadProviders()
          }}
        />
      )}
    </div>
  )
}
