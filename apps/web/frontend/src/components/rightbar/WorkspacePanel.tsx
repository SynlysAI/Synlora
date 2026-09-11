/**
 * 工作区面板（右栏「文件」页）：当前工作区的上传区 + 目录树。
 *
 * 上传区沿用原 FilesPanel 的拖拽/点选交互，但改投项目作用域接口
 * （POST /api/v1/projects/{pid}/files，文件落该项目 files/）；上传成功后
 * 递增刷新信号，让 FileTree 丢弃各层缓存重取。下方目录树根为当前工作区
 * （显式选中优先，未选回落默认工作区，见 store 的 pickActiveProject），
 * 切换工作区（输入框空态的工作区行，见 WorkspacePicker）后树随之切换。
 *
 * 文件记录：目录树条目不带 file id，而下载/删除按 id 走，故此处另拉当前项目的
 * 文件记录（GET /api/v1/projects/{pid}/files），按 stored_path 建「相对项目根
 * 路径 → 记录」映射传给 FileTree，供文件行 hover 时挂下载/删除入口（只有上传
 * 文件有记录；output/、tmp/ 无记录的行不可操作）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FileDoc, UploadResponse } from '@/types'
import { api } from '@/api/client'
import { pickActiveProject, useProjectsStore } from '@/stores/projects'
import { toast } from '@/stores/toasts'
import FileTree from './FileTree'

/**
 * 归一化用于匹配的路径：统一 POSIX 分隔符并去掉首尾斜杠。
 *
 * 后端两处均为 POSIX 相对项目根（stored_path 走 as_posix、树 path 走
 * replace("\\", "/")），此处再兜底反斜杠，避免存量记录的极端情况对不上。
 * 大小写保持敏感：不同项目可能同名，且大小写不敏感文件系统上误配风险更高。
 *
 * @param path 相对项目根的路径。
 * @returns 归一化后的路径。
 */
function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
}

/** 工作区面板组件（右栏「文件」页）。 */
export default function WorkspacePanel() {
  // 显式选中的工作区优先，未选回落默认工作区（pickActiveProject）；都无 → null
  const project = useProjectsStore(pickActiveProject)
  const projectId = project?._id ?? null
  const projectsLoaded = useProjectsStore((s) => s.loaded)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  /** 目录树刷新信号：上传/删除成功后递增，FileTree 收到后重取已展开的层。 */
  const [refresh, setRefresh] = useState(0)
  /** 文件记录快照（连项目 id 一起存，见下方为何不裸存列表）。 */
  const [records, setRecords] = useState<{ projectId: string; files: FileDoc[] } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // 记录随项目切换与刷新信号重拉；失败就地 toast 不中断面板（目录树仍可用）
  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    api<FileDoc[]>(`/api/v1/projects/${projectId}/files`)
      .then((list) => {
        if (!cancelled) setRecords({ projectId, files: list })
      })
      .catch((err: Error) => toast('error', `文件列表加载失败：${err.message}`))
    return () => {
      cancelled = true
    }
  }, [projectId, refresh])

  /**
   * stored_path（归一化）→ 记录，供 FileTree 文件行按 path 对上下载/删除。
   *
   * 快照的 projectId 与当前项目不一致（切换项目后新记录尚未返回）时返回空映射：
   * 否则会拿旧项目的记录去配新项目的树，把同名文件配上错误的 file id。
   */
  const recordByPath = useMemo(() => {
    const map = new Map<string, FileDoc>()
    if (records && records.projectId === projectId) {
      for (const doc of records.files) map.set(normalizePath(doc.stored_path), doc)
    }
    return map
  }, [records, projectId])

  /** 文件删除成功后：目录树丢缓存重取，同时重拉记录映射。 */
  const handleFilesChanged = () => setRefresh((v) => v + 1)

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
        {projectsLoaded ? '暂无工作区' : '加载中…'}
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
        records={recordByPath}
        onFilesChanged={handleFilesChanged}
      />
    </div>
  )
}
