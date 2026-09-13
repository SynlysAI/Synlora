/**
 * 输入框左下的「+」按钮 + 当前专家 chip + 附件菜单（上传文件 / 专家 / 技能）。
 *
 * 结构与定位照抄 jiuwen `ChatPanel/InputArea.tsx` 2936-3341 行：
 * - 触发器包在相对定位锚点里，点击时取 `getBoundingClientRect()`，经
 *   `createPortal` 到 `document.body` + `position: fixed` 弹出；视口下方空间
 *   不足 200px 时向上弹（bottom 反算），否则向下；
 * - 菜单项三段：上传文件（直接动作）、专家 ›（二级面板）、技能 ›（二级面板）；
 *   二级面板点击展开（不是 hover），三个面板互斥，面板绝对定位在触发项包装层内
 *   `left: calc(100% + 11px)`（见 PickerPanel）；
 * - `pointerdown` 挂 document：点在触发器/弹层之外即关闭，卸载时移除。
 *
 * 本项目新增：左侧「当前专家 chip」（jiuwen 的 chat-agent-tag 形态），点击即
 * 打开菜单并直接展开专家面板，作为输入框内的专家切换入口（左栏助手卡不动）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Assistant } from '@/types'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { useChatStore } from '@/stores/chat'
import { useSessionsStore } from '@/stores/sessions'
import ExpertPicker from './ExpertPicker'
import SkillPicker from './SkillPicker'

/** 菜单项样式（13px 行，与 ModelPicker/WorkspacePicker 菜单项一致）。 */
const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] ' +
  'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] ' +
  'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-[var(--sa-alias-label-secondary)]'

/** 二级面板展开中的触发项（选中态与 hover 同色，照 jiuwen --panel-open）。 */
const ITEM_OPEN_CLASS = 'bg-[var(--sa-alias-interactive-bg-hover)] text-[var(--sa-alias-label-primary)]'

/** 菜单展开方向：下方空间不足 200px 时向上弹（jiuwen InputArea 2945 行同判据）。 */
const MIN_SPACE_BELOW = 200

interface AttachMenuProps {
  /** 本轮已选技能名（受控，来自 Composer）。 */
  selectedSkills: string[]
  /** 勾选/取消勾选一个技能。 */
  onToggleSkill: (name: string) => void
  /** 选中待上传文件（Composer 负责上传到工作区并出附件 chips）。 */
  onFiles: (files: File[]) => void
}

/** 输入框左下「+」菜单（含当前专家 chip）。 */
export default function AttachMenu({ selectedSkills, onToggleSkill, onFiles }: AttachMenuProps) {
  const streaming = useChatStore((s) => s.streaming)
  const assistants = useAssistantsStore((s) => s.assistants)
  const defaultAssistant = useAssistantsStore(pickSelectedAssistant)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = useSessionsStore((s) =>
    s.sessions.find((x) => x._id === s.currentId),
  )

  const [open, setOpen] = useState(false)
  /** 当前展开的二级面板（三选一互斥）。 */
  const [panel, setPanel] = useState<'none' | 'expert' | 'skill'>('none')
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const [direction, setDirection] = useState<'up' | 'down'>('down')

  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  /** 当前生效的专家：有会话取会话绑定，无会话取「新对话默认助手」。 */
  const assistant = useMemo<Assistant | null>(() => {
    if (currentId) {
      return assistants.find((a) => a._id === session?.assistant_id) ?? null
    }
    return defaultAssistant
  }, [currentId, session?.assistant_id, assistants, defaultAssistant])

  // 点击组件/弹层外部关闭（pointerdown 覆盖鼠标与触屏）
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  /** 以锚点矩形打开菜单并指定初始二级面板。 */
  const openMenu = (nextPanel: 'none' | 'expert' | 'skill') => {
    const el = rootRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    setAnchor(rect)
    setDirection(window.innerHeight - rect.bottom >= MIN_SPACE_BELOW ? 'down' : 'up')
    setPanel(nextPanel)
    setOpen(true)
  }

  const toggleMenu = () => {
    if (open) setOpen(false)
    else openMenu('none')
  }

  /** 专家 chip：未展开时打开并展开专家面板，已展开则收起菜单。 */
  const toggleExpert = () => {
    if (open && panel === 'expert') setOpen(false)
    else openMenu('expert')
  }

  /** 选中文件：交给 Composer 上传到会话目标工作区并进附件草稿（chips 随消息发送）。 */
  const handleFiles = (list: File[]) => {
    if (!list.length) return
    onFiles(list)
  }

  return (
    <div ref={rootRef} className="flex min-w-0 items-center gap-1.5">
      {/* 隐藏文件输入（选中后由 Composer 上传为附件草稿） */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        data-testid="composer-attach-file-input"
        onChange={(e) => {
          handleFiles(Array.from(e.target.files ?? []))
          // 重置 value 使同名文件可重复选择
          e.target.value = ''
          setOpen(false)
        }}
      />

      {/* 「+」触发器（菜单定位锚点） */}
      <button
        type="button"
        disabled={streaming}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="更多操作"
        title="上传文件 / 选择专家 / 选择技能"
        onClick={toggleMenu}
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-md)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent ${
          open && panel === 'none' ? ITEM_OPEN_CLASS : ''
        }`}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M8 3v10M3 8h10" />
        </svg>
      </button>

      {/* 当前专家 chip：点击展开专家面板切换 */}
      {assistant && (
        <button
          type="button"
          disabled={streaming}
          aria-haspopup="menu"
          aria-expanded={open && panel === 'expert'}
          aria-label={`当前专家：${assistant.name}，点击切换`}
          title={`当前专家：${assistant.name}（点击切换）`}
          onClick={toggleExpert}
          className={`inline-flex h-[26px] max-w-[190px] shrink-0 items-center gap-1.5 rounded-[var(--sa-radius-md)] px-2 text-xs text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] disabled:cursor-not-allowed disabled:opacity-60 ${
            open && panel === 'expert' ? ITEM_OPEN_CLASS : ''
          }`}
        >
          <span
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] bg-[var(--sa-specific-sidebar-nav-item-active-accent)] text-[10px] font-medium text-[var(--sa-alias-label-primary)]"
            aria-hidden="true"
          >
            {assistant.avatar?.trim() || assistant.name.slice(0, 1)}
          </span>
          <span className="truncate">{assistant.name}</span>
          <svg
            width="10"
            height="10"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="shrink-0"
            aria-hidden="true"
          >
            <path d="M2.5 4.5 6 8l3.5-3.5" />
          </svg>
        </button>
      )}

      {/* 「+」菜单（portal 到 body，fixed 定位，贴锚点左缘） */}
      {open &&
        anchor &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label="更多操作"
            data-testid="composer-attach-menu"
            className="min-w-[180px] rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
            style={
              direction === 'up'
                ? {
                    position: 'fixed',
                    bottom: window.innerHeight - anchor.top + 10,
                    left: anchor.left,
                    zIndex: 9999,
                  }
                : {
                    position: 'fixed',
                    top: anchor.bottom + 10,
                    left: anchor.left,
                    zIndex: 9999,
                  }
            }
          >
            {/* 上传文件（作为附件随消息发送） */}
            <button
              type="button"
              role="menuitem"
              title="选择文件作为附件，随消息一起发送"
              onClick={() => {
                fileInputRef.current?.click()
              }}
              className={ITEM_CLASS}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="shrink-0"
                aria-hidden="true"
              >
                <path d="M10.5 5.5 6 10a1.77 1.77 0 0 0 2.5 2.5l4.5-4.5a3.54 3.54 0 0 0-5-5L3 8a5.3 5.3 0 0 0 7.5 7.5" />
              </svg>
              <span className="flex-1 truncate">上传文件</span>
            </button>

            {/* 专家 ›（二级面板） */}
            <div className="relative">
              <button
                type="button"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={panel === 'expert'}
                onClick={() => setPanel(panel === 'expert' ? 'none' : 'expert')}
                className={`${ITEM_CLASS} ${panel === 'expert' ? ITEM_OPEN_CLASS : ''}`}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0"
                  aria-hidden="true"
                >
                  <circle cx="8" cy="5.5" r="2.75" />
                  <path d="M2.75 13.5c0-2.35 2.35-3.75 5.25-3.75s5.25 1.4 5.25 3.75" />
                </svg>
                <span className="flex-1 truncate">专家</span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0"
                  aria-hidden="true"
                >
                  <path d="M4.5 2.5 8 6l-3.5 3.5" />
                </svg>
              </button>
              {panel === 'expert' && (
                <ExpertPicker direction={direction} onPicked={() => setOpen(false)} />
              )}
            </div>

            {/* 技能 ›（二级面板，多选） */}
            <div className="relative">
              <button
                type="button"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={panel === 'skill'}
                onClick={() => setPanel(panel === 'skill' ? 'none' : 'skill')}
                className={`${ITEM_CLASS} ${panel === 'skill' ? ITEM_OPEN_CLASS : ''}`}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0"
                  aria-hidden="true"
                >
                  <path d="M8 1.75 9.6 6l4.4.35-3.35 2.9 1 4.3L8 11.15 4.35 13.55l1-4.3L2 6.35 6.4 6 8 1.75Z" />
                </svg>
                <span className="flex-1 truncate">技能</span>
                {selectedSkills.length > 0 && (
                  <span className="shrink-0 text-[11px] text-[var(--sa-alias-state-business-primary)]">
                    {selectedSkills.length}
                  </span>
                )}
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0"
                  aria-hidden="true"
                >
                  <path d="M4.5 2.5 8 6l-3.5 3.5" />
                </svg>
              </button>
              {panel === 'skill' && (
                <SkillPicker
                  direction={direction}
                  selected={selectedSkills}
                  onToggle={onToggleSkill}
                />
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}
