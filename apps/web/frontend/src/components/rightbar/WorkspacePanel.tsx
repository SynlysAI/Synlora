/**
 * 工作区面板（右栏「文件」页）：当前项目的上传区 + 目录树。
 *
 * 上传区沿用原 FilesPanel 的拖拽/点选交互，但改投项目作用域接口
 * （POST /api/v1/projects/{pid}/files，文件落该项目 files/）；上传成功后
 * 递增刷新信号，让 FileTree 丢弃各层缓存重取。下方目录树根为当前项目，
 * 切换项目（输入框下的 ProjectPicker）后树随之切换。
 */
import { useRef, useState } from 'react'
import type { UploadResponse } from '@/types'
import { api } from '@/api/client'
import { useProjectsStore } from '@/stores/projects'
import { toast } from '@/stores/toasts'
import FileTree from './FileTree'

/** 工作区面板组件（右栏「文件」页）。 */
export default function WorkspacePanel() {
  const projectId = useProjectsStore((s) => s.currentId)
  const project = useProjectsStore((s) => s.projects.find((p) => p._id === s.currentId))
  const projectsLoaded = useProjectsStore((s) => s.loaded)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  /** 目录树刷新信号：上传成功后递增，FileTree 收到后重取已展开的层。 */
  const [refresh, setRefresh] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  /** 上传多文件到当前项目：multipart 字段 files，逐项 toast 反馈。 */
  const upload = async (list: File[]) => {
    if (!list.length || uploading || !projectId) return
    const form = new FormData()
    list.forEach((f) => form.append('files', f))
    setUploading(true)
    try {
      const res = await api<UploadResponse>(`/api/v1/projects/${projectId}/files`, {
        method: 'POST',
        form,
      })
      for (const r of res.results) {
        if (r.ok) toast('success', `已上传 ${r.file?.filename ?? ''}`)
        else toast('error', `上传失败（${r.code ?? 'error'}）：${r.error ?? '未知错误'}`)
      }
      setRefresh((v) => v + 1)
    } catch (err) {
      toast('error', `上传失败：${(err as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center p-3 text-xs text-[var(--sa-alias-label-caption)]">
        {projectsLoaded ? '暂无项目' : '加载中…'}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2.5">
      {/* 上传区：拖拽 + 点击选择（多文件，落入当前项目 files/） */}
      <div
        role="button"
        tabIndex={0}
        aria-label="上传文件到当前项目（可拖拽多个文件到此处）"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragActive(false)
          void upload(Array.from(e.dataTransfer.files))
        }}
        className={`flex shrink-0 cursor-pointer flex-col items-center gap-1 rounded-[var(--sa-radius-md)] border border-dashed px-3 py-3 text-center transition-colors duration-[var(--sa-duration-base)] ${
          dragActive
            ? 'border-[var(--sa-alias-state-business-primary)] bg-[var(--sa-alias-state-business-tertiary)]'
            : 'border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]'
        }`}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-[var(--sa-alias-label-tertiary)]"
          aria-hidden="true"
        >
          <path d="M8 10.5V2.5M4.8 5.7 8 2.5l3.2 3.2M3 13.5h10" />
        </svg>
        <span className="text-[13px] text-[var(--sa-alias-label-secondary)]">
          {uploading ? '上传中…' : '拖拽文件到此处，或点击上传'}
        </span>
        <span className="text-xs text-[var(--sa-alias-label-caption)]">
          上传到「{project?.name ?? ''}」的 files/ 目录
        </span>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            void upload(Array.from(e.target.files ?? []))
            // 重置 value 使同名文件可重复选择
            e.target.value = ''
          }}
        />
      </div>

      {/* 目录树：根为当前项目；key 让切换项目时整棵树重挂载（展开态/缓存清零） */}
      <FileTree
        key={projectId}
        projectId={projectId}
        rootName={project?.name ?? ''}
        refreshToken={refresh}
      />
    </div>
  )
}
