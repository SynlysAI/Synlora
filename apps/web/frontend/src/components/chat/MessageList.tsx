/**
 * 消息列表：滚动容器（near-bottom 自动跟随、上滚停止跟随 + 回到底部按钮）、
 * 条目渲染（用户/助手/工具卡片/流式气泡）、空态欢迎页（示例问题点击即发）。
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
  '用 Python 算一下 2 + 3',
  '帮我在工作区写一个 README.md',
  '列出我的工作区里有哪些文件',
]

interface EmptyStateProps {
  /** 当前助手名（无会话/未关联时回退产品名）。 */
  assistantName: string
  /** 点击示例即发送。 */
  onSend: (text: string) => void
}

/** 空态欢迎页：Logo + 助手名 + 示例问题。 */
function EmptyState({ assistantName, onSend }: EmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-5 py-10">
      <svg width="44" height="44" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="1" y="1" width="18" height="18" rx="5" fill="var(--sa-alias-button-primary-fill)" />
        <path
          d="M12.9 6.3a4 4 0 1 0 1.3 5.2"
          stroke="var(--sa-alias-label-primary-foreground)"
          strokeWidth="1.8"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
      <div className="text-center">
        <div className="text-[17px] font-medium text-[var(--sa-alias-label-primary)]">
          {assistantName}
        </div>
        <div className="pt-1 text-[13px] text-[var(--sa-alias-label-caption)]">
          有什么可以帮你的？
        </div>
      </div>
      <div className="flex w-full max-w-sm flex-col gap-2">
        {EXAMPLES.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onSend(text)}
            className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] px-3.5 py-2.5 text-left text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 单条聊天条目渲染分发。 */
function ChatEntry({ item }: { item: ChatItem }) {
  if (item.kind === 'user') return <UserMessage text={item.text} />
  if (item.kind === 'assistant') return <AssistantMessage content={item.content} />
  return <ToolCallCard call={item.call} result={item.result} />
}

interface MessageListProps {
  /** 当前助手名（空态展示）。 */
  assistantName: string
}

/** 消息列表组件（中间列上部的滚动区）。 */
export default function MessageList({ assistantName }: MessageListProps) {
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
            <EmptyState assistantName={assistantName} onSend={(t) => void send(t)} />
          ) : (
            <>
              {messages.map((item, i) => (
                <ChatEntry key={i} item={item} />
              ))}
              {streamingText && <AssistantMessage content={streamingText} streaming />}
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
