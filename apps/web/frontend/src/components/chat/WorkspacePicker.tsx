/**
 * 输入框空态底部「工作区行」里的工作区选择器（由 ProjectPicker 改造）。
 *
 * 形态照抄 jiuwen `ChatPanel/InputArea.tsx` 的 `chat-work-select` 与
 * `ChatPanel.css` 2240-2354 行：
 * - 触发器 26px 高、12px 字号、圆角 8、`folder 图标 + 名称 + chevron`，默认
 *   底色与所在工作区行同色（即"隐形"贴在灰行上），hover / 展开时变白卡色
 *   （`.chat-work-select__trigger:hover:not(:disabled)` → `--color-surface-card`）；
 * - 展开时 chevron 旋转 180°（`.chat-work-select--open .chat-work-select__chevron`）；
 * - 菜单项为「不使用工作区」+ 工作区列表（当前项打勾）+ 分隔线 +「新建工作区」，
 *   新建走 InputDialog 弹窗（与侧栏「新建工作区」同做法）。
 *
 * 本项目差异（两处，均因交互土壤不同）：
 * - 菜单**向上**弹出：本项目其余浮层（ModelPicker/AttachMenu）均为向上弹，且
 *   空态输入框下方只剩 96px 页脚留白，向下弹会被滚动容器裁掉；
 * - 未选中（null）=「不使用工作区」（照 jiuwen 不选项目的对话：会话目录即
 *   工作区，文件与产物都跟会话走），不自动落任何默认工作区。
 */
import { useEffect, useRef, useState } from 'react'
import InputDialog from '@/components/sidebar/InputDialog'
import { useProjectsStore } from '@/stores/projects'
import { toast } from '@/stores/toasts'

/** 菜单项样式（13px 行，与 ModelPicker/AttachMenu 菜单项保持一致）。 */
const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] ' +
  'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]'

/** 工作区选择器组件（仅空态渲染在工作区行内，见 Composer）。 */
export default function WorkspacePicker() {
  const projects = useProjectsStore((s) => s.projects)
  const currentId = useProjectsStore((s) => s.currentId)
  const loaded = useProjectsStore((s) => s.loaded)
  const load = useProjectsStore((s) => s.load)
  const create = useProjectsStore((s) => s.create)
  const setCurrent = useProjectsStore((s) => s.setCurrent)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // 点击组件外部关闭下拉
  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const current = projects.find((p) => p._id === currentId) ?? null

  /** 展开/收起；展开前兜底重拉（启动引导失败后仍可自愈）。 */
  const toggleOpen = () => {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (!loaded) {
      void load().catch(() => toast('error', '工作区列表加载失败'))
    }
  }

  /** 选中工作区（null = 不使用工作区）并收起下拉。 */
  const pick = (id: string | null) => {
    setOpen(false)
    setCurrent(id)
  }

  /** 新建工作区弹窗（InputDialog，与侧栏同做法）。 */
  const [dialogOpen, setDialogOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  /** 新建工作区确认：成功后自动切为当前（store.create 已置 currentId）。 */
  const createWorkspace = async (name: string) => {
    setCreating(true)
    setCreateError(null)
    try {
      const project = await create(name)
      setDialogOpen(false)
      toast('success', `已创建工作区 ${project.name}`)
    } catch (err) {
      setCreateError((err as Error).message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div ref={rootRef} className="relative min-w-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`当前工作区：${current?.name ?? '不使用工作区'}，点击切换`}
        title={current ? `当前工作区：${current.name}（${current.dir_name}）` : '不使用工作区（会话目录即工作区）'}
        onClick={toggleOpen}
        className={`inline-flex h-[26px] w-auto max-w-full items-center gap-1.5 rounded-[8px] px-3 text-xs text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-specific-input-major)] ${
          open ? 'bg-[var(--sa-specific-input-major)]' : ''
        }`}
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
          <path d="M1.75 4A1.25 1.25 0 0 1 3 2.75h2.9l1.3 1.75H13A1.25 1.25 0 0 1 14.25 5.75v6.5A1.25 1.25 0 0 1 13 13.5H3a1.25 1.25 0 0 1-1.25-1.25V4Z" />
        </svg>
        <span className="min-w-0 truncate">{current?.name ?? '不使用工作区'}</span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={`shrink-0 transition-transform duration-[var(--sa-duration-base)] ${open ? 'rotate-180' : ''}`}
        >
          <path d="M6 8l4 4 4-4" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="选择工作区"
          className="absolute bottom-full left-0 z-50 mb-1.5 max-h-64 w-56 overflow-y-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
        >
          {/* 首项：不使用工作区（会话目录即工作区） */}
          <button
            role="menuitemradio"
            aria-checked={currentId === null}
            type="button"
            title="不使用工作区（会话目录即工作区，文件与产物都跟会话走）"
            onClick={() => pick(null)}
            className={ITEM_CLASS}
          >
            <span className="min-w-0 flex-1 truncate">不使用工作区</span>
            {currentId === null && (
              <svg
                width="13"
                height="13"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="shrink-0 text-[var(--sa-alias-state-business-primary)]"
                aria-hidden="true"
              >
                <path d="M3 8.5 6.5 12 13 4.5" />
              </svg>
            )}
          </button>

          {projects.length === 0 ? (
            <div className="px-2.5 py-1.5 text-xs text-[var(--sa-alias-label-caption)]">
              {loaded ? '暂无工作区' : '加载中…'}
            </div>
          ) : (
            projects.map((p) => (
              <button
                key={p._id}
                role="menuitemradio"
                aria-checked={p._id === currentId}
                type="button"
                title={p.name}
                onClick={() => pick(p._id)}
                className={ITEM_CLASS}
              >
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                {p._id === currentId && (
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="shrink-0 text-[var(--sa-alias-state-business-primary)]"
                    aria-hidden="true"
                  >
                    <path d="M3 8.5 6.5 12 13 4.5" />
                  </svg>
                )}
              </button>
            ))
          )}

          {/* 分隔线 + 新建工作区 */}
          <div className="my-1 h-px bg-[var(--sa-alias-border-l1)]" aria-hidden="true" />
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false)
              setCreateError(null)
              setDialogOpen(true)
            }}
            className={ITEM_CLASS}
          >
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
              <path d="M8 3.5v9M3.5 8h9" />
            </svg>
            新建工作区
          </button>
        </div>
      )}

      {/* 新建工作区弹窗（与侧栏同款） */}
      {dialogOpen && (
        <InputDialog
          title="新建工作区"
          placeholder="输入工作区名称"
          confirmLabel="创建"
          busy={creating}
          error={createError}
          onCancel={() => setDialogOpen(false)}
          onConfirm={(name) => void createWorkspace(name)}
        />
      )}
    </div>
  )
}
