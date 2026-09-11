/**
 * 项目目录树（右栏工作区）。
 *
 * 结构照抄 DSH ui-sidebar-files 的 FilesBody.tsx：
 * - 嵌套 DOM 递归：每层一个 <ul>，只渲染一级条目，展开的目录内再嵌 <ul>；
 * - 每层组件自己发请求：Level 挂载时才 GET /api/v1/projects/{pid}/tree?path=…
 *   （折叠即不再请求，展开态与各层数据缓存在根组件，收起再展开不重复请求）；
 * - 排序与 DSH `orderEntries` 逐行对应：目录优先，同级用 Intl.Collator 自然排序；
 * - 顶部根行 = 项目名 + 刷新（DSH 的 header 行：目录灰、末段全墨；刷新丢弃
 *   各层缓存并重新取已展开的层）。
 * 与 DSH 的差异：文件行不可点击打开（本项目暂无文件查看器），改为展示大小；
 * 切换项目由调用方以 key={projectId} 重挂载整棵树（等价于 DSH 按 tab 分桶）。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { TreeEntry } from '@/types'
import { api } from '@/api/client'
import { formatBytes } from '@/utils/format'

/** 自然、大小写不敏感的名称排序，使 file2 排在 file10 之前。 */
const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/**
 * 单层条目排序：目录在前，其余在后，各组内按名称。
 *
 * @param entries 接口返回的原始条目顺序。
 * @returns 新数组：目录优先，组内按名称自然序。
 */
function orderEntries(entries: readonly TreeEntry[]): TreeEntry[] {
  return [...entries].sort((left, right) => {
    const group = Number(right.is_dir) - Number(left.is_dir)
    return group !== 0 ? group : byName.compare(left.name, right.name)
  })
}

/** 一层的加载状态（缓存于根组件）。 */
type LevelState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'ready'; entries: TreeEntry[] }

/** 各层共享的上下文：数据 + 两个手势。 */
interface TreeContext {
  /** 已列出的层，键为该层相对项目根的路径（根为空串）。 */
  levels: Record<string, LevelState>
  /** 已展开的目录路径。 */
  expanded: string[]
  /** 缓存代次：变化时已挂载的层重新请求。 */
  version: number
  /** 确保某层已请求（重复调用无副作用）。 */
  ensure: (path: string) => void
  /** 展开/收起目录。 */
  toggle: (path: string) => void
}

/** 行的通用样式（DSH `.row`：整行 hover 填充 + 10px 圆角，行间不留缝）。 */
const ROW_CLASS =
  'flex w-full min-w-0 items-center gap-1.5 rounded-[var(--sa-radius-md)] px-2.5 py-[5px] text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)]'

/** 非交互行（文件）的样式：占满整行，不与 hover 态绑定。 */
const STATIC_ROW_CLASS = `${ROW_CLASS} cursor-default`

/** 提示行（加载中 / 空 / 失败）样式（DSH `.note`）。 */
const NOTE_CLASS = 'px-2.5 py-[3px] text-xs text-[var(--sa-alias-label-tertiary)]'

/** 文件夹图标（收起态，DSH IconFolderClose16 的内联等价物）。 */
function FolderIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-[var(--sa-alias-label-tertiary)]"
      aria-hidden="true"
    >
      <path d="M1.75 4.25A1.25 1.25 0 0 1 3 3h2.6l1.4 1.75H13A1.25 1.25 0 0 1 14.25 6v5.75A1.25 1.25 0 0 1 13 13H3a1.25 1.25 0 0 1-1.25-1.25V4.25Z" />
    </svg>
  )
}

/** 文件夹图标（展开态）。 */
function FolderOpenIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-[var(--sa-alias-label-tertiary)]"
      aria-hidden="true"
    >
      <path d="M1.75 4.25A1.25 1.25 0 0 1 3 3h2.6l1.4 1.75H13A1.25 1.25 0 0 1 14.25 6v.75" />
      <path d="M1.75 6.5h12.5l-1.4 5.3A1.25 1.25 0 0 1 11.64 13H3a1.25 1.25 0 0 1-1.25-1.25V6.5Z" />
    </svg>
  )
}

/** 文件图标（沿用本项目既有文件面板的内联文档字形）。 */
function FileIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-[var(--sa-alias-label-caption)]"
      aria-hidden="true"
    >
      <path d="M9 1.75H3.75A1.25 1.25 0 0 0 2.5 3v10a1.25 1.25 0 0 0 1.25 1.25h8.5A1.25 1.25 0 0 0 13.5 13V6.25L9 1.75Z" />
      <path d="M9 1.75v4.5h4.5" />
    </svg>
  )
}

/** 一个条目：目录行（可展开）+ 展开时的子层，或文件行（名称 + 大小）。 */
function Entry({ entry, tree }: { entry: TreeEntry; tree: TreeContext }): ReactNode {
  // 后端返回的 path 已相对项目根，直接作为下一层的 path 参数与展开键
  const path = entry.path
  if (entry.is_dir) {
    const expanded = tree.expanded.includes(path)
    return (
      <li className="m-0 p-0">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => tree.toggle(path)}
          className={`${ROW_CLASS} hover:bg-[var(--sa-alias-interactive-bg-hover)]`}
        >
          {expanded ? <FolderOpenIcon /> : <FolderIcon />}
          <span className="min-w-0 flex-1 truncate">{entry.name}</span>
        </button>
        {expanded && (
          // 每嵌套一层缩进一级（DSH `.level .level { padding-left: 18px }`）
          <ul className="m-0 list-none p-0 pl-[18px]">
            <Level path={path} tree={tree} />
          </ul>
        )}
      </li>
    )
  }
  return (
    <li className="m-0 p-0">
      <span className={STATIC_ROW_CLASS} title={entry.name}>
        <FileIcon />
        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
        <span className="shrink-0 text-xs tabular-nums text-[var(--sa-alias-label-caption)]">
          {formatBytes(entry.size)}
        </span>
      </span>
    </li>
  )
}

/** 一层目录的内容：加载中 / 失败 / 条目列表。 */
function Level({ path, tree }: { path: string; tree: TreeContext }): ReactNode {
  const { levels, ensure, version } = tree
  const state = levels[path]

  // 挂载即请求本层（根层与展开的子层各发一次；缓存命中 / 已请求时 ensure 直接返回；
  // version 变化表示缓存刚被丢弃，需重新请求）
  useEffect(() => {
    ensure(path)
  }, [ensure, path, version])

  if (state === undefined || state.kind === 'loading') {
    return <li className={NOTE_CLASS}>加载中…</li>
  }
  if (state.kind === 'failed') {
    return <li className={NOTE_CLASS}>目录读取失败：{state.message}</li>
  }
  const entries = orderEntries(state.entries)
  if (entries.length === 0) return <li className={NOTE_CLASS}>空目录</li>
  return (
    <>
      {entries.map((entry) => (
        <Entry key={entry.path} entry={entry} tree={tree} />
      ))}
    </>
  )
}

interface FileTreeProps {
  /** 当前项目 id（null 表示尚未选中项目）。 */
  projectId: string | null
  /** 项目名（顶部根行展示）。 */
  rootName: string
  /** 外部刷新信号（递增即丢弃各层缓存重取，如上传成功）。 */
  refreshToken?: number
}

/** 项目目录树组件（右栏工作区下半部）。 */
export default function FileTree({ projectId, rootName, refreshToken = 0 }: FileTreeProps) {
  const [levels, setLevels] = useState<Record<string, LevelState>>({})
  const [expanded, setExpanded] = useState<string[]>([])
  const [version, setVersion] = useState(0)
  // 已发起请求的层（防止重复请求；清缓存时一并清空以便重取）
  const requested = useRef(new Set<string>())

  /** 丢弃各层缓存（保留展开态：已展开的层因缓存失效由 Level 重新请求）。 */
  const dropLevels = useCallback(() => {
    setLevels({})
    requested.current = new Set()
    setVersion((v) => v + 1)
  }, [])

  // 切换项目不走这里：调用方以 key={projectId} 重挂载整棵树（展开态与缓存
  // 天然清空，且不会先用新项目请求一次再被父级清掉）。

  // 外部刷新信号：只丢缓存，保留展开态
  const lastRefresh = useRef(refreshToken)
  useEffect(() => {
    if (lastRefresh.current === refreshToken) return
    lastRefresh.current = refreshToken
    dropLevels()
  }, [refreshToken, dropLevels])

  /** 请求某层（已请求过或已缓存则直接返回）。 */
  const ensure = useCallback(
    (path: string) => {
      if (!projectId || requested.current.has(path)) return
      requested.current.add(path)
      setLevels((prev) => ({ ...prev, [path]: { kind: 'loading' } }))
      const query = path ? `?path=${encodeURIComponent(path)}` : ''
      api<TreeEntry[]>(`/api/v1/projects/${projectId}/tree${query}`)
        .then((entries) => {
          setLevels((prev) => ({ ...prev, [path]: { kind: 'ready', entries } }))
        })
        .catch((err: Error) => {
          setLevels((prev) => ({ ...prev, [path]: { kind: 'failed', message: err.message } }))
        })
    },
    [projectId],
  )

  /** 展开/收起目录。 */
  const toggle = useCallback((path: string) => {
    setExpanded((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    )
  }, [])

  const tree: TreeContext = { levels, expanded, version, ensure, toggle }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 根行：项目名 + 刷新（DSH header 行） */}
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--sa-alias-border-l3)] pl-3 pr-1.5">
        <span
          className="min-w-0 flex-1 truncate text-xs text-[var(--sa-alias-label-primary)]"
          title={rootName}
        >
          {rootName}
        </span>
        <button
          type="button"
          aria-label="刷新目录树"
          title="刷新目录树"
          onClick={dropLevels}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M13.25 8a5.25 5.25 0 1 1-1.6-3.78M13.25 2.5v3.25H10" />
          </svg>
        </button>
      </div>

      {/* 各层：根层常驻，子层随展开挂载 */}
      <div className="min-h-0 flex-1 overflow-auto px-2 py-2">
        <ul className="m-0 list-none p-0">
          <Level path="" tree={tree} />
        </ul>
      </div>
    </div>
  )
}
