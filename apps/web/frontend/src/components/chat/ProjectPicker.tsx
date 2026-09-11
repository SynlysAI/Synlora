/**
 * 当前项目选择 chip（输入框下方）。
 *
 * 形态与字号照抄 jiuwen InputArea 的「选择项目目录」触发器
 * （`chat-work-select__trigger`：26px 高、12px 字号、圆角、文件夹图标 +
 * 名称 + 展开箭头；菜单项 32px 高、当前项打勾、末尾「新建项目」）。
 * 弹层位置沿用本项目 ModelPicker：位于页面底部，向上弹出。
 * 新建项目 V1 用 window.prompt 取名字，不做独立弹窗。
 */
import { useEffect, useRef, useState } from 'react'
import { useProjectsStore } from '@/stores/projects'
import { toast } from '@/stores/toasts'

/** 菜单项样式（13px 行，与 ModelPicker 的菜单项保持一致）。 */
const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] ' +
  'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]'

/** 项目选择器组件（Composer 内输入框下方）。 */
export default function ProjectPicker() {
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

  /** 展开前兜底重拉（启动引导失败后仍可自愈）。 */
  const toggleOpen = () => {
    setOpen((v) => !v)
    if (!loaded) void load().catch(() => toast('error', '项目列表加载失败'))
  }

  /** 选中项目并收起下拉。 */
  const pick = (id: string) => {
    setOpen(false)
    setCurrent(id)
  }

  /** 新建项目：prompt 取名字（空名/纯空白忽略），成功后自动切为当前。 */
  const createProject = async () => {
    setOpen(false)
    const name = window.prompt('新建项目名称')?.trim()
    if (!name) return
    try {
      const project = await create(name)
      toast('success', `已创建项目 ${project.name}`)
    } catch (err) {
      toast('error', `新建项目失败：${(err as Error).message}`)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`当前项目：${current?.name ?? '未选择'}，点击切换`}
        title={current ? `当前项目：${current.name}（${current.dir_name}）` : '选择项目目录'}
        onClick={toggleOpen}
        className="inline-flex h-[26px] max-w-[220px] items-center gap-1.5 rounded-[var(--sa-radius-md)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 text-xs text-[var(--sa-alias-label-primary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-active)]"
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
          className="shrink-0 text-[var(--sa-alias-label-tertiary)]"
          aria-hidden="true"
        >
          <path d="M1.75 4A1.25 1.25 0 0 1 3 2.75h2.9l1.3 1.75H13A1.25 1.25 0 0 1 14.25 5.75v6.5A1.25 1.25 0 0 1 13 13.5H3a1.25 1.25 0 0 1-1.25-1.25V4Z" />
        </svg>
        <span className="truncate">{current?.name ?? '选择项目目录'}</span>
        <svg
          width="10"
          height="10"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={`shrink-0 transition-transform duration-[var(--sa-duration-fast)] ${open ? 'rotate-180' : ''}`}
        >
          <path d="M2.5 4.5 6 8l3.5-3.5" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="选择项目"
          className="absolute bottom-full left-0 z-50 mb-1.5 max-h-64 w-56 overflow-y-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
        >
          {projects.length === 0 ? (
            <div className="px-2.5 py-1.5 text-xs text-[var(--sa-alias-label-caption)]">
              {loaded ? '暂无项目' : '加载中…'}
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

          {/* 分隔线 + 新建项目 */}
          <div className="my-1 h-px bg-[var(--sa-alias-border-l1)]" aria-hidden="true" />
          <button
            role="menuitem"
            type="button"
            onClick={() => void createProject()}
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
            新建项目
          </button>
        </div>
      )}
    </div>
  )
}
