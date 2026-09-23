/**
 * 助手管理页（#/admin/assistants，仅 admin）。
 *
 * 列表：avatar/名称/描述/内置徽标/关联模型名/操作（编辑、删除——内置禁用）；
 * 新建/编辑模态表单：name、avatar、description、system_prompt、
 * 模型选择（当前 enabled 的模型服务）、工具白名单多选（全选/全清）。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import type { Assistant, CatalogItem, ModelProvider } from '@/types'
import { TOOL_LABELS } from '@/components/chat/toolLabels'
import { toast } from '@/stores/toasts'
import { useAdminStore } from '@/stores/admin'
import { CatalogPolicySwitches, FormError, GrayBadge, Modal } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 知识库选项（后端代理 WeKnora 列表；doc_count 取不到时为 null）。 */
interface KnowledgeBase {
  id: string
  name: string
  description: string
  doc_count: number | null
}

/** 新建/编辑共用表单值（modelProviderId 空串 = 不关联模型）。 */
interface AssistantForm {
  name: string
  avatar: string
  description: string
  systemPrompt: string
  modelProviderId: string
  toolWhitelist: string[]
  skillRefs: string[]
  mcpRefs: string[]
  knowledgeBaseIds: string[]
}

/** 空表单初始值。 */
const EMPTY_FORM: AssistantForm = {
  name: '',
  avatar: '',
  description: '',
  systemPrompt: '',
  modelProviderId: '',
  toolWhitelist: [],
  skillRefs: [],
  mcpRefs: [],
  knowledgeBaseIds: [],
}

/** 助手头像：avatar 字段（emoji/字符）缺省取名称首字符（与左栏一致）。 */
function AssistantAvatar({ assistant }: { assistant: Assistant }) {
  const label = assistant.avatar?.trim() || assistant.name.slice(0, 1)
  return (
    <span
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-specific-sidebar-nav-item-active-accent)] text-[14px] font-medium"
      aria-hidden="true"
    >
      {label}
    </span>
  )
}

/** 表单模态：新建（editing=null）或编辑（editing=目标行）。 */
function AssistantFormModal({
  editing,
  models,
  onClose,
  onDone,
}: {
  editing: Assistant | null
  /** 当前 enabled 的模型服务（select 选项）。 */
  models: ModelProvider[]
  onClose: () => void
  /** 成功后回调（父级刷新列表并关模态）。 */
  onDone: (message: string) => void
}) {
  const [form, setForm] = useState<AssistantForm>(
    () =>
      editing
        ? {
            name: editing.name,
            avatar: editing.avatar ?? '',
            description: editing.description,
            systemPrompt: editing.system_prompt,
            modelProviderId: editing.model_provider_id ?? '',
            toolWhitelist: editing.tool_whitelist,
            skillRefs: editing.skill_refs ?? [],
            mcpRefs: editing.mcp_refs ?? [],
            knowledgeBaseIds: editing.knowledge_base_ids ?? [],
          }
        : EMPTY_FORM,
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  /** 知识库选项（打开表单时拉一次；失败不阻塞编辑，仅提示）。 */
  const [kbs, setKbs] = useState<KnowledgeBase[] | null>(null)
  const [kbError, setKbError] = useState('')
  /** 工具名清单（实取 /api/v1/tools；拉取失败回落 TOOL_LABELS 键，标签见 toolLabels.ts）。 */
  const [toolNames, setToolNames] = useState<string[]>(() => Object.keys(TOOL_LABELS))
  // 插件技能只随会话启用插件生效（不进助手绑定候选），与技能管理页同口径过滤
  const skills = useAdminStore((state) => state.skills).filter((s) => s.source !== 'plugin')
  const loadSkills = useAdminStore((state) => state.loadSkills)

  useEffect(() => {
    let cancelled = false
    api<KnowledgeBase[]>('/api/v1/knowledge-bases')
      .then((list) => {
        if (!cancelled) setKbs(list)
      })
      .catch((err) => {
        if (!cancelled) setKbError(errorText(err))
      })
    api<Array<{ name: string; description: string }>>('/api/v1/tools')
      .then((list) => {
        if (!cancelled) setToolNames(list.map((item) => item.name))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    loadSkills().catch(() => undefined)
  }, [loadSkills])

  /** 工具勾选切换。 */
  const toggleTool = (tool: string, checked: boolean) => {
    setForm((f) => ({
      ...f,
      toolWhitelist: checked
        ? [...f.toolWhitelist, tool]
        : f.toolWhitelist.filter((t) => t !== tool),
    }))
  }

  /** 知识库勾选切换。 */
  const toggleKb = (id: string, checked: boolean) => {
    setForm((f) => ({
      ...f,
      knowledgeBaseIds: checked
        ? [...f.knowledgeBaseIds, id]
        : f.knowledgeBaseIds.filter((k) => k !== id),
    }))
  }

  /** 提交：新建 POST / 编辑 PATCH（全字段提交，白名单按勾选原样提交，保留清单外已存工具名）。 */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    const body = {
      name: form.name.trim(),
      avatar: form.avatar.trim(),
      description: form.description.trim(),
      system_prompt: form.systemPrompt,
      tool_whitelist: form.toolWhitelist,
      skill_refs: form.skillRefs,
      mcp_refs: form.mcpRefs,
      model_provider_id: form.modelProviderId || null,
      knowledge_base_ids: form.knowledgeBaseIds,
    }
    try {
      if (editing) {
        await api(`/api/v1/assistants/${editing._id}`, { method: 'PATCH', body })
        onDone(`已更新 ${form.name.trim()}`)
      } else {
        await api('/api/v1/assistants', { method: 'POST', body })
        onDone(`已创建 ${form.name.trim()}`)
      }
    } catch (err) {
      // 422：system_prompt 空 / 非法工具名 detail / 模型服务不存在或停用
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? '编辑助手' : '新建助手'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex gap-3">
          <label className={`${labelClass} w-24`}>
            头像
            <input
              type="text"
              value={form.avatar}
              onChange={(e) => setForm((f) => ({ ...f, avatar: e.target.value }))}
              placeholder="🤖"
              maxLength={4}
              className={`${inputClass} text-center`}
            />
            <span className="text-xs text-[var(--sa-alias-label-caption)]">emoji 或单字符</span>
          </label>
          <label className={`${labelClass} flex-1`}>
            名称
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="如 文献综述助手"
              required
              autoFocus
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
            placeholder="一句话说明用途（左栏与选择列表展示）"
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          系统提示词
          <textarea
            value={form.systemPrompt}
            onChange={(e) => setForm((f) => ({ ...f, systemPrompt: e.target.value }))}
            placeholder="定义助手的角色、能力边界与输出要求"
            required
            rows={6}
            className={`${inputClass} resize-y font-mono text-[13px] leading-relaxed`}
          />
        </label>
        <label className={labelClass}>
          模型服务
          <select
            value={form.modelProviderId}
            onChange={(e) => setForm((f) => ({ ...f, modelProviderId: e.target.value }))}
            className={inputClass}
          >
            {/* 编辑已关联模型的助手时禁用"不关联"：后端 PATCH 不支持清除关联 */}
            <option value="" disabled={Boolean(editing?.model_provider_id)}>
              不关联
            </option>
            {models.map((m) => (
              <option key={m._id} value={m._id}>
                {m.name}（{m.model_id}）
              </option>
            ))}
          </select>
          <span className="text-xs text-[var(--sa-alias-label-caption)]">
            仅列出启用的模型服务{editing?.model_provider_id ? '；已关联的模型暂不支持清除' : ''}
          </span>
        </label>
        <div className={labelClass}>
          <div className="flex items-center justify-between">
            <span>工具白名单（已选 {form.toolWhitelist.length}/{toolNames.length}）</span>
            <span className="flex gap-2 text-xs">
              <button
                type="button"
                onClick={() => setForm((f) => ({ ...f, toolWhitelist: [...toolNames] }))}
                className="text-[var(--sa-alias-label-tertiary)] transition-colors hover:text-[var(--sa-alias-label-primary)]"
              >
                全选
              </button>
              <button
                type="button"
                onClick={() => setForm((f) => ({ ...f, toolWhitelist: [] }))}
                className="text-[var(--sa-alias-label-tertiary)] transition-colors hover:text-[var(--sa-alias-label-primary)]"
              >
                全清
              </button>
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1.5 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] p-2.5 sm:grid-cols-3">
            {toolNames.map((tool) => (
              <label key={tool} className="flex items-center gap-2 text-[13px] text-[var(--sa-alias-label-primary)]">
                <input
                  type="checkbox"
                  checked={form.toolWhitelist.includes(tool)}
                  onChange={(e) => toggleTool(tool, e.target.checked)}
                  className="h-4 w-4 accent-[var(--sa-alias-button-primary-fill)]"
                />
                <span className="truncate" title={tool}>
                  {TOOL_LABELS[tool] ?? tool}
                </span>
              </label>
            ))}
          </div>
        </div>

        <div className={labelClass}>
          <div className="flex items-center justify-between">
            <span>绑定技能</span>
            <span className="text-xs text-[var(--sa-alias-label-caption)]">
              已选 {form.skillRefs.length}
            </span>
          </div>
          {skills.length === 0 ? (
            <span className="text-xs text-[var(--sa-alias-label-caption)]">暂无可用技能</span>
          ) : (
            <div className="grid max-h-36 grid-cols-2 gap-1.5 overflow-y-auto rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] p-2.5 sm:grid-cols-3">
              {skills.map((skill) => (
                <label key={skill.name} className="flex min-w-0 items-start gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    checked={form.skillRefs.includes(skill.name)}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        skillRefs: event.target.checked
                          ? [...current.skillRefs, skill.name]
                          : current.skillRefs.filter((item) => item !== skill.name),
                      }))
                    }
                    className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--sa-alias-button-primary-fill)]"
                  />
                  <span className="min-w-0 truncate" title={skill.description}>
                    {skill.name}
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>

        <label className={labelClass}>
          MCP 引用
          <input
            type="text"
            value={form.mcpRefs.join(', ')}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                mcpRefs: event.target.value.split(',').map((item) => item.trim()).filter(Boolean),
              }))
            }
            placeholder="逗号分隔 MCP ID，例如 literature-server"
            className={inputClass}
          />
          <span className="text-xs text-[var(--sa-alias-label-caption)]">
            这里只保存 MCP ID，不读取或保存凭证；运行时按当前用户可访问的 MCP 连接解析。
          </span>
        </label>

        {/* 知识库绑定（WeKnora）：knowledge.search 工具的检索范围 */}
        <div className={labelClass}>
          <div className="flex items-center justify-between">
            <span>知识库（knowledge.search 检索范围）</span>
            {form.knowledgeBaseIds.length > 0 && (
              <span className="text-xs text-[var(--sa-alias-label-tertiary)]">
                已选 {form.knowledgeBaseIds.length}
              </span>
            )}
          </div>
          {kbError ? (
            <p className="text-xs text-[var(--sa-alias-state-warn-label)]">
              知识库服务不可用：{kbError}（暂不能改绑定，已有绑定保持不变）
            </p>
          ) : kbs === null ? (
            <p className="text-xs text-[var(--sa-alias-label-caption)]">加载知识库列表…</p>
          ) : kbs.length === 0 ? (
            <p className="text-xs text-[var(--sa-alias-label-caption)]">
              暂无知识库（在 WeKnora 平台创建后再来绑定）
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-1.5 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] p-2.5 sm:grid-cols-3">
              {kbs.map((kb) => (
                <label key={kb.id} className="flex items-center gap-2 text-[13px] text-[var(--sa-alias-label-primary)]">
                  <input
                    type="checkbox"
                    checked={form.knowledgeBaseIds.includes(kb.id)}
                    onChange={(e) => toggleKb(kb.id, e.target.checked)}
                    className="h-4 w-4 shrink-0 accent-[var(--sa-alias-button-primary-fill)]"
                  />
                  <span className="truncate" title={kb.description || kb.name}>
                    {kb.name}（{kb.doc_count ?? '?'} 篇文档）
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>

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

/** 助手管理页组件。 */
export default function AssistantsAdmin() {
  const allAssistants = useAdminStore((s) => s.assistants)
  // 插件播种的专家（asst-plugin-*）只读且随插件联动，统一在「插件」页查看
  const assistants = allAssistants.filter((a) => !a._id.startsWith('asst-plugin-'))
  const models = useAdminStore((s) => s.enabledModels)
  const loaded = useAdminStore((s) => s.assistantsLoaded)
  const loadAssistants = useAdminStore((s) => s.loadAssistants)
  const catalog = useAdminStore((s) => s.catalog)
  const loadCatalog = useAdminStore((s) => s.loadCatalog)
  /** 打开表单模态：null 关闭 / 'new' 新建 / 行数据编辑。 */
  const [editing, setEditing] = useState<Assistant | 'new' | null>(null)
  /** 删除二次确认中的行 id。 */
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 挂载时拉取一次（store 内同时同步工作台助手数据）；增删改成功后手动刷新
  useEffect(() => {
    loadAssistants().catch((err) => toast('error', `加载助手失败：${errorText(err)}`))
    loadCatalog().catch((err) => toast('error', `加载能力目录失败：${errorText(err)}`))
  }, [loadAssistants, loadCatalog])

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

  /** 助手行的策略开关：仅内置目录里的专家（不在目录里的返回 null）。 */
  const policySwitches = (id: string) => {
    const item = catalog.find((c) => c.kind === 'expert' && c.id === id)
    if (!item) return null
    return (
      <CatalogPolicySwitches item={item} onChange={(next) => void handlePolicy(item, next)} />
    )
  }

  /** 删除（二次确认后执行；builtin 行按钮已禁用，后端仍有 409 兜底）。 */
  const handleDelete = async (a: Assistant) => {
    if (confirmingId !== a._id) {
      setConfirmingId(a._id)
      return
    }
    setDeleting(true)
    try {
      await api(`/api/v1/assistants/${a._id}`, { method: 'DELETE' })
      toast('success', `已删除 ${a.name}`)
      setConfirmingId(null)
      await loadAssistants()
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
          <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">助手</h2>
          <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
            内置助手不可删除、可编辑；新建/修改后工作台左栏即时可选。
          </p>
        </div>
        <button type="button" onClick={() => setEditing('new')} className={primaryButtonClass}>
          + 新建
        </button>
      </div>

      {/* 列表 */}
      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {assistants.map((a) => (
          <div
            key={a._id}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <AssistantAvatar assistant={a} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-[14px] font-medium">{a.name}</span>
                {a.builtin && <GrayBadge>内置</GrayBadge>}
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={a.description}>
                {a.description || '无描述'}
              </div>
            </div>
            <div className="hidden w-32 shrink-0 truncate text-[13px] text-[var(--sa-alias-label-secondary)] sm:block" title={a.model_name ?? undefined}>
              {a.model_name ? `模型：${a.model_name}` : '未关联模型'}
            </div>
            <div className="hidden w-20 shrink-0 text-right text-xs text-[var(--sa-alias-label-caption)] md:block">
              {a.tool_whitelist.length} 项工具
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {policySwitches(a._id)}
              <button
                type="button"
                onClick={() => setEditing(a)}
                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                编辑
              </button>
              {a.builtin ? (
                <button
                  type="button"
                  disabled
                  title="内置助手不可删除"
                  className="cursor-not-allowed text-[13px] text-[var(--sa-alias-label-caption)] opacity-60"
                >
                  删除
                </button>
              ) : confirmingId === a._id ? (
                <span className="flex items-center gap-1 text-xs">
                  <button
                    type="button"
                    disabled={deleting}
                    onClick={() => void handleDelete(a)}
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
                  onClick={() => setConfirmingId(a._id)}
                  className="text-[13px] text-[var(--sa-alias-state-error-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-state-error-primary)]"
                >
                  删除
                </button>
              )}
            </div>
          </div>
        ))}
        {loaded && assistants.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            暂无助手，点击右上角"新建"添加
          </div>
        )}
      </div>

      {/* 新建/编辑模态 */}
      {modalOpen && (
        <AssistantFormModal
          editing={editing === 'new' ? null : editing}
          models={models}
          onClose={() => setEditing(null)}
          onDone={async (message) => {
            setEditing(null)
            toast('success', message)
            await loadAssistants()
          }}
        />
      )}
    </div>
  )
}
