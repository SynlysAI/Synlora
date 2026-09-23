/** 专家能力编排编辑器：人设、技能、MCP 与工具统一配置。 */
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useCatalogStore } from '@/stores/catalog'
import { Selection } from '@/components/catalog/Selection'
import { useMyCapabilitiesStore, type ExpertDraft } from '@/stores/myCapabilities'
import { api } from '@/api/client'
import { TOOL_LABELS } from '@/components/chat/toolLabels'
import type { MyCapability } from '@/types'

/** 平台工具拉取失败时的兜底清单（TOOL_LABELS 键，与后端注册表人工对齐）。 */
const FALLBACK_TOOLS = Object.keys(TOOL_LABELS)

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
  const [platformTools, setPlatformTools] = useState<Array<{ name: string; description: string }>>([])
  const [loading, setLoading] = useState(Boolean(initial))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    // 平台工具清单实取注册表（内置 + 宿主 + 插件），失败静默回落兜底清单
    void Promise.all([
      loadMine(),
      loadMcps(),
      api<Array<{ name: string; description: string }>>('/api/v1/tools').then(setPlatformTools).catch(() => {}),
    ])
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
    }).catch((err) => setError(errorText(err))).finally(() => setLoading(false))
  }, [initial, loadDetail])

  const skills = useMemo(() => items.filter((item) => item.kind === 'skill' && item.enabled), [items])
  const toolOptions = useMemo(() => {
    const platform = platformTools.length ? platformTools.map((item) => item.name) : FALLBACK_TOOLS
    const remote = mcps.filter((mcp) => mcp.enabled).flatMap((mcp) => mcp.tools.map((tool) => `mcp.${mcp.id}.${tool.name.replace(/[^A-Za-z0-9_-]/g, '_')}`))
    return [...new Set([...platform, ...remote])]
  }, [platformTools, mcps])
  const toolNotes = useMemo(() => {
    const notes = new Map<string, string>()
    for (const item of platformTools) notes.set(item.name, item.description)
    return notes
  }, [platformTools])

  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
  const draft = (): ExpertDraft => ({
    name: name.trim(), avatar: avatar.trim(), description: description.trim(), system_prompt: systemPrompt,
    skill_refs: skillRefs, mcp_refs: mcpRefs, tool_whitelist: toolWhitelist,
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
        <Selection title="可用工具（不选表示全部内置工具）" items={toolOptions.map((item) => ({ id: item, label: item, note: item.startsWith('mcp.') ? 'MCP 远程工具' : toolNotes.get(item) ?? TOOL_LABELS[item] ?? '平台工具' }))} selected={toolWhitelist} onToggle={(id) => setToolWhitelist(toggle(toolWhitelist, id))} />
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button><button type="submit" disabled={saving || loading} className={primaryButtonClass}>{saving ? '保存中…' : '保存专家'}</button></div>
      </form>
    </Modal>
  )
}
