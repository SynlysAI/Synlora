/**
 * 中间列聊天面板：启动引导（加载项目/会话/助手 → 选中首个未归档会话 /
 * 按「新对话默认专家」建会话，未选专家则不带 assistant_id）+ 消息列表 +
 * 输入区。
 */
import { useEffect } from 'react'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
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
  // 后端旧布局迁移并补种「默认项目」，且新会话要带上当前选中的工作区 id
  // （未选则不传，由后端回落默认工作区）。
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
      // 专家可选：无显式选择时按「不使用专家」建会话（平台默认提示词）
      const assistant = pickSelectedAssistant(useAssistantsStore.getState())
      // 工作区：取此刻已加载完的选择（未选 = undefined → 后端回落默认工作区）
      const projectId = useProjectsStore.getState().currentId ?? undefined
      await st.create(assistant?._id ?? null, { projectId }).catch(() => {})
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
  const assistantName = assistant?.name ?? 'Synlora'
  // 头像取助手的 emoji；没配则回退到「当前显示名的首字符」——未选专家时即平台名的 S
  const assistantAvatar = assistant?.avatar?.trim() || assistantName.slice(0, 1) || 'S'
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
      <MessageList assistantName={assistantName} assistantAvatar={assistantAvatar} />
      <Composer empty={empty} />
    </div>
  )
}
