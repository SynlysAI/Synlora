/**
 * file.send 交付卡：agent 把工作区产物发给用户（对应 jiuwen send_file_to_user）。
 * 文件已被后端复制进项目 files/ 并登记（fileId），下载走既有
 * GET /api/v1/files/{fileId}/download（带鉴权 blob 下载，裸跳转会 401 存成
 * download.json）；回放时同样可下载（文件持久在工作区）。
 *
 * 图片内联预览（照抄 jiuwen FileDownloadList 的 isImage 分支）：图片文件在
 * 卡片上部直接渲染（object-contain，科研图表不裁坐标轴）；下载端点需要
 * Bearer 头，裸 <img src> 会 401，故 fetch blob → objectURL（卸载时 revoke，
 * 失败静默降级为普通卡）。Word/PPT 等不做内联（jiuwen 那套 OOXML 前端解析
 * 器成本过高，下载本地打开够用）。
 */
import { useEffect, useState } from 'react'
import { downloadFile, getToken } from '@/api/client'
import { toast } from '@/stores/toasts'

/** 可内联预览的图片扩展名（与 jiuwen IMAGE_EXTENSIONS 对齐的常用子集）。 */
const IMAGE_EXTENSIONS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'ico',
])

interface FileSendCardProps {
  /** files 集合文档 id（下载端点用）。 */
  fileId: string
  /** 展示文件名。 */
  filename: string
  /** 字节数。 */
  size: number
  /** 一句话说明（可选）。 */
  note?: string
}

/** 文件大小格式化。 */
function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

/** 取扩展名（小写，无扩展名返回空串）。 */
function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i === -1 ? '' : name.slice(i + 1).toLowerCase()
}

/** file.send 交付卡组件（对话流内联，带下载按钮；图片文件卡内直接预览）。 */
export default function FileSendCard({ fileId, filename, size, note }: FileSendCardProps) {
  const isImage = IMAGE_EXTENSIONS.has(extOf(filename))
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!isImage) return
    let url: string | null = null
    let alive = true
    const headers: Record<string, string> = {}
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    fetch(`/api/v1/files/${fileId}/download`, { headers })
      .then((resp) => (resp.ok ? resp.blob() : Promise.reject(new Error(String(resp.status)))))
      .then((blob) => {
        if (!alive || blob.size === 0) return
        url = URL.createObjectURL(blob)
        setPreviewUrl(url)
      })
      .catch(() => {
        // 预览获取失败静默降级为普通卡（下载仍可用）
      })
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [fileId, isImage])

  return (
    <a
      href={`/api/v1/files/${fileId}/download`}
      onClick={(e) => {
        // 裸跳转不带鉴权头（会存成 download.json）：走带 token 的 blob 下载
        e.preventDefault()
        void downloadFile(`/api/v1/files/${fileId}/download`, filename).catch(
          (err: Error) => toast('error', `下载失败：${err.message}`),
        )
      }}
      className="group/file my-1 flex flex-col rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] transition-colors duration-[var(--sa-duration-fast)] hover:border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      title={`下载 ${filename}`}
    >
      {previewUrl && (
        <img
          src={previewUrl}
          alt={filename}
          className="max-h-[280px] w-full rounded-t-[calc(var(--sa-radius-md)-1px)] bg-[var(--sa-specific-selector)] object-contain"
        />
      )}
      <span className="flex items-center gap-3 px-3.5 py-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] text-[var(--sa-alias-label-secondary)]" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5.5 3.5h5L15 8v8a.9.9 0 0 1-.9.9H5.5a.9.9 0 0 1-.9-.9V4.4a.9.9 0 0 1 .9-.9z" />
            <path d="M10.3 3.5V8H15" />
          </svg>
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[13.5px] font-medium text-[var(--sa-alias-label-primary)]">
            {filename}
          </span>
          <span className="truncate text-xs text-[var(--sa-alias-label-caption)]">
            {formatSize(size)}{note ? ` · ${note}` : ''}
          </span>
        </span>
        <span
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--sa-radius-sm)] text-[var(--sa-alias-label-tertiary)] transition-colors duration-[var(--sa-duration-fast)] group-hover/file:bg-[var(--sa-alias-interactive-bg-hover-accent)] group-hover/file:text-[var(--sa-alias-label-primary)]"
          aria-hidden="true"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 2.5v7.5M4.8 7.2 8 10.4l3.2-3.2M3.5 13h9" />
          </svg>
        </span>
      </span>
    </a>
  )
}
