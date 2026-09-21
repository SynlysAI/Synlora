/**
 * 技能管理页（#/admin/skills，仅 admin）。
 *
 * 技能是全局目录（admin 可写、所有登录用户可读），磁盘 SKILL.md 落盘、
 * 不入库；技能名即目录名（kebab-case），故编辑时名称只读（PATCH 不接收改名）。
 *
 * 结构与 AssistantsAdmin 一致：
 * 列表（名称/描述/标签/版本/内置徽标/操作）+ 新建/编辑模态表单
 * + 删除两步内联确认（内置禁用）+ 「导入 SKILL.md」模态 + 导出下载。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api, getToken } from '@/api/client'
import type { CatalogItem, Skill, SkillFile } from '@/types'
import SkillFilePreview from '@/components/catalog/SkillFilePreview'
import { toast } from '@/stores/toasts'
import { useAdminStore } from '@/stores/admin'
import { CatalogPolicySwitches, FormError, GrayBadge, Modal } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 技能名合法模式（kebab-case，与后端 skill_service.NAME_OK 一致）。 */
const NAME_PATTERN = '[a-z0-9]+(-[a-z0-9]+)*'

/** 逗号分隔字符串 → 去空去重的字符串数组。 */
function splitList(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

/** 新建/编辑共用表单值（tags/allowedTools 以逗号分隔原文保存，便于输入）。 */
interface SkillForm {
  name: string
  description: string
  version: string
  author: string
  tags: string
  allowedTools: string
  content: string
}

/** 空表单初始值。 */
const EMPTY_FORM: SkillForm = {
  name: '',
  description: '',
  version: '1.0',
  author: '',
  tags: '',
  allowedTools: '',
  content: '',
}

/** 新建/编辑模态表单：editing=null 走 POST，否则走 PATCH（body 不带 name）。 */
function SkillFormModal({
  editing,
  onClose,
  onDone,
}: {
  editing: Skill | null
  onClose: () => void
  /** 成功后回调（父级刷新列表并关模态）。 */
  onDone: (message: string) => void
}) {
  const [form, setForm] = useState<SkillForm>(
    () =>
      editing
        ? {
            name: editing.name,
            description: editing.description,
            version: editing.version,
            author: editing.author,
            tags: editing.tags.join(', '),
            allowedTools: editing.allowed_tools.join(', '),
            content: editing.content,
          }
        : EMPTY_FORM,
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交：新建 POST / 编辑 PATCH（PATCH 以路径 skills/{name} 定位，body 不含 name）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    const fields = {
      description: form.description.trim(),
      content: form.content,
      version: form.version.trim() || '1.0',
      author: form.author.trim(),
      tags: splitList(form.tags),
      allowed_tools: splitList(form.allowedTools),
    }
    try {
      if (editing) {
        await api(`/api/v1/skills/${editing.name}`, { method: 'PATCH', body: fields })
        onDone(`已更新 ${editing.name}`)
      } else {
        await api('/api/v1/skills', { method: 'POST', body: { name: form.name.trim(), ...fields } })
        onDone(`已创建 ${form.name.trim()}`)
      }
    } catch (err) {
      // 422：技能名非 kebab-case 等 detail 在表单内联展示
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? '编辑技能' : '新建技能'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex gap-3">
          <label className={`${labelClass} flex-1`}>
            名称
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="如 literature-review"
              pattern={NAME_PATTERN}
              title="仅小写字母、数字与连字符（kebab-case）"
              required
              autoFocus={!editing}
              readOnly={Boolean(editing)}
              className={`${inputClass} ${editing ? 'cursor-not-allowed opacity-60' : ''}`}
            />
            <span className="text-xs text-[var(--sa-alias-label-caption)]">
              {editing ? '技能名即目录名，不支持改名（如需改名请另建再删）' : 'kebab-case：小写字母/数字/连字符'}
            </span>
          </label>
          <label className={`${labelClass} w-24`}>
            版本
            <input
              type="text"
              value={form.version}
              onChange={(e) => setForm((f) => ({ ...f, version: e.target.value }))}
              placeholder="1.0"
              className={inputClass}
            />
          </label>
        </div>
        <label className={labelClass}>
          描述
          <input
            type="text"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="做什么 + 何时用（skill.list 与技能清单展示）"
            required
            className={inputClass}
          />
        </label>
        <div className="flex gap-3">
          <label className={`${labelClass} flex-1`}>
            作者
            <input
              type="text"
              value={form.author}
              onChange={(e) => setForm((f) => ({ ...f, author: e.target.value }))}
              placeholder="可选"
              className={inputClass}
            />
          </label>
          <label className={`${labelClass} flex-1`}>
            标签
            <input
              type="text"
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
              placeholder="逗号分隔，如 写作, 文献"
              className={inputClass}
            />
          </label>
        </div>
        <label className={labelClass}>
          允许的工具
          <input
            type="text"
            value={form.allowedTools}
            onChange={(e) => setForm((f) => ({ ...f, allowedTools: e.target.value }))}
            placeholder="逗号分隔，如 file.read, python.run（留空表示不限制）"
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          正文（SKILL.md，不含 frontmatter）
          <textarea
            value={form.content}
            onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
            placeholder="# 标题&#10;&#10;## 目标&#10;…&#10;&#10;## 工作流&#10;…"
            required
            rows={10}
            className={`${inputClass} resize-y font-mono text-[13px] leading-relaxed`}
          />
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

/** 「导入 SKILL.md」模态：粘贴全文 → POST /skills/import（422 detail 内联展示）。 */
function SkillImportModal({
  onClose,
  onDone,
}: {
  onClose: () => void
  /** 成功后回调（父级刷新列表并关模态）。 */
  onDone: (message: string) => void
}) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交导入：后端解析 SKILL.md 全文，失败（缺 frontmatter 等）→ 422。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const created = await api<Skill>('/api/v1/skills/import', {
        method: 'POST',
        body: { text },
      })
      onDone(`已导入 ${created.name}`)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title="导入 SKILL.md" onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          SKILL.md 全文
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'---\nname: my-skill\ndescription: 做什么 + 何时用\nversion: "1.0"\ntags: [写作]\n---\n\n# 标题\n\n## 目标\n…'}
            required
            autoFocus
            rows={12}
            className={`${inputClass} resize-y font-mono text-[13px] leading-relaxed`}
          />
          <span className="text-xs text-[var(--sa-alias-label-caption)]">
            须含 frontmatter（name 为 kebab-case、description 必填）；同名技能将被覆盖
          </span>
        </label>

        {error && <FormError>{error}</FormError>}

        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className={secondaryButtonClass}>
            取消
          </button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '导入中…' : '导入'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** ZIP 技能目录导入模态：上传完整目录包，服务端写入公共技能层。 */
function SkillZipImportModal({
  onClose,
  onDone,
}: {
  onClose: () => void
  onDone: (message: string) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  /** 提交 ZIP 文件并刷新管理员公共技能列表。 */
  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file || saving) return
    setSaving(true)
    setError('')
    const form = new FormData()
    form.append('file', file)
    try {
      const created = await api<Skill>('/api/v1/skills/import-zip', {
        method: 'POST',
        form,
      })
      onDone(`已导入 ${created.name}`)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title="上传技能目录 ZIP" onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          技能目录包
          <input
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className={`${inputClass} file:mr-3 file:rounded file:border-0 file:bg-[var(--sa-alias-interactive-bg-hover)] file:px-2 file:py-1 file:text-xs`}
          />
          <span className="text-xs text-[var(--sa-alias-label-caption)]">
            ZIP 根目录或唯一一级子目录必须包含 SKILL.md，最大 20 MB；支持附带脚本、模板和图片。
          </span>
        </label>
        {file && (
          <p className="text-xs text-[var(--sa-alias-label-secondary)]">已选择：{file.name}</p>
        )}
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className={secondaryButtonClass}>
            取消
          </button>
          <button type="submit" disabled={!file || saving} className={primaryButtonClass}>
            {saving ? '上传中…' : '上传并导入'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 管理员技能目录预览模态：左侧文件树、右侧文本或图片预览。 */
function SkillPreviewModal({
  skill,
  onClose,
}: {
  skill: Skill
  onClose: () => void
}) {
  const [files, setFiles] = useState<SkillFile[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api<SkillFile[]>(`/api/v1/skills/${encodeURIComponent(skill.name)}/files`)
      .then((items) => {
        if (active) setFiles(items)
      })
      .catch((err) => {
        if (active) setError(errorText(err))
      })
    return () => {
      active = false
    }
  }, [skill.name])

  return (
    <Modal title={`预览技能 · ${skill.name}`} onClose={onClose}>
      {error ? (
        <FormError>{error}</FormError>
      ) : files.length === 0 ? (
        <p className="text-sm text-[var(--sa-alias-label-caption)]">加载文件目录…</p>
      ) : (
        <SkillFilePreview skillName={skill.name} files={files} apiPrefix="/api/v1/skills" />
      )}
    </Modal>
  )
}

/** 技能管理页组件。 */
export default function SkillsAdmin() {
  const allSkills = useAdminStore((s) => s.skills)
  // 插件技能只读且随插件联动，统一在「插件」页查看，不进本列表
  const skills = allSkills.filter((s) => s.source !== 'plugin')
  const loaded = useAdminStore((s) => s.skillsLoaded)
  const loadSkills = useAdminStore((s) => s.loadSkills)
  const catalog = useAdminStore((s) => s.catalog)
  const loadCatalog = useAdminStore((s) => s.loadCatalog)
  /** 打开表单模态：null 关闭 / 'new' 新建 / 行数据编辑。 */
  const [editing, setEditing] = useState<Skill | 'new' | null>(null)
  /** 导入模态开关。 */
  const [importing, setImporting] = useState(false)
  /** ZIP 导入模态开关。 */
  const [importingZip, setImportingZip] = useState(false)
  /** 当前打开文件预览的技能。 */
  const [previewing, setPreviewing] = useState<Skill | null>(null)
  /** 删除二次确认中的技能名。 */
  const [confirmingName, setConfirmingName] = useState<string | null>(null)
  /** 导出中的技能名（按钮转圈/禁用）。 */
  const [exportingName, setExportingName] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 挂载时拉取一次；后续在增删改成功后手动刷新
  useEffect(() => {
    loadSkills().catch((err) => toast('error', `加载技能失败：${errorText(err)}`))
    loadCatalog().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadSkills, loadCatalog])

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

  /** 技能行的策略开关：不在能力目录里的技能（自建/导入）返回 null。 */
  const policySwitches = (name: string) => {
    const item = catalog.find((c) => c.kind === 'skill' && c.id === name)
    if (!item) return null
    return (
      <CatalogPolicySwitches item={item} onChange={(next) => void handlePolicy(item, next)} />
    )
  }

  /** 删除（两步内联确认；builtin 行按钮已禁用，后端对只读根技能返回 404 兜底）。 */
  const handleDelete = async (s: Skill) => {
    if (confirmingName !== s.name) {
      setConfirmingName(s.name)
      return
    }
    setDeleting(true)
    try {
      await api(`/api/v1/skills/${s.name}`, { method: 'DELETE' })
      toast('success', `已删除 ${s.name}`)
      setConfirmingName(null)
      await loadSkills()
    } catch (err) {
      // 内置技能来自只读根（catalog/skills），不落公共层目录，删除返回 404；公共层技能不存在时同样 404
      toast('error', `删除失败：${errorText(err)}`)
    } finally {
      setDeleting(false)
    }
  }

  /** 导出完整技能目录 ZIP，保留 SKILL.md、脚本、模板和图片。 */
  const handleExport = async (s: Skill) => {
    setExportingName(s.name)
    try {
      const token = getToken()
      const resp = await fetch(`/api/v1/skills/${encodeURIComponent(s.name)}/export-zip`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${s.name}.zip`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast('error', `导出失败：${(err as Error).message}`)
    } finally {
      setExportingName(null)
    }
  }

  const modalOpen = editing !== null

  return (
    <div className="flex flex-col gap-4">
      {/* 页头 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">技能</h2>
          <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
            全局技能目录（SKILL.md 落盘）；内置技能不可删除、可编辑，所有登录用户可用。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" onClick={() => setImporting(true)} className={secondaryButtonClass}>
            导入 SKILL.md
          </button>
          <button type="button" onClick={() => setImportingZip(true)} className={secondaryButtonClass}>
            上传 ZIP
          </button>
          <button type="button" onClick={() => setEditing('new')} className={primaryButtonClass}>
            + 新建
          </button>
        </div>
      </div>

      {/* 列表 */}
      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {skills.map((s) => (
          <div
            key={s.name}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-[14px] font-medium">{s.name}</span>
                {s.builtin && <GrayBadge>内置</GrayBadge>}
                <span className="shrink-0 text-xs text-[var(--sa-alias-label-caption)]">v{s.version}</span>
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={s.description}>
                {s.description || '无描述'}
              </div>
            </div>
            <div className="hidden w-40 shrink-0 items-center gap-1 overflow-hidden lg:flex">
              {s.tags.slice(0, 3).map((t) => (
                <GrayBadge key={t}>{t}</GrayBadge>
              ))}
              {s.tags.length > 3 && (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">+{s.tags.length - 3}</span>
              )}
            </div>
            <div className="hidden w-20 shrink-0 text-right text-xs text-[var(--sa-alias-label-caption)] md:block">
              {s.allowed_tools.length} 项工具
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {policySwitches(s.name)}
              <button
                type="button"
                disabled={exportingName === s.name}
                onClick={() => void handleExport(s)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)] disabled:opacity-60"
              >
                {exportingName === s.name ? '导出中' : '导出'}
              </button>
              <button
                type="button"
                onClick={() => setPreviewing(s)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                预览
              </button>
              <button
                type="button"
                onClick={() => setEditing(s)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                编辑
              </button>
              {s.builtin ? (
                <button
                  type="button"
                  disabled
                  title="内置技能不可删除"
                  className="cursor-not-allowed text-[13px] text-[var(--sa-alias-label-caption)] opacity-60"
                >
                  删除
                </button>
              ) : confirmingName === s.name ? (
                <span className="flex items-center gap-1 text-xs">
                  <button
                    type="button"
                    disabled={deleting}
                    onClick={() => void handleDelete(s)}
                    className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-state-error-primary)] px-1.5 py-0.5 text-white transition-opacity disabled:opacity-50"
                  >
                    确认删除
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmingName(null)}
                    className="rounded-[var(--sa-radius-sm)] px-1.5 py-0.5 text-[var(--sa-alias-label-tertiary)] transition-colors hover:bg-[var(--sa-alias-interactive-bg-hover)]"
                  >
                    取消
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmingName(s.name)}
                  className="text-[13px] text-[var(--sa-alias-state-error-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-state-error-primary)]"
                >
                  删除
                </button>
              )}
            </div>
          </div>
        ))}
        {loaded && skills.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            暂无技能，点击右上角"新建"或"导入 SKILL.md"添加
          </div>
        )}
      </div>

      {/* 新建/编辑模态 */}
      {modalOpen && (
        <SkillFormModal
          editing={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onDone={async (message) => {
            setEditing(null)
            toast('success', message)
            await loadSkills()
          }}
        />
      )}

      {/* 导入模态 */}
      {importing && (
        <SkillImportModal
          onClose={() => setImporting(false)}
          onDone={async (message) => {
            setImporting(false)
            toast('success', message)
            await loadSkills()
          }}
        />
      )}

      {importingZip && (
        <SkillZipImportModal
          onClose={() => setImportingZip(false)}
          onDone={async (message) => {
            setImportingZip(false)
            toast('success', message)
            await loadSkills()
          }}
        />
      )}

      {previewing && <SkillPreviewModal skill={previewing} onClose={() => setPreviewing(null)} />}
    </div>
  )
}
