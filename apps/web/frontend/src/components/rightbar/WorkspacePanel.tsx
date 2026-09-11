/**
 * 工作区面板（右栏「工作区」页）：当前工作区的目录树 + 文件上传。
 *
 * 上传入口是**树根行上的「+」图标按钮**（不再是占一整块的上传区）；拖拽能力
 * 保留——整个树区域是拖放目标（见 FileTree 的 onDropFiles）。两者最终都走
 * 项目作用域接口 POST /api/v1/projects/{pid}/files，文件落该项目 files/；
 * 上传成功后递增刷新信号，让 FileTree 丢弃各层缓存重取。
 *
 * 目录树根为当前工作区（显式选中优先，未选回落默认工作区，见 store 的
 * pickActiveProject），切换工作区（输入框空态的工作区行，见 WorkspacePicker）
 * 后树随之切换。
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
    <div className="flex h-full min-h-0 flex-col p-2.5">
      {/* 隐藏的文件选择器：由树根行的「+」按钮触发（原大块上传区已收成图标） */}
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

      {/* 目录树：根为当前项目；key 让切换项目时整棵树重挂载（展开态/缓存清零） */}
      <FileTree
        key={projectId}
        projectId={projectId}
        rootName={uploading ? `${project?.name ?? ''}（上传中…）` : (project?.name ?? '')}
        refreshToken={refresh}
        records={recordByPath}
        onFilesChanged={handleFilesChanged}
        onAddFiles={() => inputRef.current?.click()}
        onDropFiles={(files) => void upload(files)}
      />
    </div>
  )
}
