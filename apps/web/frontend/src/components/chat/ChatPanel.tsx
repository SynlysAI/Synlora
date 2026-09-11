/**
 * 中间列聊天面板：启动引导（加载项目/会话/助手 → 选中首个未归档会话 /
 * 自动用第一个助手创建）+ 消息列表 + 输入区。左栏会话树 Task 5 接入后
 * 接管选中逻辑。
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

  // 启动引导：并行加载项目/会话/助手；无当前会话时选首个未归档或自动创建。
  // 项目必须在此加载并在建会话之前完成：GET /api/v1/projects 首次访问会触发
  // 后端旧布局迁移并补种「默认项目」，且新会话要带上当前项目 id。
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
      if (first) {
        st.setCurrent(first._id)
        return
      }
      const assistant = useAssistantsStore.getState().assistants[0]
      if (assistant) await st.create(assistant._id).catch(() => {})
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
  const assistantName = assistant?.name ?? 'SynlysAgent'
  const assistantAvatar = assistant?.avatar?.trim() || assistant?.name.slice(0, 1) || '科'
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
              SynlysAgent 轻松解决科研每个问题！
            </h1>
          </div>
          <Composer />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <MessageList assistantName={assistantName} assistantAvatar={assistantAvatar} />
      <Composer />
    </div>
  )
}
