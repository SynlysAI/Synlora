/** 专家能力编排编辑器：人设、技能、MCP、工具与推荐问题统一配置。 */
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'

const BUILTIN_TOOLS = ['python.run', 'file.read', 'file.read_image', 'file.send', 'ask_user']

/** 专家能力编排表单。 */
export default function ExpertEditor({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createExpert = useMyCapabilitiesStore((state) => state.createExpert)
  const updateExpert = useMyCapabilitiesStore((state) => state.updateExpert)
  const loadDetail = useCatalogStore((state) => state.loadDetail)
  const items = useMyCapabilitiesStore((state) => state.items)
  const mcps = useMyCapabilitiesStore((state) => state.mcps)
  const loadMine = useMyCapabilitiesStore((state) => state.loadMine)
  const loadMcps = useMyCapabilitiesStore((state) => state.loadMcps)
  const [name, setName] = useState(initial?.name ?? '')
  const [avatar, setAvatar] = useState('')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [skillRefs, setSkillRefs] = useState<string[]>([])
  const [mcpRefs, setMcpRefs] = useState<string[]>([])
  const [toolWhitelist, setToolWhitelist] = useState<string[]>([])
  const [suggestedPrompts, setSuggestedPrompts] = useState<string[]>([''])
  const [loading, setLoading] = useState(Boolean(initial))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void Promise.all([loadMine(), loadMcps()])
  }, [loadMine, loadMcps])

  useEffect(() => {
    if (!initial) return
    setLoading(true)
    void loadDetail('expert', initial.id).then((detail) => {
      setAvatar(detail.avatar ?? '')
      setDescription(detail.description)
      setSystemPrompt(detail.system_prompt ?? '')
      setSkillRefs(detail.skill_refs ?? [])
      setMcpRefs(detail.mcp_refs ?? [])
      setToolWhitelist(detail.tool_whitelist ?? [])
      setSuggestedPrompts(detail.suggested_prompts?.length ? detail.suggested_prompts : [''])
    }).catch((err) => setError(errorText(err))).finally(() => setLoading(false))
  }, [initial, loadDetail])

  const skills = useMemo(() => items.filter((item) => item.kind === 'skill' && item.enabled), [items])
  const toolOptions = useMemo(() => {
    const remote = mcps.filter((mcp) => mcp.enabled).flatMap((mcp) => mcp.tools.map((tool) => `mcp.${mcp.id}.${tool.name.replace(/[^A-Za-z0-9_-]/g, '_')}`))
    return [...new Set([...BUILTIN_TOOLS, ...remote])]
  }, [mcps])

  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
  const draft = (): ExpertDraft => ({
    name: name.trim(), avatar: avatar.trim(), description: description.trim(), system_prompt: systemPrompt,
    skill_refs: skillRefs, mcp_refs: mcpRefs, tool_whitelist: toolWhitelist, suggested_prompts: suggestedPrompts.map((item) => item.trim()).filter(Boolean),
  })
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      if (initial) await updateExpert(initial.id, draft())
      else await createExpert(draft())
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={initial ? `编辑专家 · ${initial.name}` : '新建专家'} onClose={() => onClose(false)}>
      <form onSubmit={submit} className="flex max-h-[72dvh] flex-col gap-4 overflow-y-auto pr-1">
        <div className="grid gap-3 sm:grid-cols-2"><label className={labelClass}>名称<input value={name} required onChange={(event) => setName(event.target.value)} className={inputClass} /></label><label className={labelClass}>头像<input value={avatar} onChange={(event) => setAvatar(event.target.value)} placeholder="🧪" className={inputClass} /></label></div>
        <label className={labelClass}>描述<input value={description} onChange={(event) => setDescription(event.target.value)} className={inputClass} /></label>
        <label className={labelClass}>Markdown 人设<textarea value={systemPrompt} required rows={8} disabled={loading} onChange={(event) => setSystemPrompt(event.target.value)} className={`${inputClass} resize-y font-mono`} /></label>
        <Selection title="可选技能" items={skills.map((item) => ({ id: item.id, label: item.name, note: item.description }))} selected={skillRefs} onToggle={(id) => setSkillRefs(toggle(skillRefs, id))} />
        <Selection title="MCP 接入" items={mcps.filter((item) => item.enabled).map((item) => ({ id: item.id, label: item.name, note: item.url }))} selected={mcpRefs} onToggle={(id) => setMcpRefs(toggle(mcpRefs, id))} empty="还没有启用的 MCP，请先在扩展中心接入" />
        <Selection title="可用工具（不选表示全部内置工具）" items={toolOptions.map((item) => ({ id: item, label: item, note: item.startsWith('mcp.') ? 'MCP 远程工具' : '平台工具' }))} selected={toolWhitelist} onToggle={(id) => setToolWhitelist(toggle(toolWhitelist, id))} />
        <div className="flex flex-col gap-2"><span className="text-xs text-[var(--sa-alias-label-secondary)]">推荐问题</span>{suggestedPrompts.map((prompt, index) => <div key={index} className="flex gap-2"><input value={prompt} onChange={(event) => setSuggestedPrompts((items) => items.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder="例如：帮我分析这组实验数据" className={inputClass} /><button type="button" onClick={() => setSuggestedPrompts((items) => items.filter((_, itemIndex) => itemIndex !== index))} className={secondaryButtonClass}>移除</button></div>)}<button type="button" onClick={() => setSuggestedPrompts((items) => [...items, ''])} className={`${secondaryButtonClass} self-start`}>+ 添加问题</button></div>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button><button type="submit" disabled={saving || loading} className={primaryButtonClass}>{saving ? '保存中…' : '保存专家'}</button></div>
      </form>
    </Modal>
  )
}

function Selection({
  title,
  items,
  selected,
  onToggle,
  empty,
}: {
  title: string
  items: Array<{ id: string; label: string; note: string }>
  selected: string[]
  onToggle: (id: string) => void
  empty?: string
}) {
  return <div className="flex flex-col gap-2"><span className="text-xs text-[var(--sa-alias-label-secondary)]">{title}</span>{items.length === 0 ? <span className="text-xs text-[var(--sa-alias-label-caption)]">{empty ?? '暂无可选项'}</span> : <div className="grid gap-1 sm:grid-cols-2">{items.map((item) => <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-2.5 py-2 text-xs hover:bg-[var(--sa-alias-interactive-bg-hover)]"><input type="checkbox" checked={selected.includes(item.id)} onChange={() => onToggle(item.id)} className="mt-0.5" /><span className="min-w-0"><span className="block truncate text-[var(--sa-alias-label-primary)]">{item.label}</span><span className="block truncate text-[10px] text-[var(--sa-alias-label-caption)]">{item.note}</span></span></label>)}</div>}</div>
}
