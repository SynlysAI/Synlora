/**
 * 后台任务列表（右栏「运行信息」区块）：本会话的异步 job 与状态。
 *
 * 行结构照抄 DSH `ui-jobs/JobListAction`（状态点 + 名称 + 状态词 + 时长，
 * 已终态的行整体降档、进行中的行每秒走时钟），差异只有两处、都是容器所迫：
 * - DSH 是会话头部按钮 + 弹层，我们常驻右栏面板（容器沿用本面板既有的
 *   「标题行 + 明细行」区块样式，与「工具调用」块同构）；
 * - 右栏窄，放不下 `spec.task.raman` 这样的类型徽标，故类型并入行 title，
 *   行的四个信息位不变。
 *
 * 数据来自 GET /api/v1/jobs?session_id=：任务由服务端 JobPoller 推进状态，
 * 浏览器只能轮询（与 DSH 的 host 推送不同，我们没有会话级订阅通道）。
 */
import { useEffect, useMemo, useState } from 'react'
import type { JobDoc, JobStatusValue } from '@/types'
import { api } from '@/api/client'
import { useChatStore } from '@/stores/chat'
import { formatDuration } from '@/utils/format'

/** 任务列表轮询间隔（ms）。 */
const POLL_INTERVAL_MS = 4000

/** 稳定的空列表（照抄 DSH 的 NO_TASKS）：无任务/会话不匹配时保持同一数组引用。 */
const NO_JOBS: JobDoc[] = []

/** 状态点语义（照抄 DSH dotState 的映射：running→ongoing、completed→done…）。 */
type DotState = 'ongoing' | 'done' | 'warning' | 'error' | 'idle'

/** 状态点颜色（idle 用三级文字色：队列中还没有结果，不该抢注意力）。 */
const DOT_COLOR: Record<DotState, string> = {
  ongoing: 'var(--sa-alias-state-business-primary)',
  done: 'var(--sa-alias-state-success-primary)',
  warning: 'var(--sa-alias-state-warn-primary)',
  error: 'var(--sa-alias-state-error-primary)',
  idle: 'var(--sa-alias-label-tertiary)',
}

/** 状态中文词。 */
const STATUS_LABEL: Record<JobStatusValue, string> = {
  pending: '排队中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 任务是否仍在推进（决定状态点语义、行是否降档、时长是否走时钟）。 */
function isLive(job: JobDoc): boolean {
  return job.status === 'pending' || job.status === 'running'
}

/** 任务状态 → 状态点语义。 */
function dotState(status: JobStatusValue): DotState {
  switch (status) {
    case 'running': return 'ongoing'
    case 'completed': return 'done'
    case 'cancelled': return 'warning'
    case 'failed': return 'error'
    default: return 'idle'
  }
}

/** 状态点（照抄 DSH StateDot：10% 透明度光晕 + 60% 实心核）。 */
function StateDot({ state }: { state: DotState }) {
  return (
    <span
      className="relative inline-block h-3 w-3 shrink-0"
      style={{ color: DOT_COLOR[state] }}
      aria-hidden="true"
    >
      <span className="absolute inset-0 rounded-full bg-current opacity-10" />
      <span className="absolute inset-[20%] rounded-full bg-current" />
    </span>
  )
}

/**
 * 行排序（照抄 DSH `ordered`）：进行中按开始时间升序在前，终态按结束时间降序在后。
 *
 * @param jobs 任务条目。
 * @returns 排序后的新数组。
 */
function ordered(jobs: JobDoc[]): JobDoc[] {
  return [...jobs].sort((a, b) => {
    const liveA = isLive(a)
    if (liveA !== isLive(b)) return liveA ? -1 : 1
    if (liveA) return a.created_at - b.created_at
    const fa = a.ended_at ?? a.created_at
    const fb = b.ended_at ?? b.created_at
    return fb !== fa ? fb - fa : a.created_at - b.created_at
  })
}

/** 后台任务列表组件（右栏「运行信息」区块）。 */
export default function JobList() {
  const sessionId = useChatStore((s) => s.sessionId)
  // 任务按会话缓存（照抄 DSH 的 jobsBySession）：切会话时旧数据立即失效，
  // 不必在 effect 里同步清状态（那会多触发一次渲染）
  const [cache, setCache] = useState<{ sid: string; docs: JobDoc[] }>({ sid: '', docs: [] })
  const [now, setNow] = useState(() => Date.now())
  const [cancelling, setCancelling] = useState<Set<string>>(() => new Set())
  const jobs = cache.sid === sessionId ? cache.docs : NO_JOBS

  // 轮询任务列表：跟随会话切换重建（旧请求回包按 alive 丢弃）
  useEffect(() => {
    if (!sessionId) return undefined
    let alive = true
    const load = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const docs = await api<JobDoc[]>(`/api/v1/jobs?session_id=${sessionId}`)
        if (alive) setCache({ sid: sessionId, docs })
      } catch {
        // 静默：面板刷新失败不打断用户，下一轮自行重试
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [sessionId])

  const rows = useMemo(() => ordered(jobs), [jobs])
  const liveCount = jobs.filter(isLive).length

  /** 通过任务 API 取消本人任务；下一轮列表轮询同步最终状态。 */
  const cancelJob = async (jobId: string) => {
    setCancelling((current) => new Set(current).add(jobId))
    try {
      await api(`/api/v1/jobs/${jobId}/cancel`, { method: 'POST' })
      setCache((current) => ({
        ...current,
        docs: current.docs.map((job) => (
          job._id === jobId ? { ...job, cancel_requested: true } : job
        )),
      }))
    } finally {
      setCancelling((current) => {
        const next = new Set(current)
        next.delete(jobId)
        return next
      })
    }
  }

  // 时钟只在有进行中任务时走：不动的行不需要每秒重渲染
  useEffect(() => {
    if (liveCount === 0) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [liveCount])

  return (
    <div className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
      <div className="flex items-center justify-between px-2.5 py-2 text-[13px]">
        <span className="text-[var(--sa-alias-label-secondary)]">后台任务</span>
        <span className="text-[var(--sa-alias-label-primary)]">
          {liveCount > 0 ? `进行中 ${liveCount} / 共 ${jobs.length}` : `共 ${jobs.length}`}
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="px-2.5 pb-2.5 text-xs text-[var(--sa-alias-label-caption)]">
          本会话暂无后台任务
        </div>
      ) : (
        <div className="flex max-h-[220px] flex-col gap-px overflow-y-auto border-t border-[var(--sa-alias-border-l1)] p-1">
          {rows.map((job) => {
            const live = isLive(job)
            const settled = !live
            const seconds = live
              ? now / 1000 - job.created_at
              : (job.ended_at ?? job.created_at) - job.created_at
            const failed = job.status === 'failed'
            const title =
              `${job.kind} · ${STATUS_LABEL[job.status]}` +
              (job.error ? ` · ${job.error}` : '')
            return (
              <div
                key={job._id}
                className={`flex items-center gap-2 rounded-[8px] px-2 py-1.5 text-[13px] leading-[18px] ${
                  settled
                    ? 'text-[var(--sa-alias-label-tertiary)]'
                    : 'text-[var(--sa-alias-label-primary)]'
                }`}
                title={title}
              >
                <StateDot state={dotState(job.status)} />
                <span className="min-w-0 flex-1 truncate">{job.label || job.kind}</span>
                {/* 轮询失败优先于状态词展示：状态卡在旧值上时，状态词本身不可信
                    （后端对失败也刻意不改状态，防网络抖动误判） */}
                {job.poll_failures ? (
                  <span className="shrink-0 text-[11px] text-[var(--sa-alias-state-warn-primary)]">
                    同步异常 ×{job.poll_failures}
                  </span>
                ) : (
                  <span
                    className={`shrink-0 text-[11px] ${
                      failed
                        ? 'text-[var(--sa-alias-state-error-primary)]'
                        : 'text-[var(--sa-alias-label-tertiary)]'
                    }`}
                  >
                    {STATUS_LABEL[job.status]}
                  </span>
                )}
                <span className="shrink-0 text-[11px] tabular-nums text-[var(--sa-alias-label-tertiary)]">
                  {formatDuration(seconds)}
                </span>
                {live && (
                  <button
                    type="button"
                    disabled={cancelling.has(job._id) || job.cancel_requested}
                    onClick={() => void cancelJob(job._id)}
                    className="shrink-0 rounded-[6px] px-1.5 py-0.5 text-[11px] text-[var(--sa-alias-state-error-primary)] hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {cancelling.has(job._id) || job.cancel_requested ? '停止中' : '取消'}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
