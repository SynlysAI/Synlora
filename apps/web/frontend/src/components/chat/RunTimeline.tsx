/**
 * 会话运行时间线：按 run 聚合后台记录，支持展开查看该 run 的事件序列。
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/api/client'
import type { SessionEvent, SessionRun } from '@/types'

function formatTime(value: number | null | undefined) {
  if (!value) return '—'
  return new Date(value * 1000).toLocaleString()
}

function formatDuration(value: number | null | undefined) {
  if (value == null) return '—'
  if (value < 1000) return `${value}ms`
  if (value < 60_000) return `${(value / 1000).toFixed(1)}s`
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`
}

/** Run 元数据与事件时间线。 */
export default function RunTimeline({ sessionId, refreshKey }: { sessionId: string; refreshKey: number }) {
  const [runs, setRuns] = useState<SessionRun[]>([])
  const [events, setEvents] = useState<SessionEvent[]>([])
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [runDocs, eventDocs] = await Promise.all([
          api<SessionRun[]>(`/api/v1/sessions/${sessionId}/runs`),
          api<SessionEvent[]>(`/api/v1/sessions/${sessionId}/events?after_seq=-1`),
        ])
        if (cancelled) return
        setRuns(runDocs)
        setEvents(eventDocs)
        setError('')
      } catch (err) {
        if (!cancelled) setError((err as Error).message)
      }
    })()
    return () => { cancelled = true }
  }, [sessionId, refreshKey])

  const eventsByRun = useMemo(() => {
    const groups = new Map<string, SessionEvent[]>()
    for (const event of events) {
      const runId = String(event.payload?.run_id ?? '')
      if (!runId) continue
      groups.set(runId, [...(groups.get(runId) ?? []), event])
    }
    return groups
  }, [events])

  if (!runs.length) return null

  return (
    <section className="mx-auto w-full max-w-3xl border-b border-[var(--sa-alias-border-subtle)] px-4 py-3 sm:px-6">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="text-[13px] font-semibold text-[var(--sa-alias-label-primary)]">运行记录</h2>
        <span className="text-[12px] text-[var(--sa-alias-label-tertiary)]">{runs.length} 个 run</span>
      </div>
      {error && <p className="text-[12px] text-[var(--sa-alias-state-error-primary)]">{error}</p>}
      <ol className="divide-y divide-[var(--sa-alias-border-subtle)]">
        {runs.map((run) => {
          const runEvents = eventsByRun.get(run._id) ?? []
          const open = openRun === run._id
          return (
            <li key={run._id}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpenRun(open ? null : run._id)}
                className="flex w-full items-baseline justify-between gap-3 py-2 text-left"
              >
                <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                  {run.label || run._id}
                </span>
                <span className="shrink-0 text-[12px] text-[var(--sa-alias-label-tertiary)]">
                  {run.status} · {formatDuration(run.duration_ms)} · {run.event_count ?? runEvents.length} 事件
                </span>
              </button>
              {open && (
                <div className="pb-3">
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] sm:grid-cols-4">
                    <div><dt className="text-[var(--sa-alias-label-tertiary)]">Synlora run</dt><dd className="break-all">{run._id}</dd></div>
                    <div><dt className="text-[var(--sa-alias-label-tertiary)]">Plane run</dt><dd className="break-all">{run.external_run_id || '—'}</dd></div>
                    <div><dt className="text-[var(--sa-alias-label-tertiary)]">开始</dt><dd>{formatTime(run.started_at)}</dd></div>
                    <div><dt className="text-[var(--sa-alias-label-tertiary)]">结束</dt><dd>{formatTime(run.ended_at)}</dd></div>
                  </dl>
                  <ol className="mt-2 max-h-48 space-y-1 overflow-y-auto border-l border-[var(--sa-alias-border-subtle)] pl-3">
                    {runEvents.map((event) => (
                      <li key={`${run._id}-${event.seq}`} className="text-[12px] text-[var(--sa-alias-label-secondary)]">
                        {event.type}
                        {event.payload?.name ? ` · ${String(event.payload.name)}` : ''}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
