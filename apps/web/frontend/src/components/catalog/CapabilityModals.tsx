/**
 * 能力编辑/安装弹窗集（技能、专家、插件安装与个人配置编辑）。
 *
 * 从 MinePanel / CapabilityCenter 抽出，供列表页与详情页共用。
 * `ExpertModal` 编辑态会先拉一次详情把 `system_prompt` / `tool_whitelist` /
 * `avatar` 回填——列表接口不回传这些字段，此前打开编辑框即空白、一保存就把
 * 原人设提示词覆盖掉。
 * 插件两个模态共用 `PluginConfigForm` 内芯：安装与改配置的差别只在预填值与端点。
 */
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft, type SkillDraft } from '@/stores/myCapabilities'
import type { CatalogItem, MyCapability, PluginConfigField } from '@/types'

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
 * 插件配置表单内芯（安装与编辑配置共用）：字段完全由 config_schema 驱动。
 *
 * 两种模式只差三处：预填值（编辑态给非敏感字段的当前值）、敏感字段是否已有存值
 * （编辑态才可能为真，显示"已配置 · 留空保持不变"）、以及提交走的端点。故做成
 * 一个受控表单 + 两个薄包装，而不是两份近乎相同的实现。
 */
function PluginConfigForm({
  mode,
  schema,
  readyKeys,
  initialConfig,
  secretsSet,
  submitLabel,
  savingLabel,
  onSubmit,
  onClose,
}: {
  mode: 'install' | 'edit'
  schema: PluginConfigField[]
  /** 管理员公共配置已就绪的字段名（这些字段留空即用系统配置）。 */
  readyKeys: string[]
  /** 非敏感字段的当前值（编辑态预填；安装态为空）。 */
  initialConfig: Record<string, string>
  /** 敏感字段是否已存值（仅编辑态可能为真）。 */
  secretsSet: Record<string, boolean>
  submitLabel: string
  savingLabel: string
  onSubmit: (config: Record<string, string>) => Promise<void>
  onClose: () => void
}) {
  const editing = mode === 'edit'
  // 只预填非敏感字段：敏感字段明文永不下发，输入框留空 = 保持原值
  const [form, setForm] = useState<Record<string, string>>(() => ({ ...initialConfig }))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const hasReady = readyKeys.length > 0
  const hasSecrets = schema.some((f) => f.secret)

  /** 提交：文本值统一 trim（避免粘贴带入的首尾空白原样入库，运行期调用才失败）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await onSubmit(Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, v.trim()]),
      ))
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {/* 公共配置打底提示：有 ready 字段才显示（无打底时留空会被必填校验挡下） */}
      {hasReady && (
        <p className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 text-xs text-[var(--sa-alias-label-secondary)]">
          {editing
            ? '标注系统默认的字段已由管理员统一配置，留空即沿用系统配置；填写则覆盖为你的个人配置（仅自己可见）。'
            : '标注系统默认的字段已由管理员统一配置，可留空直接安装；填写则覆盖为你的个人配置（仅自己可见）。'}
        </p>
      )}
      {editing && hasSecrets && (
        <p className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 text-xs text-[var(--sa-alias-label-secondary)]">
          敏感字段不回显已存值：留空表示保持不变，填写则覆盖。
        </p>
      )}
      {schema.map((field, index) => {
        const hasDefault = readyKeys.includes(field.key)
        const alreadySet = Boolean(secretsSet[field.key])
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
              // 原生必填校验；敏感字段恒不设 required（留空 = 保持原值/不覆盖，
              // 存过值也不该被表单拦住）；有系统默认的字段可留空
              required={Boolean(field.required) && !field.secret && !hasDefault}
              autoFocus={index === 0}
              autoComplete={field.type === 'password' ? 'new-password' : 'off'}
              className={inputClass}
            />
            {/* 编辑态：敏感字段只能给"存没存"的标记（明文不回显，无从预填） */}
            {editing && field.secret && (
              <span className="text-xs text-[var(--sa-alias-label-secondary)]">
                {alreadySet ? '已配置 · 留空保持不变' : '尚未配置'}
              </span>
            )}
            {field.description ? (
              <span className="text-xs text-[var(--sa-alias-label-caption)]">{field.description}</span>
            ) : null}
          </label>
        )
      })}

      {error && <FormError>{error}</FormError>}

      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onClose} className={secondaryButtonClass}>取消</button>
        <button type="submit" disabled={saving} className={primaryButtonClass}>
          {saving ? savingLabel : submitLabel}
        </button>
      </div>
    </form>
  )
}

/**
 * 插件安装模态（市场首次安装：没有"当前值"可保留，敏感字段不显示已配置标记）。
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
  return (
    <Modal title={`安装 ${item.name}`} onClose={onClose}>
      <PluginConfigForm
        mode="install"
        schema={item.config_schema ?? []}
        readyKeys={item.config_ready_keys ?? []}
        initialConfig={{}}
        secretsSet={{}}
        submitLabel="安装"
        savingLabel="安装中…"
        onSubmit={async (config) => {
          await install(item.kind, item.id, config)
          onDone(`已安装 ${item.name}`)
        }}
        onClose={onClose}
      />
    </Modal>
  )
}

/**
 * 插件个人配置编辑模态（详情页）：非敏感字段预填当前值，敏感字段留空 = 保持原值。
 */
export function PluginEditConfigModal({
  pluginId,
  name,
  schema,
  current,
  secretsSet,
  readyKeys,
  onClose,
  onDone,
}: {
  pluginId: string
  name: string
  schema: PluginConfigField[]
  /** 非敏感字段的当前值（详情端点返回的个人配置）。 */
  current: Record<string, string>
  /** 敏感字段是否已存值。 */
  secretsSet: Record<string, boolean>
  /** 管理员公共配置已就绪的字段名。 */
  readyKeys: string[]
  onClose: () => void
  /** 成功后回调（父级关模态、提示并刷新详情）。 */
  onDone: (message: string) => void
}) {
  const updatePluginConfig = useCatalogStore((s) => s.updatePluginConfig)
  return (
    <Modal title={`编辑配置 ${name}`} onClose={onClose}>
      <PluginConfigForm
        mode="edit"
        schema={schema}
        readyKeys={readyKeys}
        initialConfig={current}
        secretsSet={secretsSet}
        submitLabel="保存"
        savingLabel="保存中…"
        onSubmit={async (config) => {
          await updatePluginConfig(pluginId, config)
          onDone(`已更新 ${name} 的配置`)
        }}
        onClose={onClose}
      />
    </Modal>
  )
}
