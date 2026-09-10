/**
 * 聊天核心 store：SSE 流式接收、事件流投影、历史回放与运行取消。
 *
 * 投影唯一入口 reduceEvent（纯函数）：SSE 实时流与 GET events 历史回放共用
 * 同一 reducer，保证刷新/重进会话后的渲染结果与实时流完全一致。
 *
 * 时序要点（见 harness agent.py）：
 * - llm/delta 先行累积为流式气泡；assistant/message 携带完整 content 定稿并
 *   清空累积（避免重复）；
 * - 工具调用前的解说文本只出现在 delta 中（tool/call.content 为冗余副本），
 *   故 tool/call 时将累积文本定稿为助手消息；
 * - 终止标志是 turn/end / turn/aborted 事件而非连接关闭；收尾时残留累积
 *   文本（中止/断连）定稿保留。
 */
import { create } from 'zustand'
import type { SessionEvent, ToolCallPayload, ToolResultPayload } from '@/types'
import { api, ApiError } from '@/api/client'
import { streamSse } from '@/api/sse'
import { useSessionsStore } from './sessions'

/** 聊天条目视图模型（由会话事件投影）。 */
export type ChatItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; content: string }
  | { kind: 'tool'; call: ToolCallPayload; result?: ToolResultPayload }

/** reducer 工作状态：条目列表 + 当前流式气泡的累积文本。 */
export interface ChatProjection {
  items: ChatItem[]
  streamingText: string
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
    case 'user/message': {
      // 用户消息（含 steering 插话）逐条展示
      const item: ChatItem = { kind: 'user', text: String(payload.text ?? '') }
      return { items: [...state.items, item], streamingText: state.streamingText }
    }
    case 'llm/delta':
      return { ...state, streamingText: state.streamingText + String(payload.text ?? '') }
    case 'assistant/message': {
      // 定稿：以事件 content 为准（与 delta 累积一致），清空累积避免重复
      const content =
        typeof payload.content === 'string' && payload.content
          ? payload.content
          : state.streamingText
      if (!content) return { ...state, streamingText: '' }
      return { items: [...state.items, { kind: 'assistant', content }], streamingText: '' }
    }
    case 'tool/call': {
      // 工具调用前若有解说文本（仅 delta 中出现），先定稿为助手消息
      let items = state.items
      if (state.streamingText) {
        items = [...items, { kind: 'assistant', content: state.streamingText }]
      }
      return {
        items: [...items, { kind: 'tool', call: payload as unknown as ToolCallPayload }],
        streamingText: '',
      }
    }
    case 'tool/result': {
      // 由后往前配对最近的同 id 未配对 tool 条目
      const result = payload as unknown as ToolResultPayload
      const idx = state.items.findLastIndex(
        (it) =>
          it.kind === 'tool' && it.call.tool_call.id === result.tool_call_id && !it.result,
      )
      if (idx === -1) return state
      const target = state.items[idx]
      if (target.kind !== 'tool') return state
      const items = [...state.items]
      items[idx] = { kind: 'tool', call: target.call, result }
      return { ...state, items }
    }
    case 'turn/end':
    case 'turn/aborted': {
      // 收尾：残留流式文本（中止/断连）定稿保留
      if (!state.streamingText) return state
      return {
        items: [...state.items, { kind: 'assistant', content: state.streamingText }],
        streamingText: '',
      }
    }
    default:
      // turn/start / error 不产生条目（run_id 与错误提示由 store 层处理）
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
  /** 是否正在接收一轮回复。 */
  streaming: boolean
  /** 已读最大事件 seq。 */
  lastSeq: number
  /** 用户可见错误提示（429 / 网络错误等）。 */
  error: string | null
  /** 进行中运行的 run_id（stop 取消用，来自 turn/start）。 */
  activeRunId: string | null
  /** 会话代际：每次绑定新会话自增，旧流事件按代丢弃。 */
  epoch: number
  /** 绑定会话并全量回放历史事件。 */
  loadHistory: (sessionId: string) => Promise<void>
  /** 发送消息：optimistic 用户气泡 + SSE 流接收。 */
  send: (text: string) => Promise<void>
  /** 停止当前运行（POST cancel；失败兜底断开本地流）。 */
  stop: () => Promise<void>
  /** 清除错误提示。 */
  clearError: () => void
  /** 解绑会话并重置全部状态。 */
  reset: () => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  messages: [],
  streamingText: '',
  streaming: false,
  lastSeq: -1,
  error: null,
  activeRunId: null,
  epoch: 0,

  loadHistory: async (sessionId) => {
    // 切会话：断开旧流（run 由后端执行完落盘，回读即可见）
    activeController?.abort()
    activeController = null
    const epoch = get().epoch + 1
    set({
      sessionId,
      messages: [],
      streamingText: '',
      streaming: false,
      activeRunId: null,
      error: null,
      lastSeq: readLastSeq(sessionId),
      epoch,
    })
    try {
      const events = await api<SessionEvent[]>(
        `/api/v1/sessions/${sessionId}/events?after_seq=-1`,
      )
      if (get().epoch !== epoch) return
      const final = events.reduce(reduceEvent, { items: [], streamingText: '' })
      const lastSeq = events.length ? events[events.length - 1].seq : -1
      writeLastSeq(sessionId, lastSeq)
      set({ messages: final.items, lastSeq })
    } catch (err) {
      if (get().epoch === epoch) set({ error: (err as Error).message })
    }
  },

  send: async (text) => {
    const trimmed = text.trim()
    const { sessionId, streaming, epoch } = get()
    if (!sessionId || !trimmed || streaming) return

    const controller = new AbortController()
    activeController = controller
    // optimistic 用户气泡；后端的 user/message 事件不再重复投影
    set((s) => ({
      messages: [...s.messages, { kind: 'user', text: trimmed }],
      streaming: true,
      error: null,
      streamingText: '',
    }))

    try {
      await streamSse(
        `/api/v1/sessions/${sessionId}/messages`,
        { text: trimmed },
        (m) => {
          if (get().epoch !== epoch) return
          let ev: SessionEvent
          try {
            ev = JSON.parse(m.data) as SessionEvent
          } catch {
            return
          }
          set((s) => {
            if (ev.type === 'user/message') return { lastSeq: Math.max(s.lastSeq, ev.seq) }
            const next = reduceEvent(
              { items: s.messages, streamingText: s.streamingText },
              ev,
            )
            return {
              messages: next.items,
              streamingText: next.streamingText,
              lastSeq: Math.max(s.lastSeq, ev.seq),
            }
          })
          if (get().epoch !== epoch) return
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
          messages: s.streamingText
            ? [...s.messages, { kind: 'assistant' as const, content: s.streamingText }]
            : s.messages,
          streamingText: '',
        }))
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

  clearError: () => set({ error: null }),

  reset: () => {
    activeController?.abort()
    activeController = null
    set((s) => ({
      sessionId: null,
      messages: [],
      streamingText: '',
      streaming: false,
      error: null,
      activeRunId: null,
      lastSeq: -1,
      epoch: s.epoch + 1,
    }))
  },
}))
