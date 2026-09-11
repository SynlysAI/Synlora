/**
 * 右栏工作区：文件（当前项目上传 + 目录树）/ 运行信息 两页标签切换。
 */
import { useState } from 'react'
import RunInfoPanel from './RunInfoPanel'
import WorkspacePanel from './WorkspacePanel'

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
      {/* 标签栏：底边框 + 选中态下划线 */}
      <div
        role="tablist"
        aria-label="右栏面板"
        className="flex shrink-0 border-b border-[var(--sa-alias-border-l1)] px-2.5"
      >
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`relative px-2.5 pb-2 pt-2.5 text-[13px] transition-colors duration-[var(--sa-duration-base)] ${
              tab === key
                ? 'font-medium text-[var(--sa-alias-label-primary)]'
                : 'text-[var(--sa-alias-label-tertiary)] hover:text-[var(--sa-alias-label-secondary)]'
            }`}
          >
            {label}
            {tab === key && (
              <span
                aria-hidden="true"
                className="absolute inset-x-1.5 bottom-[-1px] h-[2px] rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-link)]"
              />
            )}
          </button>
        ))}
      </div>

      {/* 面板区 */}
      <div className="min-h-0 flex-1">
        {tab === 'files' ? <WorkspacePanel /> : <RunInfoPanel />}
      </div>
    </div>
  )
}
