/**
 * 三栏占位组件：Task 2 布局自查用，展示栏名与关键设计 token
 * （导航项 / 气泡 / 代码块 / 按钮 / 输入框）。后续任务逐个替换为真实组件。
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

/** 中间列占位：用户/助手气泡 + 代码块 token 演示。 */
export function ChatPlaceholder() {
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto px-6 py-8">
      <div
        className="pb-2 text-center text-xs font-medium"
        style={{ color: 'var(--sa-alias-label-caption)' }}
      >
        中间 · Chat
      </div>
      {/* 用户气泡：黑白极简主色 */}
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-[var(--sa-radius-lg)] bg-[var(--sa-alias-button-primary-fill)] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--sa-alias-label-primary-foreground)]">
          用 Python 写个打招呼函数
        </div>
      </div>
      {/* 助手气泡：蓝调浅底 */}
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-[var(--sa-radius-lg)] bg-[var(--sa-specific-bubble)] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--sa-alias-label-primary)]">
          好的，这是一个简单的示例：
          {/* 代码块：banner + 代码字体 token */}
          <div className="mt-2 overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l1)]">
            <div className="bg-[var(--sa-alias-markdown-code-block-banner)] px-3 py-1.5 text-xs text-[var(--sa-alias-label-caption)]">
              python
            </div>
            <pre
              className="overflow-x-auto bg-[var(--sa-specific-code-block)] px-3 py-2.5 text-[13px] leading-relaxed text-[var(--sa-alias-label-primary)]"
              style={{ fontFamily: 'var(--sa-font-code)' }}
            >{`def hello(name: str) -> str:\n    return f"Hello, {name}!"`}</pre>
          </div>
        </div>
      </div>
      {/* 输入框占位：input token + 链接色演示 */}
      <div className="mt-auto rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-input-major)] px-4 py-3 text-[13px]" style={{ color: 'var(--sa-alias-label-caption)' }}>
        输入消息…（Composer 占位，链接色演示：
        <span className="text-[var(--sa-alias-link)]">python.run</span>）
      </div>
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
