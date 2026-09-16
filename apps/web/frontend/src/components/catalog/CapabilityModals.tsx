/**
 * 能力编辑/安装弹窗集（技能、专家、插件安装）。
 *
 * 从 MinePanel / CapabilityCenter 抽出，供列表页与详情页共用。
 * `ExpertModal` 编辑态会先拉一次详情把 `system_prompt` / `tool_whitelist` /
 * `avatar` 回填——列表接口不回传这些字段，此前打开编辑框即空白、一保存就把
 * 原人设提示词覆盖掉。
 */
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft, type SkillDraft } from '@/stores/myCapabilities'
import type { CatalogItem, MyCapability } from '@/types'

/** 技能编辑模态（自建：新建与编辑共用）。 */
export function SkillModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createSkill = useMyCapabilitiesStore((s) => s.createSkill)
  const updateSkill = useMyCapabilitiesStore((s) => s.updateSkill)
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(editing)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // 详情是否已成功回填：编辑态拉取失败时保持 false，表单全程禁用——否则用户忽略
  // 错误提示继续编辑，保存时会把没拉到的正文当空串写回，等于静默清空原有技能
  const [detailReady, setDetailReady] = useState(!editing)

  // 编辑态先拉正文（列表只有名称与描述）；首次加载与失败重试共用这一份实现
  const fetchDetail = useCallback(async () => {
    if (initial === null) return
    setLoading(true)
    setError('') // 重试前先清错，否则加载中仍压着上一次的失败提示
    setDetailReady(false)
    try {
      const detail = await loadDetail('skill', initial.id)
      setDescription(detail.description)
      setContent(detail.content ?? '')
      setDetailReady(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [initial, loadDetail])

  useEffect(() => {
    void fetchDetail()
  }, [fetchDetail])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const draft: SkillDraft = { name: name.trim(), description: description.trim(), content }
      if (editing) await updateSkill(initial.id, { description: draft.description, content: draft.content })
      else await createSkill(draft)
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  // 详情没拉到且不在加载中 = 卡死态，给个重试出口（否则只能取消重开模态）
  const retryable = editing && !loading && !detailReady

  return (
    <Modal title={editing ? `编辑技能 ${initial?.name}` : '新建技能'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {!editing && (
          <label className={labelClass}>
            技能名（kebab-case）
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-skill"
              required
              autoFocus
              className={inputClass}
            />
          </label>
        )}
        <label className={labelClass}>
          描述（做什么 + 何时用）
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            autoFocus={editing}
            disabled={loading || !detailReady}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          正文（Markdown，不含 frontmatter）
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            required
            disabled={loading || !detailReady}
            className={`${inputClass} resize-y font-mono`}
          />
        </label>
        {error && (
          <div className="flex items-center gap-3">
            <FormError>{error}</FormError>
            {retryable && (
              <button type="button" onClick={() => void fetchDetail()} className={secondaryButtonClass}>
                重试
              </button>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving || loading || !detailReady} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 专家编辑模态（自建：新建与编辑共用）。 */
export function ExpertModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createExpert = useMyCapabilitiesStore((s) => s.createExpert)
  const updateExpert = useMyCapabilitiesStore((s) => s.updateExpert)
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [avatar, setAvatar] = useState('')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState('')
  const [loading, setLoading] = useState(editing)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // 详情是否已成功回填：编辑态拉取失败时保持 false，表单全程禁用——否则用户忽略
  // 错误提示继续编辑，保存时会把没拉到的工具白名单/头像当空值写回，静默丢数据
  const [detailReady, setDetailReady] = useState(!editing)

  // 编辑态先拉详情回填（人设提示词与工具白名单列表接口不返回）；失败重试共用
  const fetchDetail = useCallback(async () => {
    if (initial === null) return
    setLoading(true)
    setError('') // 重试前先清错，否则加载中仍压着上一次的失败提示
    setDetailReady(false)
    try {
      const detail = await loadDetail('expert', initial.id)
      setAvatar(detail.avatar ?? '')
      setDescription(detail.description)
      setSystemPrompt(detail.system_prompt ?? '')
      setTools((detail.tool_whitelist ?? []).join(', '))
      setDetailReady(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [initial, loadDetail])

  useEffect(() => {
    void fetchDetail()
  }, [fetchDetail])

  const draft = (): ExpertDraft => ({
    name: name.trim(),
    avatar: avatar.trim(),
    description: description.trim(),
    system_prompt: systemPrompt,
    tool_whitelist: tools.split(',').map((t) => t.trim()).filter(Boolean),
  })

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      if (editing) await updateExpert(initial.id, draft())
      else await createExpert(draft())
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  // 详情没拉到且不在加载中 = 卡死态，给个重试出口（否则只能取消重开模态）
  const retryable = editing && !loading && !detailReady

  return (
    <Modal title={editing ? `编辑专家 ${initial?.name}` : '新建专家'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
            disabled={loading || !detailReady}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          头像 emoji（可空）
          <input
            value={avatar}
            onChange={(e) => setAvatar(e.target.value)}
            disabled={loading || !detailReady}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          描述
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={loading || !detailReady}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          人设提示词
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            required
            disabled={loading || !detailReady}
            className={`${inputClass} resize-y`}
          />
        </label>
        <label className={labelClass}>
          可用工具（逗号分隔，留空 = 全部内置工具）
          <input
            value={tools}
            onChange={(e) => setTools(e.target.value)}
            placeholder="python.run, file.read"
            disabled={loading || !detailReady}
            className={inputClass}
          />
        </label>
        {error && (
          <div className="flex items-center gap-3">
            <FormError>{error}</FormError>
            {retryable && (
              <button type="button" onClick={() => void fetchDetail()} className={secondaryButtonClass}>
                重试
              </button>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving || loading || !detailReady} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/**
 * 插件安装模态：字段完全由 item.config_schema 驱动。
 *
 * 市场是**首次安装**，没有"当前值"可保留，因此敏感字段的占位直接用 schema
 * 自带的 placeholder（而不是"留空保持不变"）。
 */
export function PluginInstallModal({
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
        {/* 公共配置打底提示：有 ready 字段才显示（无打底时留空会被必填校验挡下） */}
        {(item.config_ready_keys?.length ?? 0) > 0 && (
          <p className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 text-xs text-[var(--sa-alias-label-secondary)]">
            标注系统默认的字段已由管理员统一配置，可留空直接安装；填写则覆盖为你的个人配置（仅自己可见）。
          </p>
        )}
        {(item.config_schema ?? []).map((field, index) => {
          const hasDefault = (item.config_ready_keys ?? []).includes(field.key)
          return (
            <label key={field.key} className={labelClass}>
              {field.label}
              {hasDefault && (
                <span className="pl-1 text-xs text-[var(--sa-alias-label-caption)]">（系统默认）</span>
              )}
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={form[field.key] ?? ''}
                onChange={(e) => setForm((f) => ({ ...f, [field.key]: e.target.value }))}
                placeholder={field.placeholder ?? ''}
                // 原生必填校验；敏感字段留空表示不覆盖；有系统默认的字段可留空
                required={Boolean(field.required) && !field.secret && !hasDefault}
                autoFocus={index === 0}
                autoComplete={field.type === 'password' ? 'new-password' : 'off'}
                className={inputClass}
              />
              {field.description ? (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
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
            {saving ? '安装中…' : '安装'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
