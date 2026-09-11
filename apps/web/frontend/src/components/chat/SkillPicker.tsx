/**
 * 「+」菜单「技能」二级面板：搜索 + 技能列表（多选，选中打勾）。
 *
 * 照抄 jiuwen `ChatPanel/SkillPickerPanel.tsx` 的列表结构（名称 + 描述两行、
 * 选中打勾），壳走本项目 PickerPanel；选择态由 Composer 持有（受控），
 * 因为技能是「本轮一次性」语义——发送后清空，不需要进 store 长期保留。
 */
import { useEffect, useMemo, useState } from 'react'
import { useSkillsStore } from '@/stores/skills'
import PickerPanel from './PickerPanel'

interface SkillPickerProps {
  /** 一级菜单展开方向（同步二级面板的生长方向）。 */
  direction: 'up' | 'down'
  /** 已选技能名（受控）。 */
  selected: string[]
  /** 勾选/取消勾选一个技能。 */
  onToggle: (name: string) => void
}

/** 技能选择面板（多选）。 */
export default function SkillPicker({ direction, selected, onToggle }: SkillPickerProps) {
  const skills = useSkillsStore((s) => s.skills)
  const loaded = useSkillsStore((s) => s.loaded)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  // 首次展开拉取技能列表（失败仅面板内提示，不影响发送）
  useEffect(() => {
    if (loaded) return
    void useSkillsStore
      .getState()
      .load()
      .catch((err: Error) => setError(err.message))
  }, [loaded])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.tags.some((t) => t.toLowerCase().includes(q)),
    )
  }, [skills, query])

  return (
    <PickerPanel
      direction={direction}
      widthClass="w-[300px]"
      maxHeightClass="max-h-[358px]"
      ariaLabel="选择本轮技能"
      testId="composer-skill-picker"
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索技能"
    >
      {error ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-state-error-primary)]">
          技能加载失败：{error}
        </div>
      ) : !loaded ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          加载中…
        </div>
      ) : skills.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          暂无技能
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-2 py-3 text-center text-xs text-[var(--sa-alias-label-caption)]">
          无匹配结果
        </div>
      ) : (
        filtered.map((s) => {
          const isSelected = selected.includes(s.name)
          return (
            <button
              key={s.name}
              role="menuitemcheckbox"
              aria-checked={isSelected}
              type="button"
              title={s.description}
              onClick={() => onToggle(s.name)}
              className={`flex w-full items-start gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 text-left transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] ${
                isSelected ? 'bg-[var(--sa-alias-interactive-bg-hover)]' : ''
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]">
                  {s.name}
                </span>
                <span className="block truncate text-xs text-[var(--sa-alias-label-caption)]">
                  {s.description}
                </span>
              </span>
              {isSelected && (
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
          )
        })
      )}
    </PickerPanel>
  )
}
