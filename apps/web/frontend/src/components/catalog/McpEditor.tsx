/** 用户 Streamable HTTP MCP 扩展编辑器。 */
import { useEffect, useState, type FormEvent } from 'react'
import { FormError, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import type { McpConnection } from '@/types'

type HeaderRow = { key: string; value: string }

function toRows(headers: Record<string, string>): HeaderRow[] {
  return Object.entries(headers).map(([key, value]) => ({ key, value }))
}

/** MCP 新建/编辑弹窗。 */
export default function McpEditor({
  initial,
  onClose,
}: {
  initial: McpConnection | null
  onClose: (changed: boolean) => void
}) {
  const createMcp = useMyCapabilitiesStore((state) => state.createMcp)
  const updateMcp = useMyCapabilitiesStore((state) => state.updateMcp)
  const [id, setId] = useState(initial?.id ?? '')
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [url, setUrl] = useState(initial?.url ?? '')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [headers, setHeaders] = useState<HeaderRow[]>(initial ? toRows(Object.fromEntries(initial.header_names.map((key) => [key, '']))) : [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setHeaders(initial ? toRows(Object.fromEntries(initial.header_names.map((key) => [key, '']))) : [])
  }, [initial])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    const headerMap = Object.fromEntries(headers.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value]))
    const payload: Record<string, unknown> = { name: name.trim(), description: description.trim(), url: url.trim(), headers: headerMap, enabled }
    if (!initial) payload.id = id.trim()
    if (token.trim()) payload.bearer_token = token.trim()
    try {
      if (initial) await updateMcp(initial.id, payload)
      else await createMcp(payload)
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={initial ? `编辑 MCP · ${initial.name}` : '新增 MCP 接入'} onClose={() => onClose(false)}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className={labelClass}>ID<input value={id} disabled={Boolean(initial)} required onChange={(event) => setId(event.target.value)} placeholder="lab-db" className={inputClass} /></label>
        <label className={labelClass}>名称<input value={name} required onChange={(event) => setName(event.target.value)} className={inputClass} /></label>
        <label className={labelClass}>描述<input value={description} onChange={(event) => setDescription(event.target.value)} className={inputClass} /></label>
        <label className={labelClass}>Streamable HTTP URL<input value={url} required type="url" onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/mcp" className={inputClass} /></label>
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-xs text-[var(--sa-alias-label-secondary)]"><span>Headers</span><button type="button" onClick={() => setHeaders((rows) => [...rows, { key: '', value: '' }])} className={secondaryButtonClass}>添加</button></div>
          {headers.map((row, index) => <div key={`${index}-${row.key}`} className="flex gap-2"><input value={row.key} placeholder="X-Tenant" onChange={(event) => setHeaders((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, key: event.target.value } : item))} className={inputClass} /><input value={row.value} placeholder="值" onChange={(event) => setHeaders((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} className={inputClass} /><button type="button" onClick={() => setHeaders((rows) => rows.filter((_, itemIndex) => itemIndex !== index))} className={secondaryButtonClass}>移除</button></div>)}
        </div>
        <label className={labelClass}>Bearer Token<input value={token} type="password" autoComplete="new-password" onChange={(event) => setToken(event.target.value)} placeholder={initial?.bearer_token_set ? '已配置，留空保持不变' : '可选'} className={inputClass} /></label>
        <label className="flex items-center gap-2 text-xs text-[var(--sa-alias-label-secondary)]"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用此 MCP</label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button><button type="submit" disabled={saving} className={primaryButtonClass}>{saving ? '保存中…' : '保存'}</button></div>
      </form>
    </Modal>
  )
}
