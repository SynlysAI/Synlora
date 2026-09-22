/**
 * 扩展管理页（#/admin/plugins，仅 admin）。
 *
 * 扩展是随包发布的平台插件包（扫描插件目录 manifest 得到），配置即启用
 * （首次保存配置 = 后端 install，写加密配置库）；页面按插件 config_schema
 * 动态渲染配置表单：敏感字段（secret）加密存储、接口永不回明文，已配置时
 * 输入框留空表示保持原值（对应 secrets_set[key]）。scope=admin 的字段只在
 * 本页填写（用户侧安装不弹表单）。
 *
 * 结构照 SkillsAdmin（行卡片列表）+ ModelsAdmin（模态表单的
 * saving/error 局部态、errorText 内联展示、提交后刷新列表）。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import type { CatalogItem, PluginInfo } from '@/types'
import { toast } from '@/stores/toasts'
import { useAdminStore } from '@/stores/admin'
import { CatalogPolicySwitches, FormError, GrayBadge, Modal } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 状态徽标：未配置（灰）/ 已配置（灰）/ 待补配置（弱红，列出缺失必填项）。 */
function StatusBadge({ plugin }: { plugin: PluginInfo }) {
  if (!plugin.installed) return <GrayBadge>未配置</GrayBadge>
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
 * 未安装时空对象。提交时敏感字段留空 = 保持原值（由后端判定）；
 * 已配置的敏感字段可点「清除」标记，保存时随 `clear_secrets` 一起提交（显式清除）。
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
  /** 待清除的敏感字段名（本地标记，保存时才提交；成功后随模态关闭重置）。 */
  const [clearing, setClearing] = useState<string[]>([])

  /** 标记/撤销某敏感字段的「待清除」（只改本地状态，不发请求）。 */
  const toggleClear = (key: string, next: boolean) => {
    setClearing((list) =>
      next ? (list.includes(key) ? list : [...list, key]) : list.filter((k) => k !== key),
    )
  }

  /** 字段输入变化：填了新值即撤销该字段的「待清除」（新值优先，与后端语义一致）。 */
  const handleChange = (key: string, value: string) => {
    setForm((f) => ({ ...f, [key]: value }))
    if (value.trim()) setClearing((list) => list.filter((k) => k !== key))
  }

  /** 提交：未配置 POST /install（配置即启用），已配置 PUT /config（422 缺必填等 detail 内联展示）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    // 文本值统一 trim（避免粘贴带入的首尾空白原样入库，运行期调用才失败）
    const config = Object.fromEntries(
      Object.entries(form).map(([k, v]) => [k, v.trim()]),
    )
    try {
      if (plugin.installed) {
        // 无待清除项时不带该字段（保持请求体与旧版本一致）
        const body: Record<string, unknown> = { config }
        if (clearing.length > 0) body.clear_secrets = clearing
        await api(`/api/v1/plugins/${plugin.id}/config`, {
          method: 'PUT',
          body,
        })
        setClearing([])
        onDone(`已更新 ${plugin.name} 配置`)
      } else {
        await api(`/api/v1/plugins/${plugin.id}/install`, {
          method: 'POST',
          body: { config },
        })
        onDone(`配置完成，${plugin.name} 已启用`)
      }
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={`配置 ${plugin.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* 未配置时说明首次保存的启用效果；已配置则是纯改配置，无需提示 */}
        {!plugin.installed && (
          <p className="text-xs text-[var(--sa-alias-label-caption)]">
            首次保存后将启用该插件：工具与专家对平台生效，用户即可在能力中心安装使用。
          </p>
        )}
        {plugin.config_schema.map((field, index) => {
          // 仅「已配置的敏感字段」才有清除入口（未配置时无事可做）
          const clearable = Boolean(field.secret) && Boolean(plugin.secrets_set[field.key])
          const marked = clearable && clearing.includes(field.key)
          return (
            <label key={field.key} className={labelClass}>
              {field.label}
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={form[field.key] ?? ''}
                onChange={(e) => handleChange(field.key, e.target.value)}
                placeholder={
                  marked
                    ? '保存后将清除'
                    : field.secret && plugin.secrets_set[field.key]
                      ? '留空保持不变'
                      : (field.placeholder ?? '')
                }
                // 原生必填校验；敏感字段留空表示保持原值，不加 required
                required={Boolean(field.required) && !field.secret}
                autoFocus={index === 0}
                autoComplete={field.type === 'password' ? 'new-password' : 'off'}
                // 待清除时禁用输入框：避免"又填又清"的歧义（想改就点撤销或直接输入）
                disabled={marked}
                className={`${inputClass} disabled:opacity-60`}
              />
              {field.description ? (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
              ) : null}
              {clearable ? (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">
                  {marked ? (
                    <>
                      将清除（保存后生效） ·{' '}
                      <button
                        type="button"
                        // 阻止 label 把点击转给输入框
                        onClick={(e) => {
                          e.preventDefault()
                          toggleClear(field.key, false)
                        }}
                        className="text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                      >
                        撤销
                      </button>
                    </>
                  ) : (
                    <>
                      已配置 ·{' '}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault()
                          toggleClear(field.key, true)
                        }}
                        className="text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                      >
                        清除
                      </button>
                    </>
                  )}
                </span>
              ) : null}
            </label>
          )
        })}

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
  const catalog = useAdminStore((s) => s.catalog)
  const loadCatalog = useAdminStore((s) => s.loadCatalog)
  /** 打开表单模态的插件（null 关闭）。 */
  const [editing, setEditing] = useState<PluginInfo | null>(null)

  // 挂载时拉取一次；后续在安装/配置成功后手动刷新
  useEffect(() => {
    loadPlugins().catch((err) => toast('error', `加载插件失败：${errorText(err)}`))
    loadCatalog().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadPlugins, loadCatalog])

  /** 能力目录策略开关：PUT 成功后重拉目录（不做乐观更新）。 */
  const handlePolicy = async (
    item: CatalogItem,
    next: { visibility: 'public' | 'hidden'; default_enabled: boolean },
  ) => {
    try {
      await api(`/api/v1/admin/catalog/${item.kind}/${item.id}/policy`, {
        method: 'PUT',
        body: next,
      })
      await loadCatalog()
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  /** 插件行的策略开关：目录里的插件（不在目录里的返回 null）。 */
  const policySwitches = (id: string) => {
    const item = catalog.find((c) => c.kind === 'plugin' && c.id === id)
    if (!item) return null
    return (
      <CatalogPolicySwitches item={item} onChange={(next) => void handlePolicy(item, next)} />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* 页头 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">扩展管理</h2>
          <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
            管理平台级插件、公共配置和能力目录策略；附属技能、专家与工具统一在扩展内查看。
            普通用户的个人 MCP 接入与凭证不在此管理。
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
              {/* 附属内容清单：插件带来的技能/专家/工具统一在此查看（不进技能/助手管理页） */}
              {(p.skills.length > 0 || p.experts.length > 0 || p.tools.length > 0) && (
                <div
                  className="truncate pt-0.5 text-xs text-[var(--sa-alias-label-caption)]"
                  title={[
                    ...p.skills.map((s) => `技能 ${s.name}：${s.description}`),
                    ...p.experts.map((e) => `专家 ${e.name}`),
                    ...p.tools.map((t) => `工具 ${t}`),
                  ].join('\n')}
                >
                  {[
                    p.skills.length > 0 && `技能 ${p.skills.map((s) => s.name).join('、')}`,
                    p.experts.length > 0 && `专家 ${p.experts.map((e) => e.name).join('、')}`,
                    p.tools.length > 0 && `工具 ${p.tools.length} 个`,
                  ]
                    .filter(Boolean)
                    .join('　·　')}
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {policySwitches(p.id)}
              <button
                type="button"
                onClick={() => setEditing(p)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                配置
              </button>
            </div>
          </div>
        ))}
        {loaded && plugins.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            暂无可用平台扩展
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
