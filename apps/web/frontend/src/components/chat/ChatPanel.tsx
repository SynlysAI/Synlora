/**
 * 中间列聊天面板：启动引导（加载项目/会话/助手 → 选中首个未归档会话）+
 * 消息列表 + 输入区。
 *
 * 启动引导**不预建会话**：没有历史会话时保持草稿态（`currentId` 为 null），
 * 首次发送时由 chat store 懒创建——否则每次打开页面/点新会话都会留一条空会话。
 */
import { useEffect } from 'react'
import { useAssistantsStore } from '@/stores/assistants'
import { useChatStore } from '@/stores/chat'
import { useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import Composer from './Composer'
import MessageList from './MessageList'

/** 聊天面板组件（AppShell 中间列）。 */
export default function ChatPanel() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const assistants = useAssistantsStore((s) => s.assistants)

  // 启动引导：并行加载项目/会话/助手（项目必须加载：GET /api/v1/projects 首次访问
  // 会触发后端旧布局迁移并补种「默认项目」）。有历史会话就选中首个未归档；
  // 一条都没有则保持草稿态，不预建会话（见文件头）。
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const [ss, as, ps] = [
        useSessionsStore.getState(),
        useAssistantsStore.getState(),
        useProjectsStore.getState(),
      ]
      await Promise.all([ss.load(), as.load(), ps.load()]).catch(() => {})
      if (cancelled) return
      const st = useSessionsStore.getState()
      if (st.currentId) return
      const first = st.sessions.find((s) => !s.archived)
      if (first) st.setCurrent(first._id)
    })()
    return () => {
      cancelled = true
    }
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
              Synlora 轻松解决科研每个问题！
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
      <Composer empty={empty} />
    </div>
  )
}
