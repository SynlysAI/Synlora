import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { ToastHost } from './ToastHost'
import { VersionTag } from './VersionTag'
import BrandMark from './BrandMark'
import BrandWordmark from './BrandWordmark'
import { ChatPanel, ConversationNotFound } from '@/components/chat'
import { Rightbar } from '@/components/rightbar'
import { Sidebar } from '@/components/sidebar'
import { useAuthStore } from '@/stores/auth'
import { useRouterStore } from '@/routing/router'
import { FolderIcon } from '@/components/sidebar/icons'
import { useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import { useDarkTheme } from '@/utils/theme'

/** 左栏宽度默认值与拖拽钳制范围（px）。 */
const LEFT_DEFAULT = 280
const LEFT_MIN = 240
const LEFT_MAX = 400
/** 右栏宽度默认值与拖拽钳制范围（px）。 */
const RIGHT_DEFAULT = 320
const RIGHT_MIN = 240
const RIGHT_MAX = 480
/** 视口小于该宽度时左栏切换为 overlay 抽屉。 */
const COMPACT_QUERY = '(max-width: 899px)'

/** 右栏两态：隐藏 / 展开。 */
type RightPanelState = 'hidden' | 'normal'

/** 数值钳制到 [min, max]。 */
const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

interface DragHandleProps {
  /** 手柄归属栏：left 取右缘、right 取左缘。 */
  side: 'left' | 'right'
  /** 拖拽起始时的栏宽（px）。 */
  width: number
  onResize: (width: number) => void
  onDraggingChange: (dragging: boolean) => void
}

/** 栏宽拖拽手柄：4px 热区，pointer capture + rAF 节流。 */
function DragHandle({ side, width, onResize, onDraggingChange }: DragHandleProps) {
  const dragRef = useRef({ pointerId: -1, startX: 0, startWidth: 0 })
  const rafRef = useRef(0)

  /** 开始拖拽：捕获指针，锁定 body 光标并通知父级暂停列宽过渡。 */
  const handlePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { pointerId: e.pointerId, startX: e.clientX, startWidth: width }
    onDraggingChange(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  /** 拖拽移动：rAF 节流计算新宽度（左栏向右拖变宽，右栏向左拖变宽）。 */
  const handlePointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current.pointerId !== e.pointerId || rafRef.current) return
    const { startX, startWidth } = dragRef.current
    const clientX = e.clientX
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      const delta = (clientX - startX) * (side === 'left' ? 1 : -1)
      const next = clamp(
        startWidth + delta,
        side === 'left' ? LEFT_MIN : RIGHT_MIN,
        side === 'left' ? LEFT_MAX : RIGHT_MAX,
      )
      onResize(next)
    })
  }

  /** 结束/取消拖拽：释放光标锁与过渡暂停。 */
  const endDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current.pointerId !== e.pointerId) return
    dragRef.current.pointerId = -1
    cancelAnimationFrame(rafRef.current)
    rafRef.current = 0
    onDraggingChange(false)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={side === 'left' ? '调整左栏宽度' : '调整右栏宽度'}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      className="absolute inset-y-0 z-20 w-1 cursor-col-resize touch-none"
      style={{ [side === 'left' ? 'right' : 'left']: 0 } as CSSProperties}
    />
  )
}

interface IconButtonProps {
  label: string
  onClick: () => void
  children: ReactNode
}

/** 图标按钮（ghost 交互 token）。 */
function IconButton({ label, onClick, children }: IconButtonProps) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-secondary)] transition-colors duration-200 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
    >
      {children}
    </button>
  )
}

/** 16px 线性图标。 */
const iconProps = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

/** 用户菜单（左栏底部）：头像按钮 + 下拉（能力中心 / 管理后台入口仅 admin / 退出登录）。 */
function UserMenu() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useRouterStore((s) => s.navigate)
  const [open, setOpen] = useState(false)
  if (!user) return null

  /** 下拉菜单项样式。 */
  const itemClass =
    'flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-sm ' +
    'text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] ' +
    'hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]'

  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`用户菜单：${user.username}`}
        onClick={() => setOpen((v) => !v)}
        className="flex min-w-0 items-center gap-2 rounded-[var(--sa-radius-md)] px-1.5 py-1.5 text-left transition-colors duration-200 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-interactive-bg-active)] text-xs font-medium text-[var(--sa-alias-label-primary)]">
          {user.username[0]}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-[var(--sa-alias-label-primary)]">
            {user.username}
          </span>
          <span className="block text-xs text-[var(--sa-alias-label-caption)]">
            {user.role === 'admin' ? '管理员' : '用户'}
          </span>
        </span>
      </button>
      {open && (
        <>
          {/* 透明遮罩：点击任意处关闭下拉 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            role="menu"
            className="absolute bottom-full left-0 z-50 mb-1.5 w-44 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
          >
            <a
              role="menuitem"
              href="/capabilities"
              onClick={(e) => {
                e.preventDefault()
                setOpen(false)
                navigate({ kind: 'capabilities', capabilityKind: 'expert' })
              }}
              className={itemClass}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
                <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
                <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
                <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
              </svg>
              能力中心
            </a>
            {user.role === 'admin' && (
              <a
                role="menuitem"
                href="/admin/models"
                onClick={(e) => {
                  e.preventDefault()
                  setOpen(false)
                  navigate({ kind: 'admin', tab: 'models' })
                }}
                className={itemClass}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M2 4.5h12M2 8h12M2 11.5h7" />
                </svg>
                管理后台
              </a>
            )}
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                setOpen(false)
                logout()
              }}
              className={itemClass}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6.5 3H3.75A1.25 1.25 0 0 0 2.5 4.25v7.5A1.25 1.25 0 0 0 3.75 13H6.5M10.5 5.5 13 8l-2.5 2.5M13 8H6" />
              </svg>
              退出登录
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/** 品牌标记（黑底圆角方块 + S）见 `./BrandMark`，侧栏与消息头部共用。 */

/** 左栏主体（顶部 Logo / 中部 Sidebar / 底部 主题切换 + 用户）。 */
function SidebarFrame({ onOpenDrawer }: { onOpenDrawer?: () => void }) {
  const [dark, toggleDark] = useDarkTheme()
  return (
    <div className="flex h-full flex-col">
      {/* 顶部：Logo + 名称（compact 时带汉堡开抽屉）。
          行高与上下间距照抄 DSH `SidebarRoot.module.css` 的 `.logoRow`：
          height 60px、上下各 8px；字重照它的 `.brandName` 用 600 而非 700——
          粗体配负字距会显得挤，这里去掉 tracking-tight 并显式给行高 24px。 */}
      <div className="flex h-[60px] shrink-0 items-center gap-2 px-3">
        {onOpenDrawer && (
          <IconButton label="打开侧栏" onClick={onOpenDrawer}>
            <svg {...iconProps}>
              <path d="M2 3.5h12M2 8h12M2 12.5h12" />
            </svg>
          </IconButton>
        )}
        <BrandMark />
        <BrandWordmark
          height={32}
          className="text-[var(--sa-alias-label-primary)]"
        />
      </div>
      {/* 中部：新会话/助手/会话列表 */}
      <div className="min-h-0 flex-1">
        <Sidebar />
      </div>
      {/* 底部：主题切换 + 用户菜单（Jiuwen 式设置区） */}
      <div className="flex items-center gap-1 border-t border-[var(--sa-alias-border-l1)] px-3 py-2">
        <div className="min-w-0 flex-1">
          <UserMenu />
        </div>
        <IconButton label={dark ? '切换浅色主题' : '切换深色主题'} onClick={toggleDark}>
          {dark ? (
            <svg {...iconProps}>
              <circle cx="8" cy="8" r="3.25" />
              <path d="M8 1.5v1.4M8 13.1v1.4M1.5 8h1.4M13.1 8h1.4M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" />
            </svg>
          ) : (
            <svg {...iconProps}>
              <path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z" />
            </svg>
          )}
        </IconButton>
      </div>
      {/* 版本徽标（读 /api/health，与后端口径一致） */}
      <div className="flex shrink-0 justify-center pb-1.5">
        <VersionTag />
      </div>
    </div>
  )
}

/** 中间列顶部工具条：左侧会话标题，右侧右栏三态切换（Jiuwen 式）。 */
function ChatToolbar({
  rightState,
  onToggleRight,
  onOpenDrawer,
}: {
  rightState: RightPanelState
  onToggleRight: () => void
  onOpenDrawer?: () => void
}) {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentId = useSessionsStore((s) => s.currentId)
  const session = sessions.find((s) => s._id === currentId)
  const title = session?.title || '新对话'
  const projects = useProjectsStore((s) => s.projects)
  // 工作区取**会话绑定**的那个（session.project_id），而输入框里的选择器管的是
  // 「下一个新会话去哪儿」，两者不是一回事。未绑定（不选工作区）或绑定的工作区
  // 已删时不显示工作区 chip（会话目录即工作区），与 Sidebar 未分组区口径一致。
  const workspace = projects.find((p) => p._id === session?.project_id) ?? null

  return (
    <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-[var(--sa-alias-border-l1)] px-3">
      {onOpenDrawer && (
        <IconButton label="打开侧栏" onClick={onOpenDrawer}>
          <svg {...iconProps}>
            <path d="M2 3.5h12M2 8h12M2 12.5h12" />
          </svg>
        </IconButton>
      )}
      {/* 标题 + 工作区（同行，照 jiuwen `chat-panel-header__meta`：flex row、gap 8） */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span
          className="min-w-0 truncate text-[13.5px] font-medium text-[var(--sa-alias-label-primary)]"
          title={title}
        >
          {title}
        </span>
        {workspace && (
          <span
            className="inline-flex min-w-0 shrink-0 items-center gap-1 text-[12px] text-[var(--sa-alias-label-secondary)]"
            title={`工作区：${workspace.name}`}
          >
            <FolderIcon className="h-3.5 w-3.5 shrink-0 opacity-70" />
            <span className="max-w-[160px] truncate">{workspace.name}</span>
          </span>
        )}
      </div>
      <IconButton
        label={rightState === 'hidden' ? '展开右栏' : '收起右栏'}
        onClick={onToggleRight}
      >
        <svg {...iconProps}>
          <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" />
          <path d="M9.5 2.75v10.5" />
        </svg>
      </IconButton>
    </div>
  )
}

/**
 * 应用三列工作台外壳（Jiuwen 式无顶栏）：左栏 / Chat / 右栏 grid 全高。
 * 负责：左栏拖拽调宽、右栏三态、窄视口左栏 overlay。
 */
export default function AppShell() {
  const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT)
  const [rightWidth, setRightWidth] = useState(RIGHT_DEFAULT)
  const [rightState, setRightState] = useState<RightPanelState>('normal')
  const [dragging, setDragging] = useState(false)
  const [compact, setCompact] = useState(() => window.matchMedia(COMPACT_QUERY).matches)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const route = useRouterStore((s) => s.route)
  const navigate = useRouterStore((s) => s.navigate)
  const sessions = useSessionsStore((s) => s.sessions)
  const sessionsLoaded = useSessionsStore((s) => s.loaded)
  const currentId = useSessionsStore((s) => s.currentId)
  const isEmptySession = sessions.find((s) => s._id === currentId)?.message_count === 0
  // 进入空会话时收起右栏（Jiuwen 式欢迎页无右栏），同一会话内用户手动展开后不再干预。
  // 在渲染期比较「已收起的会话」而非用 effect：避免先画一帧展开态再收起造成闪烁。
  const [collapsedFor, setCollapsedFor] = useState<string | null>(null)
  if (currentId && isEmptySession && currentId !== collapsedFor) {
    setCollapsedFor(currentId)
    setRightState('hidden')
  }

  // URL 是会话选中态的唯一事实源（照 jiuwen）：路径变化 → 同步 store；
  // /chat/new → 草稿态（currentId 置 null，ChatPanel 据此重置聊天态）
  useEffect(() => {
    const st = useSessionsStore.getState()
    if (route.kind === 'chat-session') {
      if (st.currentId !== route.sessionId) st.setCurrent(route.sessionId)
    } else if (st.currentId !== null) {
      st.setCurrent(null)
    }
  }, [route])

  // 懒创建落库：草稿态首条消息由 chat store 建会话并把 currentId 置为新 id，
  // 这里把 URL **replace** 到 /chat/<id>——后退键不回到 /chat/new 草稿（照 jiuwen）
  useEffect(() => {
    if (route.kind === 'chat-new' && currentId) {
      navigate({ kind: 'chat-session', sessionId: currentId }, { replace: true })
    }
  }, [currentId, navigate, route])

  // 直开/刷新 /chat/<id> 但会话不在当前账号列表（已删/他人会话）：缺失提示卡
  const sessionMissing =
    route.kind === 'chat-session' &&
    sessionsLoaded &&
    !sessions.some((s) => s._id === route.sessionId)

  // 视口监听：<900px 自动切换 compact（左栏改 overlay 抽屉）
  useEffect(() => {
    const mq = window.matchMedia(COMPACT_QUERY)
    const onChange = (e: MediaQueryListEvent) => {
      setCompact(e.matches)
      setSidebarOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  /** 右栏两态切换：隐藏 ↔ 展开。 */
  const toggleRight = () => setRightState((s) => (s === 'hidden' ? 'normal' : 'hidden'))

  // grid 列模板：隐藏时右栏列收 0
  const chatCol = 'minmax(0, 1fr)'
  const rightCol = rightState === 'hidden' ? '0px' : `${rightWidth}px`
  const gridTemplateColumns = compact
    ? `${chatCol} ${rightCol}`
    : `${leftWidth}px ${chatCol} ${rightCol}`
  // 拖拽中暂停列宽过渡，其余状态动画统一走动效 token
  const gridTransition = dragging ? 'none' : 'grid-template-columns var(--sa-duration-base) var(--sa-ease-in-out)'

  return (
    <div className="relative h-dvh overflow-hidden bg-[var(--sa-alias-bg-base)] text-[var(--sa-alias-label-primary)]">
      <main className="grid h-full" style={{ gridTemplateColumns, transition: gridTransition }}>
        {/* 左栏：compact 时不渲染（改走 overlay 抽屉） */}
        {!compact && (
          <aside className="relative min-h-0 overflow-hidden border-r border-[var(--sa-alias-border-l1)] bg-[var(--sa-specific-sidebar-fill)]">
            <div className="h-full" style={{ width: leftWidth }}>
              <SidebarFrame />
            </div>
            <DragHandle
              side="left"
              width={leftWidth}
              onResize={setLeftWidth}
              onDraggingChange={setDragging}
            />
          </aside>
        )}

        {/* 中间 Chat：顶部工具条（会话标题 + 右栏切换）+ 聊天面板 */}
        <section className="relative min-h-0 min-w-0 overflow-hidden">
          <div className="flex h-full flex-col">
            <ChatToolbar
              rightState={rightState}
              onToggleRight={toggleRight}
              onOpenDrawer={compact ? () => setSidebarOpen(true) : undefined}
            />
            <div className="min-h-0 flex-1">
              {sessionMissing ? (
                <div className="flex h-full items-center justify-center">
                  <ConversationNotFound description="该对话不存在或已被删除，可能属于其他账号。" />
                </div>
              ) : (
                <ChatPanel />
              )}
            </div>
          </div>
        </section>

        {/* 右栏：三态由列模板控制宽度 */}
        <aside className="relative min-h-0 min-w-0 overflow-hidden border-l border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-bg-layer-1)]">
          <div
            className="h-full"
            style={{ width: rightWidth }}
          >
            <Rightbar />
          </div>
          {rightState === 'normal' && !compact && (
            <DragHandle
              side="right"
              width={rightWidth}
              onResize={setRightWidth}
              onDraggingChange={setDragging}
            />
          )}
        </aside>
      </main>

      {/* compact 左栏 overlay 抽屉 */}
      {compact && sidebarOpen && (
        <>
          <div
            className="absolute inset-0 z-30 bg-[var(--sa-alias-bg-mask-1)]"
            onClick={() => setSidebarOpen(false)}
          />
          <aside
            className="absolute inset-y-0 left-0 z-40 overflow-hidden border-r border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-sidebar-fill)] shadow-2xl"
            style={{ width: leftWidth }}
          >
            <SidebarFrame onOpenDrawer={() => setSidebarOpen(false)} />
          </aside>
        </>
      )}

      {/* 全局 toast */}
      <ToastHost />
    </div>
  )
}
