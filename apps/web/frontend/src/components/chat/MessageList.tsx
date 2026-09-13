/**
 * 消息列表：滚动容器（near-bottom 自动跟随 + 回到底部）、turn 分组渲染。
 * （空态欢迎页见 WelcomeScreen，由 ChatPanel 在无消息时渲染）
 *
 * Jiuwen 展示模式：
 * - turn 头部行（头像 + 助手名）在整组回答上方
 * - 本轮的思考/工具行/中间解说收进"任务用时 X.XXs"折叠 chip（completed-work-chip），
 *   完成后默认收起，点击展开才看到过程，页面精简
 * - 流式进行中：过程直接实时展示（各组件自带折叠与扫光）
 * - **只有本轮的最终回答**在 chip 之后文档流展示，并带尾部时间/复制/用量；
 *   间距：组内紧（gap-1）、turn 之间松（mt-6）
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChatItem } from '@/stores/chat'
import { useChatStore } from '@/stores/chat'
import AskUserCard from './AskUserCard'
import AssistantMessage, { formatElapsed } from './AssistantMessage'
import FileSendCard from './FileSendCard'
import BrandMark from '@/components/layout/BrandMark'
import BrandWordmark from '@/components/layout/BrandWordmark'
import ReasoningPanel from './ReasoningPanel'
import ToolCallCard from './ToolCallCard'
import UserMessage from './UserMessage'

/** near-bottom 判定阈值（px）。 */
const NEAR_BOTTOM = 48

/** 分组中间态：一轮的全部 AI 条目，保持事件原始顺序。 */
interface RawTurn {
  user: Extract<ChatItem, { kind: 'user' }> | null
  items: ChatItem[]
}

/**
 * turn 分组视图结构：
 * - `process`：本轮的思考/工具/**中间解说正文**，保持事件原始顺序，收进「任务用时」chip；
 * - `answer`：本轮**最后一条** assistant 正文（最终回答），文档流展示并带尾部操作行。
 *
 * 中间解说与最终回答的区分照抄参考实现：jiuwen `buildTurnTimeline` 反向扫描把
 * 一轮里靠前的 assistant 消息标 `hideMeta`（折进「已完成」折叠条、不出复制按钮），
 * DSH 把它算作 turn-process（`Reply-bearing durable Assistant messages before the
 * final answer`），只有 turn-tail 带复制/用时/用量。
 *
 * process 保持原顺序是必须的：工具调用前的解说若被单独归到"正文"分区，会整段
 * 排到所有工具调用之后，与真实发生顺序相反。
 */
interface Turn {
  user: Extract<ChatItem, { kind: 'user' }> | null
  process: ChatItem[]
  answer: Extract<ChatItem, { kind: 'assistant' }> | null
}

/** 按 user 边界把平铺 items 切成 turn 组，并摘出每轮的最终回答。 */
function groupTurns(items: ChatItem[]): Turn[] {
  const raw: RawTurn[] = []
  for (const item of items) {
    const last = raw[raw.length - 1]
    if (item.kind === 'user') raw.push({ user: item, items: [] })
    else if (!last) raw.push({ user: null, items: [item] }) // 无前置 user 的 AI 块（异常防御）
    else last.items.push(item)
  }
  return raw.map(({ user, items: all }) => {
    const answerIdx = all.findLastIndex((it) => it.kind === 'assistant')
    if (answerIdx === -1) return { user, process: all, answer: null }
    const answer = all[answerIdx] as Extract<ChatItem, { kind: 'assistant' }>
    return { user, process: all.filter((_, i) => i !== answerIdx), answer }
  })
}

/**
 * turn 头部行：品牌标记 + 名称（+ 进行中 spinner）。
 *
 * 标记统一用平台的品牌方块（与左栏品牌区同一个图形，不再按助手显示 emoji 头像）；
 * 名称在「未选专家」时是平台名，直接用几何字标渲染，与侧栏品牌区完全一致；
 * 选了专家则显示该专家名（可变文本，字标渲染不了）。
 */
function TurnHeader({ name, platformDefault, active }: {
  name: string
  platformDefault: boolean
  active: boolean
}) {
  return (
    <div className="flex items-center gap-2">
      <BrandMark size={24} />
      {platformDefault ? (
        <BrandWordmark height={19} className="text-[var(--sa-alias-label-primary)]" />
      ) : (
        <span className="text-[14px] font-semibold text-[var(--sa-alias-label-primary)]">{name}</span>
      )}
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

/** process 区条目（思考面板/工具行/中间解说/问答回路卡片）。 */
function WorkItem({ item }: { item: ChatItem }) {
  if (item.kind === 'reasoning') {
    return <ReasoningPanel text={item.text} running={false} />
  }
  if (item.kind === 'tool') {
    return <ToolCallCard call={item.call} result={item.result} />
  }
  if (item.kind === 'ask_user') {
    return <AskUserCard callId={item.callId} query={item.query} options={item.options} approval={item.approval} answer={item.answer} />
  }
  if (item.kind === 'file_send') {
    return <FileSendCard fileId={item.fileId} filename={item.filename} size={item.size} note={item.note} />
  }
  // 中间解说：正文照常渲染，但不带尾部时间/复制/用量（那属于最终回答）
  if (item.kind === 'assistant') {
    return <AssistantMessage content={item.content} hideMeta />
  }
  return null
}

interface TurnBlockProps {
  turn: Turn
  /** 本 turn 是否仍在流式进行（最后一组）。 */
  active: boolean
  assistantName: string
  /** 未选专家（头部渲染平台字标而非专家名）。 */
  platformDefault: boolean
}

/** 单个 turn 渲染：头部 → [chip 折叠的 process 区 | 流式 process 区] → 最终回答。 */
function TurnBlock({ turn, active, assistantName, platformDefault }: TurnBlockProps) {
  const [workOpen, setWorkOpen] = useState(false)
  const answer = turn.answer
  const elapsedMs = answer?.elapsedMs
  const failed = turn.process.some((w) => w.kind === 'tool' && w.result && !w.result.ok)
  const done = !active && elapsedMs != null
  const hasHeader = turn.user != null

  return (
    <section className="mt-6 first:mt-0">
      {turn.user && <UserMessage text={turn.user.text} attachments={turn.user.attachments} />}
      <div className="flex flex-col gap-1.5">
        {hasHeader && (
          <TurnHeader name={assistantName} platformDefault={platformDefault} active={active} />
        )}
        {/* 过程区（思考/工具/中间解说）：完成后收进 chip，流式中直接展示 */}
        {turn.process.length > 0 && (
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
                    {turn.process.map((w, i) => (
                      <WorkItem key={i} item={w} />
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col gap-1 pl-1">
                {turn.process.map((w, i) => (
                  <WorkItem key={i} item={w} />
                ))}
              </div>
            )}
          </>
        )}
        {/* 最终回答（本轮唯一带尾部时间/复制/用量的正文）。流式进行中它可能
            只是刚定稿的中间解说（后面还有工具/回答），meta 等轮次落定再出现 */}
        {answer && (
          <AssistantMessage
            content={answer.content}
            usage={answer.usage}
            finishedTs={answer.finishedTs}
            hideMeta={active}
          />
        )}
      </div>
    </section>
  )
}

interface MessageListProps {
  /** 当前助手名（turn 头部展示；未选专家时即平台名）。 */
  assistantName: string
  /** 未选专家（turn 头部渲染平台字标而非专家名）。 */
  platformDefault: boolean
}

/** 消息列表组件（中间列上部的滚动区）。 */
export default function MessageList({ assistantName, platformDefault }: MessageListProps) {
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
              assistantName={assistantName}
              platformDefault={platformDefault}
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
