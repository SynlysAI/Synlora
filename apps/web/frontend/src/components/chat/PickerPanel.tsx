/**
 * 「+」菜单二级面板公共壳（照抄 jiuwen `ChatPanel/PickerPanel.tsx` 的结构：
 * 搜索行 + 内容列表 + 面板壳，尺寸由调用方给的 class 提供）。
 *
 * 定位照抄 jiuwen `ChatPanel.css` 1811-1840 行：三张二级面板统一
 * `position: absolute` 挂在各自触发项的包装层（relative）内，`left: calc(100% + 11px)`、
 * `top: 0` 与触发项顶边齐平；一级菜单贴近视口底部向上展开（direction='up'）时
 * 改为 `bottom: 0` 与触发项底边齐平、向上生长。
 *
 * 本项目映射：搜索框/条目密度照本项目已有弹层（ModelPicker/ProjectPicker 的
 * 13px 行、12px 辅助字），面板壳 token 用 `--sa-*`。
 */
import type { ReactNode } from 'react'

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
  return (
    <div
      role="menu"
      aria-label={ariaLabel}
      data-testid={testId}
      className={`absolute left-[calc(100%+11px)] z-10 flex flex-col rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg ${direction === 'up' ? 'bottom-0' : 'top-0'} ${widthClass} ${maxHeightClass}`}
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
