/**
 * 「+」菜单「专家」二级面板：搜索 + 助手列表，单选（当前项打勾）。
 *
 * 照抄 jiuwen `ChatPanel/InputArea.tsx` 3022-3102 行的 agent 面板结构
 * （头像 + 名称 + 描述 + 选中打勾），壳走本项目 PickerPanel。
 *
 * 选中语义分两态：
 * - **有当前会话**：PATCH `/api/v1/sessions/{sid}` 带 `assistant_id`，成功后
 *   `sessions.load()` 刷新列表，使右栏/顶栏与会话文档同步（后端每轮重新从会话
 *   文档取助手，故只影响后续轮次）；
 * - **无当前会话**（欢迎页）：写 `assistants.select(id)` 作为「新对话默认助手」
 *   （左栏头部卡同一份状态），新建会话时由 `pickSelectedAssistant` 取用。
 *   不提示「先建会话」——欢迎页正是最需要挑专家的场景，且两条路径都不需要
 *   会话存在，禁止选择只会造成死路。
 */
import { useEffect, useMemo, useState } from 'react'
import type { Assistant } from '@/types'
import { api } from '@/api/client'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
import PickerPanel from './PickerPanel'

interface ExpertPickerProps {
  /** 一级菜单展开方向（同步二级面板的生长方向）。 */
  direction: 'up' | 'down'
  /** 选定后收起整个「+」菜单（jiuwen 选中 agent 即 setAttachMenuOpen(false)）。 */
  onPicked: () => void
}

/** 助手头像：avatar 字段（emoji/字符）缺省时取名称首字符（同左栏 Sidebar）。 */
function Avatar({ assistant }: { assistant: Assistant }) {
  return (
    <span
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-specific-sidebar-nav-item-active-accent)] text-[11px] font-medium text-[var(--sa-alias-label-primary)]"
      aria-hidden="true"
    >
      {assistant.avatar?.trim() || assistant.name.slice(0, 1)}
    </span>
  )
}

/** 专家（助手）选择面板。 */
export default function ExpertPicker({ direction, onPicked }: ExpertPickerProps) {
  const assistants = useAssistantsStore((s) => s.assistants)
  const assistantsLoaded = useAssistantsStore((s) => s.loaded)
  const defaultAssistant = useAssistantsStore(pickSelectedAssistant)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = useSessionsStore((s) =>
    s.sessions.find((x) => x._id === s.currentId),
  )
  const [query, setQuery] = useState('')

  // 兜底加载（启动引导已完成时不重复请求）
  useEffect(() => {
    if (!assistantsLoaded) void useAssistantsStore.getState().load().catch(() => {})
  }, [assistantsLoaded])

  // 有会话时以会话文档绑定的助手为准，否则以「新对话默认助手」为准
  const selectedId = currentId
    ? (session?.assistant_id ?? null)
    : (defaultAssistant?._id ?? null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return assistants
    return assistants.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q),
    )
  }, [assistants, query])

  /** 选中助手：有会话走 PATCH + 列表刷新，无会话写新对话默认。 */
  const pick = async (assistant: Assistant) => {
    if (!currentId) {
      useAssistantsStore.getState().select(assistant._id)
      toast('info', `已选择专家「${assistant.name}」，将用于下一个新对话`)
      onPicked()
      return
    }
    try {
      await api(`/api/v1/sessions/${currentId}`, {
        method: 'PATCH',
        body: { assistant_id: assistant._id },
      })
      await useSessionsStore.getState().load()
      toast('success', `已切换到专家「${assistant.name}」`)
      onPicked()
    } catch (err) {
      toast('error', `切换专家失败：${(err as Error).message}`)
    }
  }

  return (
    <PickerPanel
      direction={direction}
      widthClass="w-[248px]"
      maxHeightClass="max-h-[min(360px,60vh)]"
      ariaLabel="选择专家"
      testId="composer-expert-picker"
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索专家"
    >
      {/* 无会话时说明写入目标（新对话默认助手），避免用户以为改了已有会话 */}
      {!currentId && (
        <div className="px-2 pb-1 pt-0.5 text-[11px] leading-4 text-[var(--sa-alias-label-caption)]">
          当前没有会话，选择将作为新对话的专家
        </div>
      )}

      {!assistantsLoaded ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          加载中…
        </div>
      ) : assistants.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          暂无专家
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          无匹配结果
        </div>
      ) : (
        filtered.map((a) => (
          <button
            key={a._id}
            role="menuitemradio"
            aria-checked={a._id === selectedId}
            type="button"
            title={a.description}
            onClick={() => void pick(a)}
            className={`flex w-full items-start gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 text-left transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] ${
              a._id === selectedId ? 'bg-[var(--sa-alias-interactive-bg-hover)]' : ''
            }`}
          >
            <Avatar assistant={a} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                {a.name}
                {a.builtin && (
                  <span className="ml-1.5 rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-markdown-tag)] px-1.5 py-px text-[10px] text-[var(--sa-alias-label-tertiary)]">
                    内置
                  </span>
                )}
              </span>
              <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
                {a.description}
              </span>
            </span>
            {a._id === selectedId && (
              <svg
                width="13"
                height="13"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="mt-1 shrink-0 text-[var(--sa-alias-state-business-primary)]"
                aria-hidden="true"
              >
                <path d="M3 8.5 6.5 12 13 4.5" />
              </svg>
            )}
          </button>
        ))
      )}
    </PickerPanel>
  )
}
