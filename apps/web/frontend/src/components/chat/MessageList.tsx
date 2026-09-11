/**
 * 消息列表：滚动容器（near-bottom 自动跟随 + 回到底部）、条目渲染、
 * turn 头部行（头像 + 助手名，Jiuwen 模式：头像在回答上方）、空态欢迎页。
 *
 * 排版节奏：新一组对话（上一条是用户消息）加大上间距，并渲染 turn 头部；
 * 组内思考面板/工具卡片/正文保持紧凑缩进对齐。
 */
import { useEffect, useRef, useState } from 'react'
import type { ChatItem } from '@/stores/chat'
import { useChatStore } from '@/stores/chat'
import AssistantMessage from './AssistantMessage'
import Avatar from './Avatar'
import ReasoningPanel from './ReasoningPanel'
import ToolCallCard from './ToolCallCard'
import UserMessage from './UserMessage'

/** near-bottom 判定阈值（px）。 */
const NEAR_BOTTOM = 48

/** 空态示例问题（点击即发送）。 */
const EXAMPLES = [
  { icon: '🧮', text: '用 Python 算一下 2 + 3' },
  { icon: '📄', text: '帮我在工作区写一个 README.md' },
  { icon: '🗂️', text: '列出我的工作区里有哪些文件' },
]

interface EmptyStateProps {
  /** 当前助手名（无会话/未关联时回退产品名）。 */
  assistantName: string
  /** 助手头像字符。 */
  assistantAvatar: string
  /** 点击示例即发送。 */
  onSend: (text: string) => void
}

/** 空态欢迎页：头像 + 大标题 + 示例问题卡。 */
function EmptyState({ assistantName, assistantAvatar, onSend }: EmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 py-10">
      <div
        aria-hidden="true"
        className="flex h-14 w-14 items-center justify-center rounded-[var(--sa-radius-lg)] bg-[var(--sa-specific-bubble)] text-[26px] leading-none"
      >
        {assistantAvatar}
      </div>
      <div className="text-center">
        <div className="text-[22px] font-semibold tracking-tight text-[var(--sa-alias-label-primary)]">
          {assistantName}
        </div>
        <div className="pt-1.5 text-[14px] text-[var(--sa-alias-label-caption)]">
          有什么可以帮你的？
        </div>
      </div>
      <div className="flex w-full max-w-sm flex-col gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.text}
            type="button"
            onClick={() => onSend(ex.text)}
            className="flex items-center gap-2.5 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-4 py-3 text-left text-[13.5px] text-[var(--sa-alias-label-secondary)] shadow-sm transition-all duration-[var(--sa-duration-base)] hover:-translate-y-px hover:border-[var(--sa-alias-border-l3)] hover:text-[var(--sa-alias-label-primary)] hover:shadow-md"
          >
            <span aria-hidden="true" className="text-[15px]">
              {ex.icon}
            </span>
            {ex.text}
          </button>
        ))}
      </div>
    </div>
  )
}

interface TurnHeaderProps {
  /** 助手头像字符。 */
  avatar: string
  /** 助手名。 */
  name: string
  /** 本轮仍在进行（spinner）。 */
  active: boolean
}

/** turn 头部行：头像 + 名称（+ 进行中 spinner）。 */
function TurnHeader({ avatar, name, active }: TurnHeaderProps) {
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

interface ChatEntryProps {
  item: ChatItem
  /** 是否为新一组对话的开始（渲染 turn 头部 + 大上间距）。 */
  newGroup: boolean
  /** 助手头像字符。 */
  assistantAvatar: string
  /** 助手名。 */
  assistantName: string
  /** 该 turn 是否仍在流式进行（仅最后一个 turn 有效）。 */
  turnActive: boolean
}

/** 单条聊天条目渲染分发。 */
function ChatEntry({ item, newGroup, assistantAvatar, assistantName, turnActive }: ChatEntryProps) {
  const header = newGroup ? (
    <TurnHeader avatar={assistantAvatar} name={assistantName} active={turnActive} />
  ) : null
  const groupCls = newGroup ? 'mt-7' : 'mt-1.5'

  if (item.kind === 'user') {
    return (
      <div className="mt-7">
        <UserMessage text={item.text} />
      </div>
    )
  }
  if (item.kind === 'reasoning') {
    return (
      <section className={groupCls}>
        {header}
        <ReasoningPanel text={item.text} running={false} />
      </section>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <section className={groupCls}>
        {header}
        <AssistantMessage
          content={item.content}
          elapsedMs={item.elapsedMs}
          usage={item.usage}
        />
      </section>
    )
  }
  return (
    <section className={groupCls}>
      {header}
      <ToolCallCard call={item.call} result={item.result} />
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
  const send = useChatStore((s) => s.send)
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

  const empty = messages.length === 0 && !streamingText && !thinkingText
  /** 流式块是否开启 turn 头部（messages 已有内容时流式块不带头部，头部由 turn 首个块渲染）。 */
  const lastIsUser = messages.length > 0 && messages[messages.length - 1].kind === 'user'

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto px-4 sm:px-6">
        <div
          className={`mx-auto flex h-full max-w-3xl flex-col gap-4 py-6 ${
            empty ? 'justify-center' : 'justify-start'
          }`}
        >
          {empty ? (
            <EmptyState
              assistantName={assistantName}
              assistantAvatar={assistantAvatar}
              onSend={(t) => void send(t)}
            />
          ) : (
            <>
              {messages.map((item, i) => (
                <ChatEntry
                  key={i}
                  item={item}
                  newGroup={i > 0 && messages[i - 1].kind === 'user'}
                  assistantAvatar={assistantAvatar}
                  assistantName={assistantName}
                  turnActive={streaming && i === messages.length - 1}
                />
              ))}
              {/* 流式思考（turn 头部跟随 lastIsUser 判定） */}
              {thinkingText && (
                <section className="mt-1.5">
                  {lastIsUser && (
                    <TurnHeader avatar={assistantAvatar} name={assistantName} active />
                  )}
                  <ReasoningPanel text={thinkingText} running />
                </section>
              )}
              {/* 流式正文 */}
              {streamingText && (
                <section className="mt-1.5">
                  {lastIsUser && !thinkingText && (
                    <TurnHeader avatar={assistantAvatar} name={assistantName} active />
                  )}
                  <AssistantMessage content={streamingText} streaming />
                </section>
              )}
              {/* 无思考无正文时的等待头部（模型连接中） */}
              {streaming && !streamingText && !thinkingText && messages.length > 0 && lastIsUser && (
                <TurnHeader avatar={assistantAvatar} name={assistantName} active />
              )}
            </>
          )}
        </div>
      </div>

      {/* 用户上滚后出现；点击回到底部并恢复跟随 */}
      {!atBottom && !empty && (
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
