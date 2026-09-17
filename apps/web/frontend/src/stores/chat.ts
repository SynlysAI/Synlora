/**
 * 聊天核心 store：SSE 流式接收、事件流投影、历史回放与运行取消。
 *
 * 投影唯一入口 reduceEvent（纯函数）：SSE 实时流与 GET events 历史回放共用
 * 同一 reducer，保证刷新/重进会话后的渲染结果与实时流完全一致。
 *
 * 时序要点（见 harness agent.py）：
 * - llm/delta 先行累积为流式气泡；assistant/message 携带完整 content 定稿并
 *   清空累积（避免重复）；
 * - 工具调用前的解说文本在实时流里来自 delta，落盘则冗余存进 tool/call.content；
 *   故 tool/call 时将其中一段定稿为助手消息（优先 payload.content，见 reduceEvent）；
 * - 终止标志是 turn/end / turn/aborted 事件而非连接关闭；收尾时残留累积
 *   文本（中止/断连）定稿保留。
 */
import { create } from 'zustand'
import type { MessageAttachment, SessionEvent, ToolCallPayload, ToolResultPayload } from '@/types'
import { api, ApiError } from '@/api/client'
import { streamSse } from '@/api/sse'
import { pickSelectedAssistant, useAssistantsStore } from './assistants'
import { useProjectsStore } from './projects'
import { useSessionsStore } from './sessions'

/** 聊天条目视图模型（由会话事件投影）。 */
export type ChatItem =
  | {
      kind: 'user'
      text: string
      attachments?: MessageAttachment[]
      /** 系统唤醒消息（后台任务完成注入）：渲染成居中提示条而非用户气泡。 */
      systemWake?: boolean
    }
  | { kind: 'reasoning'; text: string }
  | {
      kind: 'assistant'
      content: string
      /** 本轮任务用时（秒，turn/end 事件计算）。 */
      elapsedMs?: number
      /** 本轮 token 用量（turn/end payload 携带）。 */
      usage?: { prompt_tokens: number; completion_tokens: number }
      /** 本轮完成时间戳（turn/end ts，秒）。 */
      finishedTs?: number
    }
  | { kind: 'tool'; call: ToolCallPayload; result?: ToolResultPayload }
  | {
      /** ask_user 问题卡（ask/user 事件；answer 由配对的 tool/result 回填）。 */
      kind: 'ask_user'
      callId: string
      query: string
      options: { label: string; description?: string }[]
      /** 多问题模式（payload.questions，与 query 二选一）：逐题作答一次提交。 */
      questions?: {
        question: string
        header?: string
        options: { label: string; description?: string }[]
        multiSelect?: boolean
      }[]
      /** 管线强制审批卡（payload.kind=approval）：允许/拒绝按钮提交固定文案。 */
      approval?: { tool: string; argsPreview?: string }
      answer?: string
    }
  | {
      /** file.send 交付卡（file/send 事件；fileId 走既有下载端点）。 */
      kind: 'file_send'
      fileId: string
      filename: string
      size: number
      note?: string
    }

/**
 * 摘出「待作答」的交互卡（问答 / 管线审批），供吸附槽渲染。
 *
 * 问答严格串行（上一张作答后才会发下一张），故只可能是最后一条 ask_user；
 * 未在运行（回放中断轮次 / 已结束）时返回 null——这类卡片没有可回传的 run，
 * 留在消息流里按只读态展示。
 *
 * @param items 投影后的聊天条目。
 * @param streaming 是否正在接收一轮回复。
 * @returns 待作答的 ask_user 条目（无则 null）。
 */
export function pickPendingAsk(
  items: ChatItem[],
  streaming: boolean,
): Extract<ChatItem, { kind: 'ask_user' }> | null {
  if (!streaming) return null
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind !== 'ask_user') continue
    return it.answer == null ? it : null
  }
  return null
}

/** reducer 工作状态：条目列表 + 流式正文/思考累积 + 本轮起始时间。 */
export interface ChatProjection {
  items: ChatItem[]
  streamingText: string
  /** 流式思考累积（reasoning/delta）。 */
  thinkingText: string
  /** 本轮 turn/start 的 ts（turn/end 计算用时）。 */
  turnStartTs: number | null
}

/** 单个工具的调用统计（右栏运行信息展示）。 */
export interface ToolStat {
  /** 调用次数（tool/call 计数）。 */
  calls: number
  /** 成功次数（tool/result.ok 计数）。 */
  ok: number
  /** 失败次数。 */
  fail: number
}

/** 会话事件流统计（SSE 实时流与历史回放共用同一 reducer 累积）。 */
export interface RunStats {
  /** 已接收的持久事件总数（瞬态 delta 不计，与回放口径一致）。 */
  events: number
  /** 完成的轮数（turn/end + turn/aborted）。 */
  turns: number
  /** 其中被中止的轮数。 */
  aborted: number
  /** error 事件数。 */
  errors: number
  /** 按工具名聚合的调用统计。 */
  byTool: Record<string, ToolStat>
}

/** 空统计初值（浅拷贝 byTool 保证各会话互不污染）。 */
export const emptyStats = (): RunStats => ({
  events: 0,
  turns: 0,
  aborted: 0,
  errors: 0,
  byTool: {},
})

/** 瞬态事件类型（只推 SSE 不落盘）：统计与落盘/回放口径对齐，一律不计入。 */
const TRANSIENT_EVENT_TYPES: ReadonlySet<string> = new Set(['llm/delta', 'reasoning/delta'])

/**
 * 会话事件 → 运行统计的纯函数累积（历史回放 reduce 与 SSE 逐事件共用）。
 *
 * 瞬态事件（llm/delta、reasoning/delta）不计入：SSE 流里它们逐 token 一条
 * （一轮就有几十条），而刷新回放只读落盘副本（瞬态不落盘）——不过滤的话
 * 流式中与刷新后的"事件总数"会对不上（如 52 vs 6）。
 *
 * @param stats 当前统计。
 * @param ev 会话事件。
 * @returns 应用事件后的新统计（不可变更新）。
 */
export function reduceStats(stats: RunStats, ev: SessionEvent): RunStats {
  if (TRANSIENT_EVENT_TYPES.has(ev.type)) return stats
  const next: RunStats = { ...stats, events: stats.events + 1 }
  const payload = ev.payload ?? {}
  switch (ev.type) {
    case 'turn/end':
      return { ...next, turns: stats.turns + 1 }
    case 'turn/aborted':
      return { ...next, turns: stats.turns + 1, aborted: stats.aborted + 1 }
    case 'error':
      return { ...next, errors: stats.errors + 1 }
    case 'tool/call': {
      const call = payload.tool_call as { name?: string } | undefined
      const name = String(call?.name ?? '')
      const cur = stats.byTool[name] ?? { calls: 0, ok: 0, fail: 0 }
      return {
        ...next,
        byTool: { ...stats.byTool, [name]: { ...cur, calls: cur.calls + 1 } },
      }
    }
    case 'tool/result': {
      const name = String(payload.name ?? '')
      const cur = stats.byTool[name] ?? { calls: 0, ok: 0, fail: 0 }
      const ok = payload.ok === true
      return {
        ...next,
        byTool: {
          ...stats.byTool,
          [name]: { ...cur, ok: cur.ok + (ok ? 1 : 0), fail: cur.fail + (ok ? 0 : 1) },
        },
      }
    }
    default:
      return next
  }
}

/** localStorage 已读 seq 键前缀（断连恢复备用，V1 重进会话全量回放）。 */
const LAST_SEQ_PREFIX = 'sa.chat.lastseq.'

/** 进行中流的 AbortController（模块级，避免进 state 引发多余渲染）。 */
let activeController: AbortController | null = null

/** 读取会话已读 seq（无记录返回 -1）。 */
function readLastSeq(sessionId: string): number {
  const raw = localStorage.getItem(LAST_SEQ_PREFIX + sessionId)
  const n = raw ? Number(raw) : -1
  return Number.isFinite(n) ? n : -1
}

/** 写入会话已读 seq。 */
function writeLastSeq(sessionId: string, seq: number): void {
  localStorage.setItem(LAST_SEQ_PREFIX + sessionId, String(seq))
}

/** 增量同步轮询间隔（ms）：与后端 JobPoller 的 5s 同档（比它更快没有意义）。 */
const SYNC_INTERVAL_MS = 5000

/** 增量轮询句柄与重入旗标（模块级：不进 state，避免每次 tick 触发渲染）。 */
let syncTimer: number | null = null
let syncInFlight = false

/**
 * 需要全量对账：上一轮流没等到 turn/end 就断了（网络中断/页签挂起）。
 *
 * 该路径下 finally 会把残留累积文本定稿成本地条目（防丢字），这段文本若也已
 * 落盘，增量同步会把它再上屏一次。故下一次同步改走 after_seq=-1 全量重放
 * （reducer 幂等，以落盘事件为准），把本地残留抹平——这也是文件头注释里
 * "重连经 GET /sessions/{id}/events?after_seq=N 补齐" 的落地。
 */
let needsReconcile = false

/** 启动会话事件增量轮询（绑定会话时调用；重复调用幂等）。 */
function startSyncPolling(): void {
  if (syncTimer != null) return
  syncTimer = window.setInterval(() => {
    void useChatStore.getState().syncEvents()
  }, SYNC_INTERVAL_MS)
}

/** 停止增量轮询（解绑会话时调用）。 */
function stopSyncPolling(): void {
  if (syncTimer == null) return
  window.clearInterval(syncTimer)
  syncTimer = null
}

/**
 * 会话事件 → 视图模型的纯函数投影（SSE 流与历史回放共用）。
 *
 * @param state 当前投影（条目列表 + 流式累积文本）。
 * @param ev 会话事件。
 * @returns 应用事件后的新投影（不可变更新）。
 */
export function reduceEvent(state: ChatProjection, ev: SessionEvent): ChatProjection {
  const payload = ev.payload ?? {}
  switch (ev.type) {
    case 'turn/start':
      return { ...state, turnStartTs: ev.ts }
    case 'user/message': {
      // 用户消息（含 steering 插话）逐条展示；附件（已上传文件引用）随事件展示。
      // 后台任务完成唤醒（payload.kind=job_completed）是系统注入消息，分流成提示条、
      // 不显示成用户气泡。**分流只认 kind**（job_id 是数据不是标志）：若拿 job_id
      // 是否存在当分流依据，id 缺失时唤醒原文会回落到用户气泡上屏
      const attachments = Array.isArray(payload.attachments)
        ? (payload.attachments as MessageAttachment[])
        : undefined
      const item: ChatItem = {
        kind: 'user',
        text: String(payload.text ?? ''),
        attachments,
        ...(payload.kind === 'job_completed' ? { systemWake: true } : {}),
      }
      return { ...state, items: [...state.items, item] }
    }
    case 'llm/delta':
      return { ...state, streamingText: state.streamingText + String(payload.text ?? '') }
    case 'reasoning/delta':
      return { ...state, thinkingText: state.thinkingText + String(payload.text ?? '') }
    case 'assistant/reasoning': {
      // 思考定稿：以事件 content 为准（回放与 SSE 流一致），清空流式累积
      const text =
        typeof payload.content === 'string' && payload.content
          ? payload.content
          : state.thinkingText
      if (!text) return { ...state, thinkingText: '' }
      return { ...state, items: [...state.items, { kind: 'reasoning', text }], thinkingText: '' }
    }
    case 'assistant/message': {
      // 定稿：以事件 content 为准（与 delta 累积一致），清空累积避免重复
      const content =
        typeof payload.content === 'string' && payload.content
          ? payload.content
          : state.streamingText
      if (!content) return { ...state, streamingText: '' }
      return { ...state, items: [...state.items, { kind: 'assistant', content }], streamingText: '' }
    }
    case 'tool/call': {
      // 工具调用前若有思考/解说文本，先定稿。解说文本在实时流里只出现在 delta
      // 中，同时被冗余写进 tool/call.content 落盘——**优先取 payload.content**，
      // 这样刷新/重进会话（无 delta）也能还原同一段解说，实时与回放渲染一致。
      const narration =
        (typeof payload.content === 'string' && payload.content) || state.streamingText
      let items = state.items
      if (state.thinkingText) {
        items = [...items, { kind: 'reasoning', text: state.thinkingText }]
      }
      if (narration) {
        items = [...items, { kind: 'assistant', content: narration }]
      }
      return {
        ...state,
        items: [...items, { kind: 'tool', call: payload as unknown as ToolCallPayload }],
        streamingText: '',
        thinkingText: '',
      }
    }
    case 'ask/user': {
      const isApproval = payload.kind === 'approval'
      const rawQuestions = Array.isArray(payload.questions)
        ? (payload.questions as Record<string, unknown>[])
        : undefined
      const questions = rawQuestions
        ?.filter((q) => String(q.question ?? '').trim())
        .map((q) => ({
          question: String(q.question ?? ''),
          header: q.header ? String(q.header) : undefined,
          options:
            (q.options as { label: string; description?: string }[] | undefined) ?? [],
          multiSelect: q.multi_select === true,
        }))
      const item: ChatItem = {
        kind: 'ask_user',
        callId: String(payload.tool_call_id ?? ''),
        query: String(payload.query ?? ''),
        options: isApproval
          ? []
          : (payload.options as { label: string; description?: string }[] | undefined) ?? [],
        questions: !isApproval && questions?.length ? questions : undefined,
        approval: isApproval
          ? {
              tool: String(payload.tool ?? ''),
              argsPreview: payload.args_preview ? String(payload.args_preview) : undefined,
            }
          : undefined,
      }
      return { ...state, items: [...state.items, item] }
    }
    case 'file/send': {
      const item: ChatItem = {
        kind: 'file_send',
        fileId: String(payload.file_id ?? ''),
        filename: String(payload.filename ?? ''),
        size: Number(payload.size ?? 0),
        note: payload.note ? String(payload.note) : undefined,
      }
      return { ...state, items: [...state.items, item] }
    }
    case 'tool/result': {
      // 由后往前配对最近的同 id 未配对 tool 条目
      const result = payload as unknown as ToolResultPayload
      const idx = state.items.findLastIndex(
        (it) =>
          it.kind === 'tool' && it.call.tool_call.id === result.tool_call_id && !it.result,
      )
      // ask_user / 审批卡的答复同时回填卡片（callId 全局唯一配对；审批的
      // tool/result name 是被审批工具而非 ask_user，故不按 name 过滤）
      {
        const askIdx = state.items.findLastIndex(
          (it) => it.kind === 'ask_user' && it.callId === result.tool_call_id && !it.answer,
        )
        if (askIdx !== -1) {
          const items = [...state.items]
          const ask = items[askIdx]
          if (ask.kind === 'ask_user') {
            items[askIdx] = {
              ...ask,
              // 审批卡只显示终态（工具结果详情在配对的工具行）；问答卡显示回答原文
              answer: ask.approval
                ? result.ok || result.error !== 'denied'
                  ? '已允许'
                  : '已拒绝'
                : String(result.content ?? ''),
            }
          }
          state = { ...state, items }
        }
      }
      if (idx === -1) return state
      const target = state.items[idx]
      if (target.kind !== 'tool') return state
      const items = [...state.items]
      items[idx] = { kind: 'tool', call: target.call, result }
      return { ...state, items }
    }
    case 'turn/end':
    case 'turn/aborted': {
      // 收尾：残留流式文本（中止/断连）定稿保留；用时/用量写回本轮定稿 assistant
      let items = state.items
      if (state.thinkingText) {
        items = [...items, { kind: 'reasoning', text: state.thinkingText }]
      }
      if (state.streamingText) {
        items = [...items, { kind: 'assistant', content: state.streamingText }]
      }
      const elapsedMs =
        state.turnStartTs != null ? Math.max(0, (ev.ts - state.turnStartTs) * 1000) : undefined
      const usageRaw = payload.usage as
        | { prompt_tokens?: number; completion_tokens?: number }
        | undefined
      const usage =
        usageRaw && (usageRaw.prompt_tokens || usageRaw.completion_tokens)
          ? {
              prompt_tokens: Number(usageRaw.prompt_tokens ?? 0),
              completion_tokens: Number(usageRaw.completion_tokens ?? 0),
            }
          : undefined
      // 找本轮最后一个 assistant 条目（从尾往前），把元数据挂上去
      if (elapsedMs != null || usage) {
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'user') break
          if (it.kind === 'assistant') {
            items = [...items]
            items[i] = { ...it, elapsedMs, usage, finishedTs: ev.ts }
            break
          }
        }
      }
      return { items, streamingText: '', thinkingText: '', turnStartTs: null }
    }
    default:
      // error 不产生条目（错误提示由 store 层处理）
      return state
  }
}

interface ChatState {
  /** 绑定的会话 id（null 未绑定）。 */
  sessionId: string | null
  /** 投影后的聊天条目。 */
  messages: ChatItem[]
  /** 当前流式气泡累积文本（渲染为流式助手消息）。 */
  streamingText: string
  /** 当前流式思考累积文本（渲染为思考折叠区）。 */
  thinkingText: string
  /** 当前 turn 起始 ts（投影内部用于计算用时，跨 set 保持）。 */
  turnStartTs: number | null
  /** 是否正在接收一轮回复。 */
  streaming: boolean
  /** 已读最大事件 seq。 */
  lastSeq: number
  /** 用户可见错误提示（429 / 网络错误等）。 */
  error: string | null
  /** 事件流运行统计（右栏运行信息面板消费）。 */
  stats: RunStats
  /** 进行中运行的 run_id（stop 取消用，来自 turn/start）。 */
  activeRunId: string | null
  /** 会话代际：每次绑定新会话自增，旧流事件按代丢弃。 */
  epoch: number
  /** 绑定会话并全量回放历史事件。 */
  loadHistory: (sessionId: string) => Promise<void>
  /**
   * 拉取本会话的增量事件并合并进消息流（后台唤醒轮可见性的唯一来源）。
   *
   * 唤醒轮由服务端 JobPoller 起，不经过浏览器发起的 SSE 请求——没有本方法时，
   * 用户必须刷新或切换会话才能看到「任务已完成」提示条与模型的主动汇报。
   */
  syncEvents: () => Promise<void>
  /**
   * 发送消息：optimistic 用户气泡 + SSE 流接收。
   *
   * @param text 消息文本。
   * @param skills 本轮勾选的技能名（空/缺省 = 用全部可用技能，后端 requested_skills）。
   * @param attachments 随消息发送的附件（已上传文件的元数据；空/缺省 = 无附件）。
   */
  send: (text: string, skills?: string[], attachments?: MessageAttachment[]) => Promise<void>
  /** 停止当前运行（POST cancel；失败兜底断开本地流）。 */
  stop: () => Promise<void>
  /** 运行中插话（steering：不打断当前步骤，下一个 step 边界注入）。 */
  steer: (text: string) => Promise<void>
  /** 回答运行中 ask_user 的问题（POST answer；卡片随后由 tool/result 回填答案）。 */
  answerAsk: (text: string) => Promise<void>
  /** 清除错误提示。 */
  clearError: () => void
  /** 解绑会话并重置全部状态。 */
  reset: () => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  messages: [],
  streamingText: '',
  thinkingText: '',
  turnStartTs: null,
  streaming: false,
  lastSeq: -1,
  error: null,
  stats: emptyStats(),
  activeRunId: null,
  epoch: 0,

  loadHistory: async (sessionId) => {
    // 已绑定同一会话则跳过：懒创建路径里 send 已绑过一次，紧接着 ChatPanel 的
    // currentId effect 还会再调一次，这里挡住那次重复请求（调用点只有「会话切换」，
    // 不存在「刷新同一会话」的用法）。
    if (get().sessionId === sessionId) return
    // 切会话：断开旧流（run 由后端执行完落盘，回读即可见）
    activeController?.abort()
    activeController = null
    const epoch = get().epoch + 1
    set({
      sessionId,
      messages: [],
      streamingText: '',
      thinkingText: '',
      turnStartTs: null,
      streaming: false,
      activeRunId: null,
      error: null,
      stats: emptyStats(),
      lastSeq: readLastSeq(sessionId),
      epoch,
    })
    // 绑定期开始增量轮询：服务端起的唤醒轮没有 SSE 通道，只能靠它上屏
    startSyncPolling()
    try {
      const events = await api<SessionEvent[]>(
        `/api/v1/sessions/${sessionId}/events?after_seq=-1`,
      )
      if (get().epoch !== epoch) return
      const final = events.reduce(reduceEvent, { items: [], streamingText: '', thinkingText: '', turnStartTs: null })
      const stats = events.reduce(reduceStats, emptyStats())
      const lastSeq = events.length ? events[events.length - 1].seq : -1
      writeLastSeq(sessionId, lastSeq)
      set({ messages: final.items, lastSeq, stats })
    } catch (err) {
      if (get().epoch === epoch) set({ error: (err as Error).message })
    }
  },

  syncEvents: async () => {
    const { sessionId, streaming, epoch } = get()
    // 跳过条件：无绑定会话；本轮流式中（投影状态归 SSE 持有，并发 reduce 会把
    // 同一条事件重复上屏）；页面不可见（后台标签页不做无谓轮询）；上一次未回包
    if (!sessionId || streaming || syncInFlight) return
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    const full = needsReconcile
    syncInFlight = true
    try {
      const events = await api<SessionEvent[]>(
        `/api/v1/sessions/${sessionId}/events?after_seq=${full ? -1 : get().lastSeq}`,
      )
      const cur = get()
      // 回包期间可能已切会话或开了一轮新对话，状态不再是本方法的基线
      if (cur.epoch !== epoch || cur.sessionId !== sessionId || cur.streaming) return
      if (!events.length && !full) return
      // 与 SSE 同一条 reducer（瞬态 delta 不落盘，故这里只回放定稿事件）：
      // 增量在同一条流上续接，全量对账则从空投影重建（幂等，顺带抹掉本地残留）
      const proj = events.reduce(reduceEvent, full
        ? { items: [], streamingText: '', thinkingText: '', turnStartTs: null }
        : {
            items: cur.messages,
            streamingText: cur.streamingText,
            thinkingText: cur.thinkingText,
            turnStartTs: cur.turnStartTs,
          })
      const lastSeq = events.length ? events[events.length - 1].seq : cur.lastSeq
      set({
        messages: proj.items,
        streamingText: proj.streamingText,
        thinkingText: proj.thinkingText,
        turnStartTs: proj.turnStartTs,
        stats: full
          ? events.reduce(reduceStats, emptyStats())
          : events.reduce(reduceStats, cur.stats),
        lastSeq: Math.max(cur.lastSeq, lastSeq),
      })
      needsReconcile = false
      writeLastSeq(sessionId, get().lastSeq)
    } catch {
      // 静默：增量同步失败不打扰用户（不设 error），下一轮自行重试
    } finally {
      syncInFlight = false
    }
  },

  send: async (text, skills, attachments) => {
    const trimmed = text.trim()
    if (!trimmed || get().streaming) return

    // 懒创建：点「新会话」只进草稿态（不落库），**首次真正发送**才建会话——
    // 否则每点一次新会话就留一条空会话。建完立刻绑定，再走原来的 optimistic + SSE 流程。
    if (!get().sessionId) {
      try {
        const assistant = pickSelectedAssistant(useAssistantsStore.getState())
        const projectId = useProjectsStore.getState().currentId ?? undefined
        // 草稿态选好的模型随建会话一起下发：无专家时没有可回落的模型服务，
        // 不带这个字段后端会 422「未指定模型服务」；插件开关同带（null = 未选过）
        const modelProviderId = useSessionsStore.getState().draftModelProviderId
        const enabledPlugins = useSessionsStore.getState().draftEnabledPlugins
        const session = await useSessionsStore.getState().create(
          assistant?._id ?? null,
          { projectId, modelProviderId, ...(enabledPlugins ? { enabledPlugins } : {}) },
        )
        // 新会话必然没有历史事件，直接置绑定态——**不要**走 loadHistory：
        // 它的 set({messages: ...}) 发生在 await 之后，会把下面刚写进去的
        // optimistic 用户气泡清成 []，表现为「已在流式、界面却还停在空态」。
        set((s) => ({
          sessionId: session._id,
          messages: [],
          streamingText: '',
          thinkingText: '',
          turnStartTs: null,
          activeRunId: null,
          error: null,
          stats: emptyStats(),
          lastSeq: -1,
          epoch: s.epoch + 1,
        }))
        startSyncPolling()
      } catch (err) {
        set({ error: (err as Error).message || '新建会话失败' })
        return
      }
    }
    const { sessionId, epoch } = get()
    if (!sessionId) return

    const controller = new AbortController()
    activeController = controller
    // optimistic 用户气泡（含附件 chips）；后端的 user/message 事件不再重复投影
    set((s) => ({
      messages: [...s.messages, {
        kind: 'user',
        text: trimmed,
        ...(attachments?.length ? { attachments } : {}),
      }],
      streaming: true,
      error: null,
      streamingText: '',
      thinkingText: '',
      turnStartTs: null,
    }))

    // 是否收到过终止事件（turn/end / turn/aborted）：没收到就说明是断连收场。
    // 声明在 try 之外——finally 与 try 是两个块作用域，里面声明外面看不见
    let turnEnded = false
    try {
      // 本流内首条非 steering 用户消息已被 optimistic 气泡渲染（跳过投影防
      // 重复）；后续同类消息 = 插话兜底续跑轮的输入，须投影上屏并开启新 turn
      // 分组（否则与回放视图不一致：上一轮回答会被折进任务用时区）
      let ownUserMsgSeen = false
      await streamSse(
        `/api/v1/sessions/${sessionId}/messages`,
        // 技能/附件非空才带对应字段（缺省语义由后端区分：skills = 全部可用）
        {
          text: trimmed,
          ...(skills && skills.length ? { skills } : {}),
          ...(attachments && attachments.length
            ? { attachments: attachments.map((a) => ({ file_id: a.file_id })) }
            : {}),
        },
        (m) => {
          if (get().epoch !== epoch) return
          let ev: SessionEvent
          try {
            ev = JSON.parse(m.data) as SessionEvent
          } catch {
            return
          }
          set((s) => {
            const stats = reduceStats(s.stats, ev)
            // 普通用户消息已被 optimistic 气泡渲染，跳过投影避免重复；
            // steering 插话与续跑轮输入没有 optimistic，走投影正常上屏
            if (ev.type === 'user/message' && !ev.payload?.steering) {
              if (!ownUserMsgSeen) {
                ownUserMsgSeen = true
                return { lastSeq: Math.max(s.lastSeq, ev.seq), stats }
              }
            }
            const next = reduceEvent(
              { items: s.messages, streamingText: s.streamingText, thinkingText: s.thinkingText, turnStartTs: s.turnStartTs },
              ev,
            )
            return {
              messages: next.items,
              streamingText: next.streamingText,
              thinkingText: next.thinkingText,
              turnStartTs: next.turnStartTs,
              lastSeq: Math.max(s.lastSeq, ev.seq),
              stats,
            }
          })
          if (get().epoch !== epoch) return
          if (ev.type === 'turn/end' || ev.type === 'turn/aborted') turnEnded = true
          if (ev.type === 'turn/start') {
            set({ activeRunId: String(ev.payload?.run_id ?? '') || null })
          } else if (ev.type === 'error') {
            const msg = String(ev.payload?.message ?? '运行出错')
            set({ error: `${String(ev.payload?.code ?? 'error')}: ${msg}` })
          }
        },
        controller.signal,
      )
    } catch (err) {
      if (get().epoch === epoch) {
        set({
          error:
            err instanceof ApiError && err.status === 429
              ? '该会话已有进行中的消息，请稍候或停止后重试'
              : (err as Error).message || '发送失败',
        })
      }
    } finally {
      activeController = null
      if (get().epoch === epoch) {
        set((s) => ({
          streaming: false,
          activeRunId: null,
          // 流断在终止事件之前（网络中断等）：残留累积文本定稿，避免丢字
          messages: [
            ...s.thinkingText
              ? [{ kind: 'reasoning' as const, text: s.thinkingText }]
              : [],
            ...s.streamingText
              ? [{ kind: 'assistant' as const, content: s.streamingText }]
              : [],
            ...s.messages,
          ],
          streamingText: '',
          thinkingText: '',
        }))
        // 没等到 terminate 事件就收场（网络中断等）：下一轮同步走全量对账，
        // 用落盘事件覆盖上面的本地残留（见 needsReconcile 注释）
        if (!turnEnded) needsReconcile = true
        if (sessionId) writeLastSeq(sessionId, get().lastSeq)
      }
      // 刷新会话列表（自动标题/计数/排序变化）
      void useSessionsStore.getState().load().catch(() => {})
    }
  },

  stop: async () => {
    const { activeRunId } = get()
    if (!activeRunId) return
    try {
      await api(`/api/v1/runs/${activeRunId}/cancel`, { method: 'POST' })
      // 取消成功：后端推送 turn/aborted 后正常关流，无需本地断开
    } catch {
      // cancel 失败（run 已结束等）：兜底断开本地流，UI 由 finally 收敛
      activeController?.abort()
    }
  },

  answerAsk: async (text) => {
    const trimmed = text.trim()
    const { streaming, activeRunId, epoch } = get()
    if (!trimmed || !streaming || !activeRunId) return
    try {
      await api(`/api/v1/runs/${activeRunId}/answer`, {
        method: 'POST',
        body: { text: trimmed },
      })
      // 不本地回填：tool/result 事件很快到达并统一回填（实时与回放同口径）
    } catch (err) {
      if (get().epoch !== epoch) return
      set({
        error:
          err instanceof ApiError && err.status === 409
            ? '没有等待回答的问题（或本轮已结束）'
            : (err as Error).message || '提交回答失败',
      })
    }
  },

  clearError: () => set({ error: null }),

  steer: async (text) => {
    const trimmed = text.trim()
    const { streaming, activeRunId, epoch } = get()
    if (!trimmed || !streaming || !activeRunId) return
    try {
      // 插话不乐观上屏：SSE 会很快推回带 steering 标记的 user/message 事件
      await api(`/api/v1/runs/${activeRunId}/steer`, {
        method: 'POST',
        body: { text: trimmed },
      })
    } catch (err) {
      if (get().epoch !== epoch) return
      set({
        error:
          err instanceof ApiError && err.status === 409
            ? '本轮回复已结束，插话未送达，请直接发送新消息'
            : (err as Error).message || '插话失败',
      })
    }
  },

  reset: () => {
    activeController?.abort()
    activeController = null
    stopSyncPolling()
    set((s) => ({
      sessionId: null,
      messages: [],
      streamingText: '',
      thinkingText: '',
      turnStartTs: null,
      streaming: false,
      error: null,
      stats: emptyStats(),
      activeRunId: null,
      lastSeq: -1,
      epoch: s.epoch + 1,
    }))
  },
}))
