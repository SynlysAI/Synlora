/**
 * 中间列聊天面板：启动引导（加载项目/会话/助手）+ 消息列表 + 输入区。
 *
 * 会话选中态由 URL 驱动（AppShell 把 /chat/<id> 同步进 sessions store），
 * 本组件**不自动选中任何会话**——打开根地址落在 /chat/new 草稿态，首次发送
 * 时由 chat store 懒创建会话，否则每次打开页面/点新会话都会留一条空会话。
 */
import { useEffect } from 'react'
import { useAssistantsStore } from '@/stores/assistants'
import { useChatStore } from '@/stores/chat'
import { useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import Composer from './Composer'
import InteractionSlot from './InteractionSlot'
import MessageList from './MessageList'

/** 聊天面板组件（AppShell 中间列）。 */
export default function ChatPanel() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const assistants = useAssistantsStore((s) => s.assistants)

  // 启动引导：并行加载项目/会话/助手（项目必须加载：GET /api/v1/projects 首次访问
  // 会触发后端旧布局迁移并补种「默认项目」）。选中态只来自 URL，这里不干预。
  useEffect(() => {
    void (async () => {
      const [ss, as, ps] = [
        useSessionsStore.getState(),
        useAssistantsStore.getState(),
        useProjectsStore.getState(),
      ]
      await Promise.all([ss.load(), as.load(), ps.load()]).catch(() => {})
    })()
  }, [])

  // 会话切换 → 全量历史回放；会话清空 → 重置聊天态
  useEffect(() => {
    if (currentId) void useChatStore.getState().loadHistory(currentId)
    else useChatStore.getState().reset()
  }, [currentId])

  const session = sessions.find((s) => s._id === currentId)
  const assistant = assistants.find((a) => a._id === session?.assistant_id)
  // 未选专家时头部即平台身份：名用平台名（消息头部会渲染成字标而非文字）
  const assistantName = assistant?.name ?? 'Synlora'
  const platformDefault = !assistant
  const messages = useChatStore((s) => s.messages)
  const streamingText = useChatStore((s) => s.streamingText)
  const thinkingText = useChatStore((s) => s.thinkingText)
  const empty = messages.length === 0 && !streamingText && !thinkingText

  // 空态：标题 + 输入框整体在中栏垂直居中（Jiuwen 欢迎页）；有消息后输入框落回底部
  if (empty) {
    return (
      <div className="flex h-full flex-col overflow-y-auto">
        <div className="m-auto w-full pt-6 pb-24">
          <div className="mx-auto w-full max-w-3xl px-4 sm:px-6">
            <h1 className="mb-5 text-[32px] font-semibold leading-[48px] tracking-tight text-[var(--sa-alias-label-primary)]">
              {/* 品牌名单独包一层加重（照 jiuwen chat-welcome__heading-highlight 结构） */}
              <span className="font-bold">Synlora</span> 轻松解决每个科研问题！
            </h1>
          </div>
          <Composer empty={empty} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <MessageList assistantName={assistantName} platformDefault={platformDefault} />
      {/* 交互吸附槽：待作答的 ask_user 卡浮在输入框正上方，不占消息流 */}
      <InteractionSlot />
      <Composer empty={empty} />
    </div>
  )
}
