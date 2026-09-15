/**
 * 交互吸附槽（照抄 jiuwen `InteractionSlot/index.tsx`）：输入框正上方的交互吸附位。
 *
 * - 待作答的 ask_user 卡（问答 / 管线审批）在此以浮卡形态浮出，**不参与消息滚动**，
 *   紧贴输入框顶部——用户不必去工具调用折叠区里找问题；
 * - 作答后吸附槽立即让位：回显卡留在消息流的过程区（随「任务用时」chip 折叠），
 *   由 MessageList 一并渲染；
 * - 无待作答时渲染 null，不留空白。
 */
import { pickPendingAsk, useChatStore } from '@/stores/chat'
import AskUserCard from './AskUserCard'

/** 交互吸附槽组件（中间列：消息列表与输入框之间）。 */
export default function InteractionSlot() {
  const pending = useChatStore((s) => pickPendingAsk(s.messages, s.streaming))
  if (!pending) return null

  return (
    <div className="sa-rise shrink-0 px-4 pb-2 sm:px-6">
      <div className="mx-auto max-w-3xl">
        <AskUserCard
          callId={pending.callId}
          query={pending.query}
          options={pending.options}
          questions={pending.questions}
          approval={pending.approval}
          answer={pending.answer}
          floating
        />
      </div>
    </div>
  )
}
