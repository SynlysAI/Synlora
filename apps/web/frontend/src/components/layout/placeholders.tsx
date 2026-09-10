/**
 * 三栏占位组件：Task 2 布局自查用，展示栏名与关键设计 token
 * （导航项 / 按钮）。中间 Chat 已由真实组件替换，仅剩左/右栏占位。
 */

interface SidebarPlaceholderProps {
  /** 抽屉模式下点击导航项后关闭抽屉。 */
  onNavigate?: () => void
}

/** 左栏占位：侧栏填充 + 导航项 active/hover token 演示。 */
export function SidebarPlaceholder({ onNavigate }: SidebarPlaceholderProps) {
  const items = [
    { label: '科研助手', active: true },
    { label: '数据分析助手', active: false },
    { label: '历史会话', active: false },
  ]
  return (
    <div className="flex h-full flex-col gap-1 overflow-y-auto p-2.5">
      <div
        className="px-2 pb-1 pt-1 text-xs font-medium"
        style={{ color: 'var(--sa-alias-label-caption)' }}
      >
        左栏 · Sidebar
      </div>
      <button
        type="button"
        className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-2 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-200 hover:bg-[var(--sa-alias-button-primary-hover)]"
      >
        + 新对话
      </button>
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          onClick={onNavigate}
          className={`rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-200 hover:bg-[var(--sa-specific-sidebar-nav-item-hover)] ${
            item.active
              ? 'bg-[var(--sa-specific-sidebar-nav-item-active)] text-[var(--sa-alias-label-primary)]'
              : 'text-[var(--sa-alias-label-secondary)]'
          }`}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

/** 右栏占位：三态切换与分区演示。 */
export function RightbarPlaceholder() {
  const sections = ['文件', '沙箱产物', '运行信息']
  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-3">
      <div
        className="px-1 pb-1 text-xs font-medium"
        style={{ color: 'var(--sa-alias-label-caption)' }}
      >
        右栏 · Rightbar
      </div>
      {sections.map((section) => (
        <div
          key={section}
          className="rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)] px-3 py-2.5"
        >
          <div className="text-[13px] font-medium text-[var(--sa-alias-label-primary)]">
            {section}
          </div>
          <div className="pt-1 text-xs text-[var(--sa-alias-label-caption)]">
            占位内容
          </div>
        </div>
      ))}
    </div>
  )
}
