/**
 * file.send 交付卡：agent 把工作区产物发给用户（对应 jiuwen send_file_to_user）。
 * 文件已被后端复制进项目 files/ 并登记（fileId），下载走既有
 * GET /api/v1/files/{fileId}/download；回放时同样可下载（文件持久在工作区）。
 */
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

/** file.send 交付卡组件（对话流内联，带下载按钮）。 */
export default function FileSendCard({ fileId, filename, size, note }: FileSendCardProps) {
  return (
    <a
      href={`/api/v1/files/${fileId}/download`}
      download
      className="group/file my-1 flex items-center gap-3 rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3.5 py-2.5 transition-colors duration-[var(--sa-duration-fast)] hover:border-[var(--sa-alias-border-l3)] hover:bg-[var(--sa-alias-interactive-bg-hover)]"
      title={`下载 ${filename}`}
    >
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
    </a>
  )
}
