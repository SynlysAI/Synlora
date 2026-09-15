/**
 * 「我的」面板：自建 + 已安装的能力（技能/专家/插件），带子页签过滤。
 *
 * 结构照 jiuwen `SkillPanel/index.tsx`：子页签 全部/已启用/已停用/内置；
 * 自建条目可编辑/删除，内置条目只能启用/停用/卸载（规格 D5：内置不可定制）。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { FormError, GrayBadge, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft, type SkillDraft } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'

type SubTab = 'all' | 'enabled' | 'disabled' | 'builtin'

const SUB_TABS: Array<{ key: SubTab; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'enabled', label: '已启用' },
  { key: 'disabled', label: '已停用' },
  { key: 'builtin', label: '内置' },
]

const KIND_LABEL: Record<MyCapability['kind'], string> = {
  skill: '技能',
  expert: '专家',
  plugin: '插件',
}

/** 技能编辑模态（自建：新建与编辑共用）。 */
function SkillModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createSkill = useMyCapabilitiesStore((s) => s.createSkill)
  const updateSkill = useMyCapabilitiesStore((s) => s.updateSkill)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

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
            className={`${inputClass} resize-y font-mono`}
          />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 专家编辑模态（自建：新建与编辑共用）。 */
function ExpertModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createExpert = useMyCapabilitiesStore((s) => s.createExpert)
  const updateExpert = useMyCapabilitiesStore((s) => s.updateExpert)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [avatar, setAvatar] = useState('')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

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

  return (
    <Modal title={editing ? `编辑专家 ${initial?.name}` : '新建专家'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          名称
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus className={inputClass} />
        </label>
        <label className={labelClass}>
          头像 emoji（可空）
          <input value={avatar} onChange={(e) => setAvatar(e.target.value)} className={inputClass} />
        </label>
        <label className={labelClass}>
          描述
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputClass} />
        </label>
        <label className={labelClass}>
          人设提示词
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            required
            className={`${inputClass} resize-y`}
          />
        </label>
        <label className={labelClass}>
          可用工具（逗号分隔，留空 = 全部内置工具）
          <input value={tools} onChange={(e) => setTools(e.target.value)} placeholder="python.run, file.read" className={inputClass} />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 「我的」面板。 */
export default function MinePanel() {
  const items = useMyCapabilitiesStore((s) => s.items)
  const loaded = useMyCapabilitiesStore((s) => s.loaded)
  const loadMine = useMyCapabilitiesStore((s) => s.loadMine)
  const deleteSkill = useMyCapabilitiesStore((s) => s.deleteSkill)
  const deleteExpert = useMyCapabilitiesStore((s) => s.deleteExpert)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
  const uninstall = useCatalogStore((s) => s.uninstall)
  const [sub, setSub] = useState<SubTab>('enabled')
  const [editingSkill, setEditingSkill] = useState<MyCapability | null | undefined>(undefined)
  const [editingExpert, setEditingExpert] = useState<MyCapability | null | undefined>(undefined)

  useEffect(() => {
    loadMine().catch((err) => toast('error', `加载我的能力失败：${errorText(err)}`))
  }, [loadMine])

  const rows = items.filter((item) => {
    if (sub === 'enabled') return item.enabled
    if (sub === 'disabled') return !item.enabled
    if (sub === 'builtin') return item.builtin
    return true
  })

  const handleToggle = async (item: MyCapability) => {
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
      await loadMine()
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  const handleRemove = async (item: MyCapability) => {
    try {
      if (item.source === 'mine' && item.kind === 'skill') await deleteSkill(item.id)
      else if (item.source === 'mine' && item.kind === 'expert') await deleteExpert(item.id)
      else await uninstall(item.kind, item.id)
      await loadMine()
      toast('success', `已删除 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 工具行：新建入口 + 子页签 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {SUB_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setSub(t.key)}
              className={
                sub === t.key
                  ? 'rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-sm)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setEditingSkill(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建技能
          </button>
          <button type="button" onClick={() => setEditingExpert(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建专家
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {rows.map((item) => (
          <div
            key={`${item.kind}:${item.id}`}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-[14px] font-medium">{item.name}</span>
                <GrayBadge>{KIND_LABEL[item.kind]}</GrayBadge>
                <GrayBadge>{item.source === 'mine' ? '自建' : '已安装'}</GrayBadge>
                {item.revoked && <GrayBadge>已被管理员下架</GrayBadge>}
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={item.description}>
                {item.description || '无描述'}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {item.enabled ? <GrayBadge>已启用</GrayBadge> : <GrayBadge>已停用</GrayBadge>}
              {!item.revoked && (
                <button type="button" onClick={() => void handleToggle(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  {item.enabled ? '停用' : '启用'}
                </button>
              )}
              {item.source === 'mine' && item.kind === 'skill' && (
                <button type="button" onClick={() => setEditingSkill(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              {item.source === 'mine' && item.kind === 'expert' && (
                <button type="button" onClick={() => setEditingExpert(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              <button type="button" onClick={() => void handleRemove(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                {item.source === 'mine' ? '删除' : '卸载'}
              </button>
            </div>
          </div>
        ))}
        {loaded && rows.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            还没有技能或专家，去「市场」安装，或点右上角自己创建一个
          </div>
        )}
      </div>

      {editingSkill !== undefined && (
        <SkillModal
          initial={editingSkill}
          onClose={(changed) => {
            setEditingSkill(undefined)
            if (changed) {
              toast('success', '已保存技能')
              void loadMine()
            }
          }}
        />
      )}
      {editingExpert !== undefined && (
        <ExpertModal
          initial={editingExpert}
          onClose={(changed) => {
            setEditingExpert(undefined)
            if (changed) {
              toast('success', '已保存专家')
              void loadMine()
            }
          }}
        />
      )}
    </div>
  )
}
