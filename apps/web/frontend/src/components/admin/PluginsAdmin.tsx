/**
 * 插件管理页（#/admin/plugins，仅 admin）。
 *
 * 插件是随包发布的工具扩展包（扫描插件目录 manifest 得到），安装/配置
 * 均写加密配置库；页面按插件 config_schema 动态渲染配置表单：
 * 敏感字段（secret）加密存储、接口永不回明文，已配置时输入框留空表示
 * 保持原值（对应 secrets_set[key]）。
 *
 * 结构照 SkillsAdmin（行卡片列表）+ ModelsAdmin（模态表单的
 * saving/error 局部态、errorText 内联展示、提交后刷新列表）。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import type { PluginInfo } from '@/types'
import { toast } from '@/stores/toasts'
import { useAdminStore } from '@/stores/admin'
import { FormError, GrayBadge, Modal } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 状态徽标：未安装（灰）/ 已配置（灰）/ 待补配置（弱红，列出缺失必填项）。 */
function StatusBadge({ plugin }: { plugin: PluginInfo }) {
  if (!plugin.installed) return <GrayBadge>未安装</GrayBadge>
  if (plugin.configured) return <GrayBadge>已配置</GrayBadge>
  return (
    <span className="inline-flex shrink-0 items-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-interactive-bg-hover-danger)] px-1.5 py-px text-[10px] leading-4 text-[var(--sa-alias-state-error-primary)]">
      待补配置：{plugin.missing.join(', ')}
    </span>
  )
}

/**
 * 安装/配置共用模态表单：字段完全由 plugin.config_schema 驱动。
 *
 * 初值：已安装时用 plugin.config 回填非敏感字段（敏感字段不回传，永远留空）；
 * 未安装时空对象。提交时敏感字段留空 = 保持原值（由后端判定）。
 */
function PluginFormModal({
  plugin,
  onClose,
  onDone,
}: {
  plugin: PluginInfo
  onClose: () => void
  /** 成功后回调（父级刷新列表并关模态）。 */
  onDone: (message: string) => void
}) {
  const [form, setForm] = useState<Record<string, string>>(() =>
    plugin.installed ? { ...plugin.config } : {},
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交：未安装 POST /install，已安装 PUT /config（422 缺必填等 detail 内联展示）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      if (plugin.installed) {
        await api(`/api/v1/plugins/${plugin.id}/config`, {
          method: 'PUT',
          body: { config: form },
        })
        onDone(`已更新 ${plugin.name}`)
      } else {
        await api(`/api/v1/plugins/${plugin.id}/install`, {
          method: 'POST',
          body: { config: form },
        })
        onDone(`已安装 ${plugin.name}`)
      }
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={plugin.installed ? `配置 ${plugin.name}` : `安装 ${plugin.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {plugin.config_schema.map((field) => (
          <label key={field.key} className={labelClass}>
            {field.label}
            {field.required ? <span className="text-[var(--sa-alias-state-error-primary)]"> *</span> : null}
            <input
              type={field.type === 'password' ? 'password' : 'text'}
              value={form[field.key] ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, [field.key]: e.target.value }))}
              placeholder={
                field.type === 'password' && plugin.secrets_set[field.key]
                  ? '留空保持不变'
                  : (field.placeholder ?? '')
              }
              autoComplete={field.type === 'password' ? 'new-password' : 'off'}
              className={inputClass}
            />
            {field.description ? (
              <span className="text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
            ) : null}
          </label>
        ))}

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

/** 插件管理页组件。 */
export default function PluginsAdmin() {
  const plugins = useAdminStore((s) => s.plugins)
  const loaded = useAdminStore((s) => s.pluginsLoaded)
  const loadPlugins = useAdminStore((s) => s.loadPlugins)
  /** 打开表单模态的插件（null 关闭）。 */
  const [editing, setEditing] = useState<PluginInfo | null>(null)

  // 挂载时拉取一次；后续在安装/配置成功后手动刷新
  useEffect(() => {
    loadPlugins().catch((err) => toast('error', `加载插件失败：${errorText(err)}`))
  }, [loadPlugins])

  return (
    <div className="flex flex-col gap-4">
      {/* 页头 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[16px] font-medium tracking-tight">插件</h2>
          <p className="pt-0.5 text-[13px] text-[var(--sa-alias-label-tertiary)]">
            内置插件包的工具扩展；按插件声明的 schema 配置，敏感字段加密存储、永不明文回显。
          </p>
        </div>
      </div>

      {/* 列表 */}
      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {plugins.map((p) => (
          <div
            key={p.id}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-[14px] font-medium">{p.name}</span>
                <StatusBadge plugin={p} />
                <span className="shrink-0 text-xs text-[var(--sa-alias-label-caption)]">v{p.version}</span>
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={p.description}>
                {p.description || '无描述'}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              <button
                type="button"
                onClick={() => setEditing(p)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                {p.installed ? '配置' : '安装'}
              </button>
            </div>
          </div>
        ))}
        {loaded && plugins.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            暂无可用插件
          </div>
        )}
      </div>

      {/* 安装/配置模态 */}
      {editing && (
        <PluginFormModal
          plugin={editing}
          onClose={() => setEditing(null)}
          onDone={async (message) => {
            setEditing(null)
            toast('success', message)
            await loadPlugins()
          }}
        />
      )}
    </div>
  )
}
