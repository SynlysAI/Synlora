/**
 * MCP 服务管理页（#/admin/mcp，仅 admin）。
 *
 * 公共 MCP = catalog 第 4 类条目（`catalog/mcp/<id>/mcp.json`，stdio /
 * streamable-http），管理语义与技能/插件一致：`catalog_policy` 控可见/内置，
 * 新增与编辑写数据目录覆盖层（`{data_dir}/public/catalog/mcp/`），删除仅对
 * 有覆盖副本的条目开放（仓库内置请用「隐藏」下线），「恢复默认」删覆盖副本。
 *
 * 结构照 SkillsAdmin：列表（名称/形态/策略/状态/操作）+ 新建/编辑模态表单
 * + 测试连接（stdio 真握手 / http initialize）+ 删除两步内联确认。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import { toast } from '@/stores/toasts'
import { CatalogPolicySwitches, FormError, GrayBadge, Modal } from './shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from './form'

/** 管理端一行（GET /api/v1/admin/mcp）。 */
interface McpAdminRow {
  id: string
  name: string
  description: string
  transport: 'stdio' | 'streamable-http' | string
  command: string
  args: string[]
  cwd: string
  env: Record<string, string>
  url: string
  header_names: string[]
  bearer_token_set: boolean
  timeout_s: number
  overlaid: boolean
  visibility: 'public' | 'hidden'
  default_enabled: boolean
  status: string
  last_error: string
  tool_count: number
}

/** 新建/编辑共用表单值（args/env 以换行/逗号原文保存，便于输入）。 */
interface McpForm {
  id: string
  name: string
  description: string
  transport: string
  command: string
  args: string
  cwd: string
  env: string
  url: string
  headers: string
  bearer_token: string
  timeout_s: string
}

/** 空表单初始值。 */
const EMPTY_FORM: McpForm = {
  id: '', name: '', description: '', transport: 'streamable-http',
  command: '', args: '', cwd: '', env: '',
  url: '', headers: '', bearer_token: '', timeout_s: '60',
}

/** "每行一个"或逗号分隔文本 → 数组。 */
function parseList(value: string): string[] {
  return value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)
}

/** "每行 key: value"文本 → 对象。 */
function parseMap(value: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of value.split('\n')) {
    const idx = line.indexOf(':')
    if (idx <= 0) continue
    const key = line.slice(0, idx).trim()
    const val = line.slice(idx + 1).trim()
    if (key && val) out[key] = val
  }
  return out
}

/** 对象 → "每行 key: value"文本（表单回填用）。 */
function mapToText(value: Record<string, string>): string {
  return Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('\n')
}

/** 行 → 表单初始值（凭证回填占位：有值显示「已配置」由留空保持语义兜底）。 */
function rowToForm(row: McpAdminRow): McpForm {
  return {
    id: row.id, name: row.name, description: row.description,
    transport: row.transport, command: row.command,
    args: row.args.join('\n'), cwd: row.cwd, env: mapToText(row.env),
    url: row.url, headers: mapToText(
      Object.fromEntries(row.header_names.map((k) => [k, '']))),
    bearer_token: row.bearer_token_set ? '已配置' : '',
    timeout_s: String(row.timeout_s),
  }
}

/** 新建/编辑模态表单：editing=null 走 POST，否则走 PUT（id 只读）。 */
function McpFormModal({ editing, onClose, onDone }: {
  editing: McpAdminRow | null
  onClose: () => void
  onDone: (message: string) => void
}) {
  const [form, setForm] = useState<McpForm>(editing ? rowToForm(editing) : EMPTY_FORM)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (patch: Partial<McpForm>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    const body = {
      id: form.id.trim(),
      name: form.name.trim(),
      description: form.description.trim(),
      transport: form.transport,
      command: form.command.trim(),
      args: parseList(form.args),
      cwd: form.cwd.trim(),
      env: parseMap(form.env),
      url: form.url.trim(),
      headers: parseMap(form.headers),
      bearer_token: form.bearer_token.trim(),
      timeout_s: Number(form.timeout_s) || 60,
    }
    try {
      if (editing) {
        await api(`/api/v1/admin/mcp/${encodeURIComponent(editing.id)}`, {
          method: 'PUT', body,
        })
      } else {
        await api('/api/v1/admin/mcp', { method: 'POST', body })
      }
      onDone(editing ? `已更新 ${body.name}` : `已新增 ${body.name}`)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={editing ? `编辑 MCP：${editing.name}` : '新增公共 MCP'} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <FormError>{error}</FormError>
        <label className="flex flex-col gap-1">
          <span className={labelClass}>ID（kebab-case，创建后不可改）</span>
          <input required disabled={!!editing} value={form.id}
                 onChange={(e) => set({ id: e.target.value })} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={labelClass}>名称</span>
          <input required value={form.name}
                 onChange={(e) => set({ name: e.target.value })} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={labelClass}>描述</span>
          <input value={form.description}
                 onChange={(e) => set({ description: e.target.value })} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={labelClass}>连接形态</span>
          <select value={form.transport}
                  onChange={(e) => set({ transport: e.target.value })} className={inputClass}>
            <option value="streamable-http">Streamable HTTP</option>
            <option value="stdio">stdio（本地命令行）</option>
          </select>
        </label>
        {form.transport === 'stdio' ? (
          <>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>启动命令</span>
              <input required value={form.command}
                     onChange={(e) => set({ command: e.target.value })} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>参数（每行或逗号一个）</span>
              <textarea rows={2} value={form.args}
                        onChange={(e) => set({ args: e.target.value })} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>工作目录（可选）</span>
              <input value={form.cwd}
                     onChange={(e) => set({ cwd: e.target.value })} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>环境变量（每行 KEY: value）</span>
              <textarea rows={2} value={form.env}
                        onChange={(e) => set({ env: e.target.value })} className={inputClass} />
            </label>
          </>
        ) : (
          <>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>服务地址（http:// 或 https://）</span>
              <input required value={form.url}
                     onChange={(e) => set({ url: e.target.value })} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>请求头（每行 KEY: value）</span>
              <textarea rows={2} value={form.headers}
                        onChange={(e) => set({ headers: e.target.value })} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>Bearer Token（留空保持原值）</span>
              <input value={form.bearer_token}
                     onChange={(e) => set({ bearer_token: e.target.value })} className={inputClass} />
            </label>
          </>
        )}
        <label className="flex flex-col gap-1">
          <span className={labelClass}>调用超时（秒）</span>
          <input type="number" min="1" value={form.timeout_s}
                 onChange={(e) => set({ timeout_s: e.target.value })} className={inputClass} />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={busy} className={primaryButtonClass}>
            {busy ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** MCP 服务管理页主组件。 */
export default function McpAdmin() {
  const [rows, setRows] = useState<McpAdminRow[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [editor, setEditor] = useState<McpAdminRow | null | undefined>(undefined)
  const [confirming, setConfirming] = useState<string | null>(null)

  const load = async () => {
    try {
      setRows(await api<McpAdminRow[]>('/api/v1/admin/mcp'))
      setError('')
    } catch (err) {
      setError(errorText(err))
    }
  }

  useEffect(() => { void load() }, [])

  const run = async (key: string, action: () => Promise<void>, success: string) => {
    setBusy(key)
    try {
      await action()
      await load()
      toast('success', success)
    } catch (err) {
      toast('error', errorText(err))
    } finally {
      setBusy('')
    }
  }

  const togglePolicy = (row: McpAdminRow,
                        next: { visibility: 'public' | 'hidden'; default_enabled: boolean }) => {
    void run(`policy:${row.id}`, async () => {
      await api(`/api/v1/admin/catalog/mcp/${encodeURIComponent(row.id)}/policy`, {
        method: 'PUT', body: next,
      })
    }, `已更新 ${row.name} 的策略`)
  }

  const testConnection = (row: McpAdminRow) => {
    void run(`test:${row.id}`, async () => {
      const out = await api<{ tools: unknown[] }>(
        `/api/v1/admin/mcp/${encodeURIComponent(row.id)}/test`, { method: 'POST' })
      toast('success', `${row.name} 连接成功，发现 ${out.tools.length} 个工具`)
    }, '')
  }

  const remove = (row: McpAdminRow) => {
    void run(`delete:${row.id}`, async () => {
      await api(`/api/v1/admin/mcp/${encodeURIComponent(row.id)}`, { method: 'DELETE' })
    }, `已删除 ${row.name}`)
  }

  const reset = (row: McpAdminRow) => {
    void run(`reset:${row.id}`, async () => {
      await api(`/api/v1/admin/mcp/${encodeURIComponent(row.id)}/reset`, { method: 'POST' })
    }, `已恢复 ${row.name} 的默认配置`)
  }

  return (
    <div>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold leading-9 text-[var(--sa-alias-label-primary)]">MCP 服务</h2>
          <p className="mt-1 text-sm text-[var(--sa-alias-label-tertiary)]">
            公共 MCP 工具服务：支持 stdio 与 Streamable HTTP，全员按可见/内置策略使用
          </p>
        </div>
        <button type="button" onClick={() => setEditor(null)}
                className={primaryButtonClass}>+ 新增 MCP</button>
      </header>

      <FormError>{error}</FormError>

      {rows === null ? (
        <p className="py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">加载中…</p>
      ) : rows.length === 0 ? (
        <p className="py-12 text-center text-sm text-[var(--sa-alias-label-caption)]">还没有公共 MCP，点击右上角新增</p>
      ) : (
        <div className="mt-5 flex flex-col gap-3">
          {rows.map((row) => (
            <div key={row.id} className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] p-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <strong className="text-sm text-[var(--sa-alias-label-primary)]">{row.name}</strong>
                    <GrayBadge>{row.transport === 'stdio' ? 'stdio' : 'HTTP'}</GrayBadge>
                    {row.overlaid && <GrayBadge>已覆盖</GrayBadge>}
                    <GrayBadge>{row.status === 'connected'
                      ? `可用 · ${row.tool_count} 工具`
                      : row.status === 'error' ? `连接失败${row.last_error ? `：${row.last_error}` : ''}`
                      : '未检测'}</GrayBadge>
                  </div>
                  <p className="mt-1 truncate text-xs text-[var(--sa-alias-label-secondary)]">
                    {row.description || row.id}
                  </p>
                  <p className="mt-0.5 truncate text-[11px] text-[var(--sa-alias-label-caption)]">
                    {row.transport === 'stdio'
                      ? `${row.command} ${row.args.join(' ')}`.trim()
                      : row.url}
                  </p>
                </div>
                <CatalogPolicySwitches item={{
                  ...row,
                  kind: 'mcp' as const,
                  source: 'builtin' as const,
                  installed: true, enabled: true,
                  visible: row.visibility === 'public',
                }} onChange={(next) => togglePolicy(row, next)} />
                <div className="flex shrink-0 items-center gap-2">
                  <button type="button" disabled={busy === `test:${row.id}`}
                          onClick={() => testConnection(row)}
                          className={secondaryButtonClass}>
                    {busy === `test:${row.id}` ? '测试中…' : '测试连接'}
                  </button>
                  <button type="button" onClick={() => setEditor(row)}
                          className={secondaryButtonClass}>编辑</button>
                  {row.overlaid && (
                    <button type="button" disabled={busy === `reset:${row.id}`}
                            onClick={() => reset(row)}
                            className={secondaryButtonClass}>恢复默认</button>
                  )}
                  {row.overlaid && (confirming === row.id ? (
                    <>
                      <button type="button" disabled={busy === `delete:${row.id}`}
                              onClick={() => remove(row)}
                              className={primaryButtonClass}>确认删除</button>
                      <button type="button" onClick={() => setConfirming(null)}
                              className={secondaryButtonClass}>取消</button>
                    </>
                  ) : (
                    <button type="button" onClick={() => setConfirming(row.id)}
                            className={secondaryButtonClass}>删除</button>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editor !== undefined && (
        <McpFormModal editing={editor} onClose={() => setEditor(undefined)}
                      onDone={(message) => { setEditor(undefined); toast('success', message); void load() }} />
      )}
    </div>
  )
}
