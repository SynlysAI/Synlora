/**
 * 用户侧「能力中心」整页（/capabilities，任意登录用户可见）。
 *
 * 平台内置能力（专家 / 技能 / 插件）的市场视图：列表来自 `GET /api/v1/market/{kind}`
 * （只返回当前用户可见的条目，即不含 hidden），按三类分组展示；每行按状态给动作——
 * 默认启用（所有人可用）只显示徽标、已安装显示「卸载」、未安装显示「安装」。
 *
 * 结构与交互照 PluginsAdmin（行卡片列表 + 模态表单 + toast + 成功后刷新）：
 * 插件行带 config_schema 时先开表单弹窗填个人配置，再 POST `{config}` 安装；
 * 无 schema 的条目直接 POST `{}`。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { PageTopBar, ToastHost } from '@/components/layout'
import { FormError, GrayBadge, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import type { CatalogItem } from '@/types'

/** 分组定义：kind + 小标题 + 空态文案。 */
const GROUPS: Array<{ kind: CatalogItem['kind']; title: string; empty: string }> = [
  { kind: 'expert', title: '专家', empty: '暂无可用专家' },
  { kind: 'skill', title: '技能', empty: '暂无可用技能' },
  { kind: 'plugin', title: '插件', empty: '暂无可用插件' },
]

/**
 * 插件安装模态：字段完全由 item.config_schema 驱动（结构照 PluginsAdmin）。
 *
 * 与管理员配置表单的差异：市场是**首次安装**，没有"当前值"可保留，因此敏感字段
 * 的占位直接用 schema 自带的 placeholder（而不是"留空保持不变"）。
 */
function PluginInstallModal({
  item,
  onClose,
  onDone,
}: {
  item: CatalogItem
  onClose: () => void
  /** 成功后回调（父级关模态并提示）。 */
  onDone: (message: string) => void
}) {
  const install = useCatalogStore((s) => s.install)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交：POST /catalog/plugin/{id}/install（422 缺必填等 detail 内联展示）。 */
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
      await install(item.kind, item.id, config)
      onDone(`已安装 ${item.name}`)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={`安装 ${item.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {(item.config_schema ?? []).map((field, index) => (
          <label key={field.key} className={labelClass}>
            {field.label}
            <input
              type={field.type === 'password' ? 'password' : 'text'}
              value={form[field.key] ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, [field.key]: e.target.value }))}
              placeholder={field.placeholder ?? ''}
              // 原生必填校验；敏感字段留空表示不覆盖，不加 required
              required={Boolean(field.required) && !field.secret}
              autoFocus={index === 0}
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
            {saving ? '安装中…' : '安装'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 用户侧能力中心整页组件。 */
export default function CapabilityCenter() {
  const byKind = useCatalogStore((s) => s.byKind)
  const loaded = useCatalogStore((s) => s.loaded)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
  const install = useCatalogStore((s) => s.install)
  const uninstall = useCatalogStore((s) => s.uninstall)
  /** 需要先填配置再安装的插件（null 关闭）。 */
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)

  // 挂载时拉取一次；安装/卸载在 store 内成功后自行重拉
  useEffect(() => {
    loadMarket().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadMarket])

  /** 安装：带 schema 的插件先开配置模态，其余直接安装。 */
  const handleInstall = async (item: CatalogItem) => {
    if (item.config_schema && item.config_schema.length > 0) {
      setConfiguring(item)
      return
    }
    try {
      await install(item.kind, item.id)
      toast('success', `已安装 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  /** 卸载：删除本人安装记录，成功后 store 已重拉列表。 */
  const handleUninstall = async (item: CatalogItem) => {
    try {
      await uninstall(item.kind, item.id)
      toast('success', `已卸载 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <PageTopBar title="能力中心" />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 py-6">
          <div className="flex flex-col gap-6">
            {/* 页头 */}
            <div>
              <h2 className="text-[16px] font-medium tracking-tight">能力中心</h2>
              <p className="pt-0.5 text-[13px] text-[var(--sa-alias-label-tertiary)]">
                平台提供的能力，安装后即可在自己的对话里使用
              </p>
            </div>

            {/* 三个分组：专家 / 技能 / 插件 */}
            {GROUPS.map((group) => {
              const rows = byKind[group.kind]
              return (
                <section key={group.kind} className="flex flex-col gap-2">
                  <h3 className="text-[13px] font-medium text-[var(--sa-alias-label-secondary)]">
                    {group.title}
                  </h3>
                  <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
                    {rows.map((item) => (
                      <div
                        key={item.id}
                        className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate font-mono text-[14px] font-medium">{item.name}</span>
                            <GrayBadge>内置</GrayBadge>
                            {item.installed && !item.default_enabled && <GrayBadge>已安装</GrayBadge>}
                          </div>
                          <div
                            className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]"
                            title={item.description}
                          >
                            {item.description || '无描述'}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2.5">
                          {item.default_enabled ? (
                            <GrayBadge>默认可用</GrayBadge>
                          ) : item.installed ? (
                            <button
                              type="button"
                              onClick={() => void handleUninstall(item)}
                              className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                            >
                              卸载
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void handleInstall(item)}
                              className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                            >
                              安装
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                    {loaded && rows.length === 0 && (
                      <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
                        {group.empty}
                      </div>
                    )}
                  </div>
                </section>
              )
            })}
          </div>
        </div>
      </main>

      {/* 插件配置模态（安装后才写个人配置） */}
      {configuring && (
        <PluginInstallModal
          item={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(message) => {
            setConfiguring(null)
            toast('success', message)
          }}
        />
      )}

      {/* 全局 toast */}
      <ToastHost />
    </div>
  )
}
