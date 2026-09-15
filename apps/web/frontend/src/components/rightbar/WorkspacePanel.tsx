/**
 * 工作区面板（右栏「工作区」页）：当前目标的目录树 + 文件上传。
 *
 * 目标解析（与 agent 工作根一致）：
 * - 已有会话：绑定了工作区 → 该项目；无绑定 → 会话目录（sessions/{sid}），
 *   树与文件走会话作用域端点；
 * - 草稿态：WorkspacePicker 当前**显式选中**的工作区（未选 = 下个会话不使用
 *   工作区，此时树无目标，显示引导文案）。
 *
 * 上传入口是**树根行上的「+」图标按钮**（不再是占一整块的上传区）；拖拽能力
 * 保留——整个树区域是拖放目标（见 FileTree 的 onDropFiles）。两者最终都走
 * 目标作用域上传端点，文件落目标根的 files/；上传成功后递增刷新信号，让
 * FileTree 丢弃各层缓存重取。
 *
 * 文件记录：目录树条目不带 file id，而下载/删除按 id 走，故此处另拉当前目标的
 * 文件记录，按 stored_path 建「相对根路径 → 记录」映射传给 FileTree，供文件行
 * hover 时挂下载/删除入口（只有上传文件有记录；output/、tmp/ 无记录的行不可操作）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FileDoc, UploadResponse } from '@/types'
import { api } from '@/api/client'
import { useChatStore } from '@/stores/chat'
import { useProjectsStore } from '@/stores/projects'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'
import FileTree from './FileTree'

/**
 * 归一化用于匹配的路径：统一 POSIX 分隔符并去掉首尾斜杠。
 *
 * 后端两处均为 POSIX 相对根（stored_path 走 as_posix、树 path 走
 * replace("\\", "/")），此处再兜底反斜杠，避免存量记录的极端情况对不上。
 * 大小写保持敏感：不同目标可能同名，且大小写不敏感文件系统上误配风险更高。
 *
 * @param path 相对目标根的路径。
 * @returns 归一化后的路径。
 */
function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
}

/** 工作区面板组件（右栏「文件」页）。 */
export default function WorkspacePanel() {
  const sessions = useSessionsStore((s) => s.sessions)
  const currentSessionId = useSessionsStore((s) => s.currentId)
  const session = sessions.find((s) => s._id === currentSessionId) ?? null
  const projects = useProjectsStore((s) => s.projects)
  const pickedProjectId = useProjectsStore((s) => s.currentId)
  const projectsLoaded = useProjectsStore((s) => s.loaded)
  const [uploading, setUploading] = useState(false)
  /** 目录树刷新信号：上传/删除成功后递增，FileTree 收到后重取已展开的层。 */
  const [refresh, setRefresh] = useState(0)

  // 一轮回答结束（streaming 落沿）自动刷新：agent 可能刚建了会话工作区或写入
  // 产物（output/ 文件），不刷新则树停留在请求时的旧态（新会话首条消息瞬间
  // 工作区还没建，树是空的，回答完这里补一次重取就能看到 files/output/tmp）
  const streaming = useChatStore((s) => s.streaming)
  const wasStreaming = useRef(false)
  useEffect(() => {
    if (wasStreaming.current && !streaming) setRefresh((v) => v + 1)
    wasStreaming.current = streaming
  }, [streaming])
  /** 文件记录快照（连目标 key 一起存，见下方为何不裸存列表）。 */
  const [records, setRecords] = useState<{ targetKey: string; files: FileDoc[] } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // 目标解析：会话绑定项目 → 项目；无绑定会话 → 会话目录；草稿态 → 显式选中的工作区
  const boundProject = session?.project_id
    ? projects.find((p) => p._id === session.project_id) ?? null
    : null
  const target = useMemo(() => {
    if (session) {
      if (boundProject) {
        return {
          key: `project:${boundProject._id}`,
          name: boundProject.name,
          treeBase: `/api/v1/projects/${boundProject._id}/tree`,
          filesUrl: `/api/v1/projects/${boundProject._id}/files`,
        }
      }
      return {
        key: `session:${session._id}`,
        name: session.title || '会话文件',
        treeBase: `/api/v1/sessions/${session._id}/tree`,
        filesUrl: `/api/v1/sessions/${session._id}/files`,
      }
    }
    const project = projects.find((p) => p._id === pickedProjectId) ?? null
    if (project) {
      return {
        key: `project:${project._id}`,
        name: project.name,
        treeBase: `/api/v1/projects/${project._id}/tree`,
        filesUrl: `/api/v1/projects/${project._id}/files`,
      }
    }
    return null
  }, [session, boundProject, projects, pickedProjectId])

  // 记录随目标切换与刷新信号重拉；失败就地 toast 不中断面板（目录树仍可用）
  useEffect(() => {
    if (!target) return
    let cancelled = false
    api<FileDoc[]>(target.filesUrl)
      .then((list) => {
        if (!cancelled) setRecords({ targetKey: target.key, files: list })
      })
      .catch((err: Error) => toast('error', `文件列表加载失败：${err.message}`))
    return () => {
      cancelled = true
    }
  }, [target, refresh])

  /**
   * stored_path（归一化）→ 记录，供 FileTree 文件行按 path 对上下载/删除。
   *
   * 快照的 targetKey 与当前目标不一致（切换目标后新记录尚未返回）时返回空映射：
   * 否则会拿旧目标的记录去配新目标的树，把同名文件配上错误的 file id。
   */
  const recordByPath = useMemo(() => {
    const map = new Map<string, FileDoc>()
    if (records && records.targetKey === target?.key) {
      for (const doc of records.files) map.set(normalizePath(doc.stored_path), doc)
    }
    return map
  }, [records, target])

  /** 文件删除成功后：目录树丢缓存重取，同时重拉记录映射。 */
  const handleFilesChanged = () => setRefresh((v) => v + 1)

  /** 上传多文件到当前目标：multipart 字段 files，逐项 toast 反馈。 */
  const upload = async (list: File[]) => {
    if (!list.length || uploading || !target) return
    const form = new FormData()
    list.forEach((f) => form.append('files', f))
    setUploading(true)
    try {
      const res = await api<UploadResponse>(target.filesUrl, {
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

  if (!target) {
    return (
      <div className="flex h-full items-center justify-center p-3 text-xs text-[var(--sa-alias-label-caption)]">
        {projectsLoaded
          ? '当前会话未使用工作区（文件保存在会话目录）；草稿态可先在输入框下方选择工作区'
          : '加载中…'}
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

      {/* 目录树：根为当前目标；key 让切换目标时整棵树重挂载（展开态/缓存清零） */}
      <FileTree
        key={target.key}
        treeBase={target.treeBase}
        rootName={uploading ? `${target.name}（上传中…）` : target.name}
        refreshToken={refresh}
        records={recordByPath}
        onFilesChanged={handleFilesChanged}
        onAddFiles={() => inputRef.current?.click()}
        onDropFiles={(files) => void upload(files)}
      />
    </div>
  )
}
