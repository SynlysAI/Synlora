/**
 * 技能目录文件预览：左侧文件树，右侧显示文本或图片。
 *
 * 文件内容通过带 Authorization 的 API 请求读取，避免裸 img/a 标签丢失登录令牌。
 */
import { useEffect, useMemo, useState } from 'react'
import { api, fetchBlobUrl } from '@/api/client'
import type { SkillFile } from '@/types'

function isImage(path: string): boolean {
  return /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(path)
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function FileTree({
  nodes,
  selected,
  onSelect,
}: {
  nodes: SkillFile[]
  selected: string
  onSelect: (node: SkillFile) => void
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {nodes.map((node) => (
        <div key={node.path}>
          <button
            type="button"
            disabled={node.kind === 'directory'}
            onClick={() => onSelect(node)}
            className={`flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2 py-1.5 text-left text-xs ${
              selected === node.path
                ? 'bg-[var(--sa-alias-interactive-bg-hover)] text-[var(--sa-alias-label-primary)]'
                : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-alias-interactive-bg-hover)]'
            } ${node.kind === 'directory' ? 'cursor-default font-medium' : ''}`}
          >
            <span aria-hidden="true">{node.kind === 'directory' ? '▾' : isImage(node.path) ? '▧' : '•'}</span>
            <span className="min-w-0 flex-1 truncate">{node.path.split('/').pop()}</span>
            {node.kind === 'file' && <span className="text-[10px] text-[var(--sa-alias-label-caption)]">{formatSize(node.size)}</span>}
          </button>
          {node.children && node.children.length > 0 && (
            <div className="ml-3 border-l border-[var(--sa-alias-border-l2)] pl-1">
              <FileTree nodes={node.children} selected={selected} onSelect={onSelect} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** 技能文件树与文件内容预览。 */
export default function SkillFilePreview({
  skillName,
  files,
  apiPrefix = '/api/v1/me/skills',
}: {
  skillName: string
  files: SkillFile[]
  /** 文件读取 API 前缀；管理员技能使用 /api/v1/skills。 */
  apiPrefix?: string
}) {
  const firstFile = useMemo(() => {
    const find = (nodes: SkillFile[]): SkillFile | undefined => {
      for (const node of nodes) {
        if (node.kind === 'file' && node.previewable !== false) return node
        if (node.children) {
          const found = find(node.children)
          if (found) return found
        }
      }
      return undefined
    }
    return find(files)
  }, [files])
  const [selected, setSelected] = useState(firstFile?.path ?? '')
  const [content, setContent] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (firstFile && !selected) setSelected(firstFile.path)
  }, [firstFile, selected])

  useEffect(() => {
    if (!selected) return
    let active = true
    let objectUrl = ''
    setLoading(true)
    setError('')
    setContent('')
    setImageUrl('')
    const load = async () => {
      try {
        if (isImage(selected)) {
          objectUrl = await fetchBlobUrl(
            `${apiPrefix}/${encodeURIComponent(skillName)}/raw?path=${encodeURIComponent(selected)}`,
          )
          if (active) setImageUrl(objectUrl)
        } else {
          const result = await api<{ content: string }>(
            `${apiPrefix}/${encodeURIComponent(skillName)}/file?path=${encodeURIComponent(selected)}`,
          )
          if (active) setContent(result.content)
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : '文件读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [apiPrefix, selected, skillName])

  return (
    <div className="grid min-h-[360px] overflow-hidden rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] md:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="border-b border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-2 md:border-b-0 md:border-r">
        {files.length > 0 ? <FileTree nodes={files} selected={selected} onSelect={(node) => setSelected(node.path)} /> : (
          <p className="p-2 text-xs text-[var(--sa-alias-label-caption)]">没有可预览文件</p>
        )}
      </aside>
      <div className="min-w-0 bg-[var(--sa-specific-code-block)] p-4">
        <div className="mb-3 text-xs text-[var(--sa-alias-label-caption)]">{selected || '选择文件'}</div>
        {loading && <p className="text-sm text-[var(--sa-alias-label-caption)]">加载中…</p>}
        {error && <p className="text-sm text-[var(--sa-alias-state-error-primary)]">{error}</p>}
        {!loading && !error && imageUrl && (
          <div className="flex min-h-[280px] items-center justify-center overflow-auto rounded border border-[var(--sa-alias-border-l2)] bg-white p-4">
            <img src={imageUrl} alt={selected} className="max-h-[520px] max-w-full object-contain" />
          </div>
        )}
        {!loading && !error && !imageUrl && selected && (
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[var(--sa-alias-label-primary)]">{content || '（空文件）'}</pre>
        )}
      </div>
    </div>
  )
}
