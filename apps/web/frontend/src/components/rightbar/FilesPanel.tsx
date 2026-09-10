/**
 * 文件面板：用户工作区文件管理（右栏 Files 页）。
 *
 * 上传：拖拽 / 点击多选 → POST multipart（字段 files）→ 逐项 toast
 * （成功轻提示、失败红提示带 code）；上传与删除后刷新列表，切换会话时
 * 也重新拉取。下载走 fetch + blob（携带 Bearer）再触发本地保存。
 */
import { useEffect, useRef, useState } from 'react'
import type { FileDoc, UploadResponse } from '@/types'
import { api, getToken } from '@/api/client'
import { useChatStore } from '@/stores/chat'
import { useFilesStore } from '@/stores/files'
import { toast } from '@/stores/toasts'
import { formatBytes, formatRelativeTime } from '@/utils/format'

/** 单条文件行：名称 + 元信息 + 下载/删除操作（删除二次确认）。 */
function FileRow({ file, onDeleted }: { file: FileDoc; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  /** 下载：带 Bearer 取 blob 后本地保存（a[download] 触发）。 */
  const handleDownload = async () => {
    setBusy(true)
    try {
      const token = getToken()
      const resp = await fetch(`/api/v1/files/${file._id}/download`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast('error', `下载失败：${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  /** 删除（二次确认后执行）。 */
  const handleDelete = async () => {
    if (!confirming) {
      setConfirming(true)
      return
    }
    setBusy(true)
    try {
      await api(`/api/v1/files/${file._id}`, { method: 'DELETE' })
      toast('success', `已删除 ${file.filename}`)
      onDeleted()
    } catch (err) {
      toast('error', `删除失败：${(err as Error).message}`)
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)]">
      <svg
        width="14"
        height="14"
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
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] text-[var(--sa-alias-label-primary)]" title={file.filename}>
          {file.filename}
        </span>
        <span className="block text-xs text-[var(--sa-alias-label-caption)]">
          {formatBytes(file.size)} · {formatRelativeTime(file.created_at)}
        </span>
      </span>
      {confirming ? (
        <span className="flex shrink-0 items-center gap-1 text-xs">
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleDelete()}
            className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-state-error-primary)] px-1.5 py-0.5 text-white transition-opacity disabled:opacity-50"
          >
            确认
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="rounded-[var(--sa-radius-sm)] px-1.5 py-0.5 text-[var(--sa-alias-label-tertiary)] transition-colors hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            取消
          </button>
        </span>
      ) : (
        <span className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label={`下载 ${file.filename}`}
            title="下载"
            disabled={busy}
            onClick={() => void handleDownload()}
            className="flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-accent)] disabled:opacity-50"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M8 2.5v8M4.5 7.5 8 11l3.5-3.5M3 13.5h10" />
            </svg>
          </button>
          <button
            type="button"
            aria-label={`删除 ${file.filename}`}
            title="删除"
            disabled={busy}
            onClick={() => void handleDelete()}
            className="flex h-6 w-6 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover-danger)] hover:text-[var(--sa-alias-state-error-primary)] disabled:opacity-50"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M2.5 4.5h11M6 4.5V3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M4 4.5l.6 8a1 1 0 0 0 1 .9h4.8a1 1 0 0 0 1-.9l.6-8" />
            </svg>
          </button>
        </span>
      )}
    </div>
  )
}

/** 文件面板组件（右栏 Files 页）。 */
export default function FilesPanel() {
  const sessionId = useChatStore((s) => s.sessionId)
  const files = useFilesStore((s) => s.files)
  const loaded = useFilesStore((s) => s.loaded)
  const load = useFilesStore((s) => s.load)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // 挂载与进入会话时刷新（列表错误就地 toast，不中断面板）
  useEffect(() => {
    load().catch((err) => toast('error', `文件列表加载失败：${(err as Error).message}`))
  }, [load, sessionId])

  /** 上传多文件：multipart 字段 files，逐项 toast 反馈。 */
  const upload = async (list: File[]) => {
    if (!list.length || uploading) return
    const form = new FormData()
    list.forEach((f) => form.append('files', f))
    setUploading(true)
    try {
      const res = await api<UploadResponse>('/api/v1/files', { method: 'POST', form })
      for (const r of res.results) {
        if (r.ok) toast('success', `已上传 ${r.file?.filename ?? ''}`)
        else
          toast('error', `上传失败（${r.code ?? 'error'}）：${r.error ?? '未知错误'}`)
      }
      await load()
    } catch (err) {
      toast('error', `上传失败：${(err as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="flex h-full flex-col gap-2 p-2.5">
      {/* 上传区：拖拽 + 点击选择（多文件） */}
      <div
        role="button"
        tabIndex={0}
        aria-label="上传文件（可拖拽多个文件到此处）"
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
        className={`flex shrink-0 cursor-pointer flex-col items-center gap-1 rounded-[var(--sa-radius-md)] border border-dashed px-3 py-4 text-center transition-colors duration-[var(--sa-duration-base)] ${
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
          支持多文件；.exe/.bat 等可执行类型被拒绝
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

      {/* 文件列表 */}
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {files.length === 0 ? (
          loaded && (
            <div className="flex flex-1 items-center justify-center px-2 py-6 text-xs text-[var(--sa-alias-label-caption)]">
              暂无文件
            </div>
          )
        ) : (
          files.map((f) => (
            <FileRow key={f._id} file={f} onDeleted={() => void load()} />
          ))
        )}
      </div>
    </div>
  )
}
