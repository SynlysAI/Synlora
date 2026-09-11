/**
 * 中间列聊天面板：启动引导（加载列表 → 选中首个未归档会话 / 自动用第一个
 * 助手创建）+ 消息列表 + 输入区。左栏会话树 Task 5 接入后接管选中逻辑。
 */
import { useEffect } from 'react'
import { useAssistantsStore } from '@/stores/assistants'
import { useChatStore } from '@/stores/chat'
import { useSessionsStore } from '@/stores/sessions'
import Composer from './Composer'
import MessageList from './MessageList'

/** 聊天面板组件（AppShell 中间列）。 */
export default function ChatPanel() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const assistants = useAssistantsStore((s) => s.assistants)

  // 启动引导：并行加载会话与助手；无当前会话时选首个未归档或自动创建
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const [ss, as] = [useSessionsStore.getState(), useAssistantsStore.getState()]
      await Promise.all([ss.load(), as.load()]).catch(() => {})
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

  return (
    <div className="flex h-full flex-col">
      <MessageList assistantName={assistantName} assistantAvatar={assistantAvatar} />
      <Composer />
    </div>
  )
}
