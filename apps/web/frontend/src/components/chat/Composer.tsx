/**
 * 底部输入区：卡片内「已选技能 chips + 多行自适应输入 + 底部功能行」。
 * Enter 发送 / Shift+Enter 换行（IME 组合中的 Enter 不发送）；streaming 时
 * 输入禁用且发送变停止按钮；错误提示条（含 429 话术）。
 *
 * 卡片两态照抄 jiuwen `ChatPanel/InputArea.tsx` 2703-2728 行与
 * `ChatPanel.css` 125-170 行（`showWorkContextRow = 空会话`，见 800 行）：
 * - **空态**（`empty`，即欢迎页）：外层灰卡（`--work-home` 变体：border 0、
 *   radius 24、padding 4px 4px 0、有阴影）+ 内层白卡（border 1px、radius 20），
 *   卡片底部再挂一行**工作区行**（`chat-work-context-row`：min-height 44px、
 *   radius 0 0 24 24、与外层卡同底色），行内是 WorkspacePicker；
 * - **有消息**：退回单层白卡（= 内层白卡那一层，无外层灰卡、无工作区行）。
 *
 * 底部功能行布局照 jiuwen InputArea 的 `chat-input-toolbar`：
 * 左 = 「+」菜单（上传/专家/技能，含当前专家 chip，见 AttachMenu），
 * 右 = 模型选择器 + 发送/停止圆形按钮（模型选择器紧挨发送键左侧，照 jiuwen
 * `chat-input-actions` 把 ChatModelSelector 放在发送键前）。
 * 已选技能是「本轮一次性」语义：发送后清空。
 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useChatStore } from '@/stores/chat'
import AttachMenu from './AttachMenu'
import ModelPicker from './ModelPicker'
import WorkspacePicker from './WorkspacePicker'

/** 输入框最大高度（px，约 6 行：行高 22 + 上下 padding 12×2 + 边框）。 */
const MAX_INPUT_HEIGHT = 148

/**
 * 输入框最小高度（px）：空态约 2 行、有消息约 1.5 行（行高 22）。
 * 比 jiuwen `.chat-input-editor`（88/96）紧凑得多——本项目是消息流型
 * 工作台而非编码场景，输入区不宜长期占位。
 */
const MIN_INPUT_HEIGHT_EMPTY = 48
const MIN_INPUT_HEIGHT_CHAT = 32

/**
 * 卡片样式：外层灰卡（仅空态，jiuwen `.chat-input-container--work-home`）与
 * 内层白卡 / 有消息时的单层白卡（jiuwen `--work-home .chat-input-body`）。
 * 底色用组件层 token（外层 `--sa-specific-selector` 灰、内层
 * `--sa-specific-input-major` 白），边框走别名层 l2，与 jiuwen 的
 * action-secondary / surface-card / border-default 三件套一一对应；
 * 外层阴影用 Tailwind `shadow-md`（本项目无 shadow token，同类浮层一律
 * 走 Tailwind 档位），对应 jiuwen 的 `--effect-shadow-md`。
 */
const OUTER_CARD_CLASS =
  'rounded-[24px] bg-[var(--sa-specific-selector)] p-1 pb-0 shadow-md'
const INNER_CARD_CLASS =
  'rounded-[20px] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-3.5 pt-2.5 pb-2 ' +
  'transition-colors duration-[var(--sa-duration-base)] focus-within:border-[var(--sa-alias-border-l4)]'

interface ComposerProps {
  /** 会话是否还没有任何消息（空态 = 欢迎页形态：双层卡 + 工作区行）。 */
  empty: boolean
}

/**
 * 输入框组件（中间列下部）。
 *
 * @param props 见 ComposerProps。
 */
export default function Composer({ empty }: ComposerProps) {
  const streaming = useChatStore((s) => s.streaming)
  const error = useChatStore((s) => s.error)
  const send = useChatStore((s) => s.send)
  const stop = useChatStore((s) => s.stop)
  const clearError = useChatStore((s) => s.clearError)
  const [value, setValue] = useState('')
  /** 本轮勾选的技能名（一次性：发送后清空，见 submit）。 */
  const [skills, setSkills] = useState<string[]>([])
  const ref = useRef<HTMLTextAreaElement>(null)

  /** 勾选/取消勾选一个技能（「+」菜单技能面板回调）。 */
  const toggleSkill = (name: string) =>
    setSkills((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    )

  // 自适应高度：先归零再量 scrollHeight，钳制到最大值
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`
    el.style.overflowY = el.scrollHeight > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
  }, [value])

  /** 发送当前输入（空串/流式中忽略）；技能随本轮请求带上后清空（一次性）。 */
  const submit = () => {
    const text = value.trim()
    if (!text || streaming) return
    setValue('')
    void send(text, skills)
    setSkills([])
  }

  /** Enter 发送 / Shift+Enter 换行；中文输入法组合中的 Enter 不发送。 */
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  };

  /** 卡片内容（两种形态共用）：已选技能 chips + 输入区 + 底部功能行。 */
  const body = (
    <>
      {/* 已选技能 chips（输入框上方，可逐个删除；发送后清空） */}
      {skills.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 pb-1.5">
          {skills.map((name) => (
            <span
              key={name}
              className="inline-flex h-6 max-w-[180px] items-center gap-1 rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-interactive-bg-hover)] pl-2.5 pr-1 text-xs text-[var(--sa-alias-label-secondary)]"
            >
              <span className="truncate">{name}</span>
              <button
                type="button"
                aria-label={`移除技能 ${name}`}
                title={`移除技能 ${name}`}
                onClick={() => toggleSkill(name)}
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-active)] hover:text-[var(--sa-alias-label-primary)]"
              >
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}
      <textarea
        ref={ref}
        rows={1}
        value={value}
        disabled={streaming}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={streaming ? '回复生成中…' : '给 SynlysAgent 发送消息'}
        aria-label="消息输入框"
        style={{ minHeight: empty ? MIN_INPUT_HEIGHT_EMPTY : MIN_INPUT_HEIGHT_CHAT }}
        className="max-h-[148px] w-full resize-none bg-transparent py-0.5 text-[15px] leading-[22px] text-[var(--sa-alias-label-primary)] placeholder:text-[var(--sa-alias-label-caption)] outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
      {/* 底部功能行：左「+」菜单（含专家 chip），右模型选择器 + 发送/停止 */}
      <div className="flex items-center justify-between gap-2 pt-1.5">
        <AttachMenu selectedSkills={skills} onToggleSkill={toggleSkill} />
        <div className="flex shrink-0 items-center gap-1.5">
          <ModelPicker />
          {/* 发送 / 停止按钮（DSH：accent 蓝圆形） */}
          {streaming ? (
            <button
              type="button"
              aria-label="停止生成"
              title="停止生成"
              onClick={() => void stop()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-static-blue-500)] text-white transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-static-blue-600)]"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              aria-label="发送"
              title="发送（Enter）"
              onClick={submit}
              disabled={!value.trim()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-static-blue-500)] text-white transition-colors duration-[var(--sa-duration-base)] hover:bg-[var(--sa-static-blue-600)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M8 13V3M3.8 7.2 8 3l4.2 4.2" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </>
  )

  return (
    <div className="shrink-0 px-4 pb-4 sm:px-6">
      <div className="mx-auto max-w-3xl">
        {/* 错误提示条（429 / 网络错误等；消息保留可重发） */}
        {error && (
          <div
            role="alert"
            className="mb-2 flex items-start gap-2 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-state-error-primary)] bg-[var(--sa-alias-interactive-bg-hover-danger)] px-3 py-2 text-[13px] text-[var(--sa-alias-state-error-primary)]"
          >
            <span className="min-w-0 flex-1 break-words">{error}</span>
            <button
              type="button"
              aria-label="关闭错误提示"
              onClick={clearError}
              className="shrink-0 rounded-[var(--sa-radius-sm)] p-0.5 transition-colors hover:bg-[var(--sa-alias-interactive-bg-hover)]"
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </button>
          </div>
        )}

        {/*
          卡片两态（见文件头）：空态 = 外层灰卡 + 内层白卡 + 底部工作区行；
          有消息 = 单层白卡。内层白卡在两种形态下完全一致。
        */}
        {empty ? (
          <div className={OUTER_CARD_CLASS}>
            <div className={INNER_CARD_CLASS}>{body}</div>
            {/* 工作区行（jiuwen `chat-work-context-row`）：圆角 0 0 24 24、与外层卡同底色 */}
            <div className="mt-1 flex min-h-[44px] items-center gap-2 rounded-b-[24px] bg-[var(--sa-specific-selector)] px-3 pt-1">
              <WorkspacePicker />
            </div>
          </div>
        ) : (
          <div className={INNER_CARD_CLASS}>{body}</div>
        )}
      </div>
    </div>
  )
}
