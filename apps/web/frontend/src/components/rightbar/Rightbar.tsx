/**
 * 右栏工作区：文件 / 运行信息 两页标签切换。
 */
import { useState } from 'react'
import FilesPanel from './FilesPanel'
import RunInfoPanel from './RunInfoPanel'

/** 标签页定义。 */
const TABS = [
  { key: 'files', label: '文件' },
  { key: 'run', label: '运行信息' },
] as const

type TabKey = (typeof TABS)[number]['key']

/** 右栏组件（AppShell 右侧列）。 */
export default function Rightbar() {
  const [tab, setTab] = useState<TabKey>('files')

  return (
    <div className="flex h-full flex-col">
      {/* 标签栏 */}
      <div
        role="tablist"
        aria-label="右栏面板"
        className="flex shrink-0 gap-1 p-2.5 pb-2"
      >
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`flex-1 rounded-[var(--sa-radius-sm)] px-2 py-1 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
              tab === key
                ? 'bg-[var(--sa-alias-button-ghost-active-fill)] font-medium text-[var(--sa-alias-label-primary)]'
                : 'text-[var(--sa-alias-label-tertiary)] hover:bg-[var(--sa-alias-interactive-bg-hover)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 面板区 */}
      <div className="min-h-0 flex-1">
        {tab === 'files' ? <FilesPanel /> : <RunInfoPanel />}
      </div>
    </div>
  )
}
