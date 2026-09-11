/**
 * 消息列表：滚动容器（near-bottom 自动跟随、上滚停止跟随 + 回到底部按钮）、
 * 条目渲染（用户/助手/工具卡片/流式）、对话组间距节奏、空态欢迎页。
 *
 * 排版节奏：上一条是用户消息时（即新一组对话开始）加大上间距，
 * 组内的工具卡片与助手回复保持紧凑。
 */
import { useEffect, useRef, useState } from 'react'
import type { ChatItem } from '@/stores/chat'
import { useChatStore } from '@/stores/chat'
import AssistantMessage from './AssistantMessage'
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

interface ChatEntryProps {
  item: ChatItem
  /** 是否为新一组对话的开始（上一条是用户消息时加大上间距）。 */
  newGroup: boolean
  /** 助手头像字符。 */
  assistantAvatar: string
}

/** 单条聊天条目渲染分发。 */
function ChatEntry({ item, newGroup, assistantAvatar }: ChatEntryProps) {
  const groupSpacing = newGroup ? 'mt-7' : ''
  if (item.kind === 'user') return <div className={groupSpacing}><UserMessage text={item.text} /></div>
  if (item.kind === 'assistant')
    return <div className={groupSpacing}><AssistantMessage content={item.content} avatar={assistantAvatar} /></div>
  return (
    <div className={groupSpacing}>
      <ToolCallCard call={item.call} result={item.result} />
    </div>
  )
}

interface MessageListProps {
  /** 当前助手名（空态展示）。 */
  assistantName: string
  /** 当前助手头像字符。 */
  assistantAvatar: string
}

/** 消息列表组件（中间列上部的滚动区）。 */
export default function MessageList({ assistantName, assistantAvatar }: MessageListProps) {
  const messages = useChatStore((s) => s.messages)
  const streamingText = useChatStore((s) => s.streamingText)
  const send = useChatStore((s) => s.send)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)

  // near-bottom 时新内容自动跟随滚动到底
  useEffect(() => {
    if (!atBottom) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streamingText, atBottom])

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

  const empty = messages.length === 0 && !streamingText

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
                />
              ))}
              {streamingText && (
                <AssistantMessage content={streamingText} streaming avatar={assistantAvatar} />
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
