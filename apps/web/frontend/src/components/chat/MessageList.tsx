/**
 * 消息列表：滚动容器（near-bottom 自动跟随 + 回到底部）、turn 分组渲染。
 * （空态欢迎页见 WelcomeScreen，由 ChatPanel 在无消息时渲染）
 *
 * Jiuwen 展示模式：
 * - turn 头部行（头像 + 助手名）在整组回答上方
 * - 本轮的思考/工具行收进"任务用时 X.XXs"折叠 chip（completed-work-chip），
 *   完成后默认收起，点击展开才看到思考与工具调用，页面精简
 * - 流式进行中：思考/工具行直接实时展示（各组件自带折叠与扫光）
 * - 回答正文（assistant）在 chip 之后文档流展示；间距：组内紧（gap-1）、
 *   turn 之间松（mt-6）
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChatItem } from '@/stores/chat'
import { useChatStore } from '@/stores/chat'
import AssistantMessage, { formatElapsed } from './AssistantMessage'
import Avatar from './Avatar'
import ReasoningPanel from './ReasoningPanel'
import ToolCallCard from './ToolCallCard'
import UserMessage from './UserMessage'

/** near-bottom 判定阈值（px）。 */
const NEAR_BOTTOM = 48

/** turn 分组视图结构：user 起始，work = 思考+工具（收进 chip），answers = 正文。 */
interface Turn {
  user: Extract<ChatItem, { kind: 'user' }> | null
  work: Extract<ChatItem, { kind: 'reasoning' } | { kind: 'tool' }>[]
  answers: Extract<ChatItem, { kind: 'assistant' }>[]
}

/** 按 user 边界把平铺 items 切成 turn 组。 */
function groupTurns(items: ChatItem[]): Turn[] {
  const turns: Turn[] = []
  for (const item of items) {
    const last = turns[turns.length - 1]
    if (item.kind === 'user') {
      turns.push({ user: item, work: [], answers: [] })
    } else if (!last) {
      // 没有前置 user 的 AI 块（异常防御）：独立成组
      turns.push({ user: null, work: [], answers: [] })
      pushTo(turns[turns.length - 1], item)
    } else {
      pushTo(last, item)
    }
  }
  return turns
}

/** 把 AI 块放入对应分区。 */
function pushTo(turn: Turn, item: ChatItem) {
  if (item.kind === 'assistant') turn.answers.push(item)
  else if (item.kind !== 'user') turn.work.push(item)
}

/** turn 头部行：头像 + 名称（+ 进行中 spinner）。 */
function TurnHeader({ avatar, name, active }: { avatar: string; name: string; active: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <Avatar char={avatar} />
      <span className="text-[13px] font-semibold text-[var(--sa-alias-label-primary)]">{name}</span>
      {active && (
        <svg
          className="h-3 w-3 animate-spin text-[var(--sa-alias-label-tertiary)]"
          viewBox="0 0 16 16"
          fill="none"
          aria-label="生成中"
        >
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" opacity="0.25" />
          <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )}
    </div>
  )
}

/** 时钟图标（Jiuwen WaitingStatusIcon 同款语义）。 */
function ClockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="6.5" />
      <path d="M10 6.5V10l2.4 1.6" />
    </svg>
  )
}

interface WorkChipProps {
  /** 本轮任务用时。 */
  elapsedMs: number
  /** 是否含失败工具（错误色调）。 */
  failed: boolean
  /** 展开状态。 */
  open: boolean
  /** 点击切换。 */
  onToggle: () => void
}

/** "任务用时 X.XXs" 折叠 chip（照抄 Jiuwen completed-work-chip），包住本轮思考与工具。 */
function WorkChip({ elapsedMs, failed, open, onToggle }: WorkChipProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={`group/chip inline-flex max-w-full items-center gap-1.5 border-0 bg-transparent py-0.5 pr-1 text-left ${
        failed ? 'text-[var(--sa-alias-state-error-primary)]' : 'text-[var(--sa-alias-label-tertiary)]'
      }`}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
        <ClockIcon />
      </span>
      <span className="min-w-0 text-[12px] font-medium tabular-nums leading-[1.3]">
        任务用时 {formatElapsed(elapsedMs)}
      </span>
      <span
        aria-hidden="true"
        className={`flex h-3 w-3 shrink-0 items-center justify-center transition-[transform,opacity] duration-200 ${
          open ? 'rotate-90 opacity-100' : 'opacity-0 group-hover/chip:opacity-100'
        }`}
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="m8 6 4 4-4 4" />
        </svg>
      </span>
    </button>
  )
}

/** work 区条目（思考面板/工具行）。 */
function WorkItem({ item }: { item: ChatItem }) {
  if (item.kind === 'reasoning') {
    return <ReasoningPanel text={item.text} running={false} />
  }
  if (item.kind === 'tool') {
    return <ToolCallCard call={item.call} result={item.result} />
  }
  return null
}

interface TurnBlockProps {
  turn: Turn
  /** 本 turn 是否仍在流式进行（最后一组）。 */
  active: boolean
  assistantAvatar: string
  assistantName: string
}

/** 单个 turn 渲染：头部 → [chip 折叠的 work 区 | 流式 work 区] → 正文。 */
function TurnBlock({ turn, active, assistantAvatar, assistantName }: TurnBlockProps) {
  const [workOpen, setWorkOpen] = useState(false)
  const lastAnswer = turn.answers[turn.answers.length - 1]
  const elapsedMs = lastAnswer?.elapsedMs
  const failed = turn.work.some((w) => w.kind === 'tool' && w.result && !w.result.ok)
  const done = !active && elapsedMs != null
  const hasHeader = turn.user != null

  return (
    <section className="mt-6 first:mt-0">
      {turn.user && <UserMessage text={turn.user.text} />}
      <div className="flex flex-col gap-1.5">
        {hasHeader && (
          <TurnHeader avatar={assistantAvatar} name={assistantName} active={active} />
        )}
        {/* 思考/工具区：完成后收进 chip，流式中直接展示 */}
        {turn.work.length > 0 && (
          <>
            {done ? (
              <>
                <WorkChip
                  elapsedMs={elapsedMs}
                  failed={failed}
                  open={workOpen}
                  onToggle={() => setWorkOpen((v) => !v)}
                />
                {workOpen && (
                  <div className="flex flex-col gap-1 pl-1">
                    {turn.work.map((w, i) => (
                      <WorkItem key={i} item={w} />
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col gap-1 pl-1">
                {turn.work.map((w, i) => (
                  <WorkItem key={i} item={w} />
                ))}
              </div>
            )}
          </>
        )}
        {/* 回答正文（最后一个带完成元信息） */}
        {turn.answers.map((a, i) => (
          <AssistantMessage
            key={i}
            content={a.content}
            usage={i === turn.answers.length - 1 ? a.usage : undefined}
            finishedTs={i === turn.answers.length - 1 ? a.finishedTs : undefined}
          />
        ))}
      </div>
    </section>
  )
}

interface MessageListProps {
  /** 当前助手名（空态/turn 头部展示）。 */
  assistantName: string
  /** 当前助手头像字符。 */
  assistantAvatar: string
}

/** 消息列表组件（中间列上部的滚动区）。 */
export default function MessageList({ assistantName, assistantAvatar }: MessageListProps) {
  const messages = useChatStore((s) => s.messages)
  const streamingText = useChatStore((s) => s.streamingText)
  const thinkingText = useChatStore((s) => s.thinkingText)
  const streaming = useChatStore((s) => s.streaming)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)

  // near-bottom 时新内容自动跟随滚动到底
  useEffect(() => {
    if (!atBottom) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streamingText, thinkingText, atBottom])

  /** 滚动监听：更新 near-bottom 状态。 */
  const handleScroll = () => {
    const el = scrollRef.current
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM)
  }

  /** 回到底部（并恢复跟随）。 */
  const scrollToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight })
    setAtBottom(true)
  }

  const turns = useMemo(() => groupTurns(messages), [messages])

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto px-4 sm:px-6">
        <div className="mx-auto flex min-h-full max-w-3xl flex-col pt-6 pb-10">
          {turns.map((turn, i) => (
            <TurnBlock
              key={i}
              turn={turn}
              active={streaming && i === turns.length - 1}
              assistantAvatar={assistantAvatar}
              assistantName={assistantName}
            />
          ))}
          {/* 流式区（头部已由最后一个 TurnBlock 渲染，此处只接内容） */}
          {streaming && (thinkingText || streamingText) && (
            <section className="mt-1.5">
              {thinkingText && (
                <div className="pl-1">
                  <ReasoningPanel text={thinkingText} running />
                </div>
              )}
              {streamingText && <AssistantMessage content={streamingText} streaming />}
            </section>
          )}
        </div>
      </div>

      {/* 用户上滚后出现；点击回到底部并恢复跟随 */}
      {!atBottom && (
        <button
          type="button"
          aria-label="回到底部"
          onClick={scrollToBottom}
          className="absolute bottom-3 left-1/2 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-[var(--sa-radius-full)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-button-floating-fill)] text-[var(--sa-alias-label-secondary)] shadow-md transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-floating-hover)]"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M8 3v9.5M3.8 8.7 8 12.9l4.2-4.2" />
          </svg>
        </button>
      )}
    </div>
  )
}
