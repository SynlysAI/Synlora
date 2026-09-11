/**
 * 左栏："+ 新对话" 主按钮 → 助手选择区（头部卡 + 展开列表切换新对话默认
 * 助手，不影响已有会话）→ 会话搜索框 → 会话列表（SessionList）。
 */
import { useState } from 'react'
import type { Assistant } from '@/types'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
import SessionList from './SessionList'

/** 助手头像：avatar 字段（emoji/字符）缺省时取名称首字符。 */
function AssistantAvatar({ assistant, active }: { assistant: Assistant; active?: boolean }) {
  const label = assistant.avatar?.trim() || assistant.name.slice(0, 1)
  return (
    <span
      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] text-[13px] font-medium ${
        active
          ? 'bg-[var(--sa-alias-button-primary-fill)] text-[var(--sa-alias-label-primary-foreground)]'
          : 'bg-[var(--sa-specific-sidebar-nav-item-active-accent)] text-[var(--sa-alias-label-primary)]'
      }`}
      aria-hidden="true"
    >
      {label}
    </span>
  )
}

/** 助手选择区：当前默认助手头部卡 + 点击展开的切换列表（绝对定位浮层）。 */
function AssistantPicker() {
  const assistants = useAssistantsStore((s) => s.assistants)
  const selectedId = useAssistantsStore((s) => s.selectedId)
  const select = useAssistantsStore((s) => s.select)
  const [open, setOpen] = useState(false)
  // 显式选择优先，回退第一个（与 pickSelectedAssistant 同规则，订阅态直接推导）
  const selected =
    assistants.find((a) => a._id === selectedId) ?? assistants[0] ?? null

  return (
    <div className="relative">
      {/* 头部卡：当前新对话默认助手 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] px-2.5 py-2 text-left transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        {selected && <AssistantAvatar assistant={selected} />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-[var(--sa-alias-label-primary)]">
            {selected?.name ?? '加载助手…'}
          </span>
          <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
            {selected?.description || '用于新对话'}
          </span>
        </span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`shrink-0 text-[var(--sa-alias-label-caption)] transition-transform duration-[var(--sa-duration-base)] ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        >
          <path d="m3.5 6 4.5 4.5L12.5 6" />
        </svg>
      </button>

      {/* 展开列表：切换新对话默认助手（不影响已有会话） */}
      {open && (
        <div className="absolute inset-x-0 top-full z-30 mt-1 max-h-64 overflow-y-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg">
          {assistants.map((a) => (
            <button
              key={a._id}
              type="button"
              onClick={() => {
                select(a._id)
                setOpen(false)
              }}
              className={`flex w-full items-center gap-2.5 rounded-[var(--sa-radius-sm)] px-2 py-1.5 text-left transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] ${
                a._id === selectedId ? 'bg-[var(--sa-specific-sidebar-nav-item-active)]' : ''
              }`}
            >
              <AssistantAvatar assistant={a} active={a._id === selectedId} />
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
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

interface SidebarProps {
  /** 抽屉模式下点击导航项后关闭抽屉。 */
  onNavigate?: () => void
}

/** 左栏组件（AppShell 左侧列 / compact 抽屉）。 */
export default function Sidebar({ onNavigate }: SidebarProps) {
  const create = useSessionsStore((s) => s.create)
  const [query, setQuery] = useState('')

  /** 新建会话：用当前默认助手创建并置为当前（chat 由 currentId 效应重置）。 */
  const handleNew = async () => {
    const assistant = pickSelectedAssistant(useAssistantsStore.getState())
    if (!assistant) return
    try {
      await create(assistant._id)
      onNavigate?.()
    } catch (err) {
      toast('error', `新建会话失败：${(err as Error).message}`)
    }
  }

  return (
    <div className="flex h-full flex-col gap-2 p-2.5">
      {/* 新对话主按钮（DSH 式：描边低调按钮） */}
      <button
        type="button"
        onClick={() => void handleNew()}
        className="flex shrink-0 items-center justify-center gap-1.5 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-[7px] text-[13px] font-medium text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-base)] hover:border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="M8 3v10M3 8h10" />
        </svg>
        新会话
      </button>

      {/* 助手选择区 */}
      <AssistantPicker />

      {/* 会话搜索 */}
      <div className="relative shrink-0">
        <svg
          width="13"
          height="13"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--sa-alias-label-caption)]"
          aria-hidden="true"
        >
          <circle cx="7" cy="7" r="4.5" />
          <path d="m10.5 10.5 3 3" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索会话"
          aria-label="搜索会话"
          className="w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] py-1.5 pl-7.5 pr-2 text-[13px] text-[var(--sa-alias-label-primary)] outline-none transition-colors placeholder:text-[var(--sa-alias-label-caption)] focus:border-[var(--sa-alias-button-ghost-active-border)]"
        />
      </div>

      {/* 会话列表 */}
      <SessionList query={query} onNavigate={onNavigate} />
    </div>
  )
}
