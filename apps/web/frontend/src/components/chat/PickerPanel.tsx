/**
 * 「+」菜单二级面板公共壳（照抄 jiuwen `ChatPanel/PickerPanel.tsx` 的结构：
 * 搜索行 + 内容列表 + 面板壳，尺寸由调用方给的 class 提供）。
 *
 * 定位照抄 jiuwen `ChatPanel.css` 1811-1840 行：三张二级面板统一
 * `position: absolute` 挂在各自触发项的包装层（relative）内，`left: calc(100% + 11px)`、
 * `top: 0` 与触发项顶边齐平；一级菜单贴近视口底部向上展开（direction='up'）时
 * 改为 `bottom: 0` 与触发项底边齐平、向上生长。
 *
 * 本项目映射：搜索框/条目密度照本项目已有弹层（ModelPicker/WorkspacePicker 的
 * 13px 行、12px 辅助字），面板壳 token 用 `--sa-*`。
 *
 * 视口收窄：面板高度上限由调用方 class 给定（如 max-h-[358px]），但面板是
 * absolute 弹在触发项旁的——一级菜单向下展开时面板从触发项顶边向下生长，
 * 内容多时会戳出视口底部（向上展开时同理顶出上沿）。挂载后按定位父级
 * （触发项包装层）到视口边缘的剩余空间动态压低 max-height，保证面板
 * 始终完整可见、列表内部滚动。
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

interface PickerPanelProps {
  /** 弹出方向：up 时与触发项底边齐平向上生长（一级菜单向上弹时用）。 */
  direction: 'up' | 'down'
  /** 面板宽度 class（如 w-[248px]）。 */
  widthClass: string
  /** 面板最大高度 class（如 max-h-[360px]）。 */
  maxHeightClass: string
  /** 面板可访问名（role=menu）。 */
  ariaLabel: string
  /** 稳定的测试钩子。 */
  testId: string
  /** 搜索关键词（受控）。 */
  query: string
  /** 搜索关键词变更回调。 */
  onQueryChange: (value: string) => void
  /** 搜索框占位文案。 */
  searchPlaceholder: string
  /** 列表内容（条目或加载/空态）。 */
  children: ReactNode
}

/** 面板边缘与视口边缘的最小间距（px）。 */
const VIEWPORT_MARGIN = 8

/** 压低后的面板最小可用高度（px），极矮窗口下仍保证搜索行 + 几条列表可见。 */
const MIN_PANEL_HEIGHT = 120

/** 二级面板公共壳（「+」菜单内 absolute 弹出）。 */
export default function PickerPanel({
  direction,
  widthClass,
  maxHeightClass,
  ariaLabel,
  testId,
  query,
  onQueryChange,
  searchPlaceholder,
  children,
}: PickerPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  /** 视口剩余空间（px）；null = 未测量（首帧）或空间充足无需干预。 */
  const [clampedHeight, setClampedHeight] = useState<number | null>(null)

  // 挂载后按方向测量定位父级到视口边缘的剩余空间，小于设计上限时压低 max-height
  useLayoutEffect(() => {
    const el = rootRef.current
    if (!el) return
    // 面板 max-height 由 class 给定（无 class 时 computed 为 none，不设上限）
    const designCap = Number.parseFloat(getComputedStyle(el).maxHeight)
    // absolute 面板的 offsetParent 即触发项包装层（relative），其矩形即面板对齐基准
    const parent = (el.offsetParent as HTMLElement | null) ?? el
    const rect = parent.getBoundingClientRect()
    const space =
      direction === 'up'
        ? rect.bottom - VIEWPORT_MARGIN
        : window.innerHeight - rect.top - VIEWPORT_MARGIN
    if (Number.isNaN(designCap) || space >= designCap) {
      setClampedHeight(null)
      return
    }
    setClampedHeight(Math.max(space, MIN_PANEL_HEIGHT))
  }, [direction])

  return (
    <div
      ref={rootRef}
      role="menu"
      aria-label={ariaLabel}
      data-testid={testId}
      className={`absolute left-[calc(100%+11px)] z-10 flex flex-col rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg ${direction === 'up' ? 'bottom-0' : 'top-0'} ${widthClass} ${maxHeightClass}`}
      style={clampedHeight !== null ? { maxHeight: clampedHeight } : undefined}
    >
      {/* 搜索行（下划线分隔，照 jiuwen .chat-picker-panel__search） */}
      <label className="flex h-7 shrink-0 items-center gap-1.5 px-2 text-[var(--sa-alias-label-tertiary)]">
        <svg
          width="13"
          height="13"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0"
          aria-hidden="true"
        >
          <circle cx="7" cy="7" r="4.25" />
          <path d="m10.5 10.5 3 3" />
        </svg>
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={searchPlaceholder}
          className="min-w-0 flex-1 bg-transparent text-xs text-[var(--sa-alias-label-primary)] outline-none placeholder:text-[var(--sa-alias-label-caption)]"
        />
      </label>
      <div className="mx-1 mb-1 h-px shrink-0 bg-[var(--sa-alias-border-l1)]" aria-hidden="true" />

      {/* 内容列表：超过 max-height 由列表自身滚动 */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</div>
    </div>
  )
}
