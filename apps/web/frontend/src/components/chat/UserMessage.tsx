/**
 * 用户消息：右对齐轻量气泡（max-w 收窄 + 不对称圆角贴近对话流）。
 * 不配头像——对话里只有助手一侧有身份标识，用户侧靠气泡右对齐即可辨认。
 * 附件（随消息一起发送的文件）在正文下方以可下载的文件行展示。
 */
import type { MessageAttachment } from '@/types'
import { downloadFile } from '@/api/client'
import { toast } from '@/stores/toasts'

/** 文件大小格式化（与 FileSendCard 同口径）。 */
function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

interface UserMessageProps {
  /** 消息文本。 */
  text: string
  /** 随消息发送的附件（可选）。 */
  attachments?: MessageAttachment[]
  /** 系统唤醒消息标记（true = 居中提示条，不是用户气泡）。 */
  systemWake?: boolean
}

/** 用户消息组件（轻量气泡 + 附件行；系统唤醒消息渲染为居中提示条）。 */
export default function UserMessage({ text, attachments, systemWake }: UserMessageProps) {
  // 唤醒消息的正文是给模型的指令，对用户无意义：只回固定文案，
  // 完整结论由紧随其后的助手回答承载
  if (systemWake) {
    return (
      <div className="flex justify-center">
        {/* 底色用 interactive-bg-hover（明暗都是 alpha 叠加，随宿主底色自适应）：
            bg-layer-1 在浅色下等于页面底色 bg-base（都是 bluish-00 纯白），白底白条
            会让药丸形状完全消失；边框照 Jiuwen 系统消息条的 bg + border 组合 */}
        <div className="flex max-w-[85%] items-center gap-2 rounded-[var(--sa-radius-full)] border border-[var(--sa-alias-border-l1)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-1.5 text-[13px] text-[var(--sa-alias-label-tertiary)]">
          <svg
            width="13"
            height="13"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="shrink-0 opacity-70"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="7.2" />
            <path d="M10 6.4v4.2l2.6 1.6" />
          </svg>
          <span className="min-w-0 truncate">后台任务已完成，正在汇总结果…</span>
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-end justify-end">
      <div className="max-w-[75%] rounded-[var(--sa-radius-lg)] rounded-br-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-active)] px-3.5 py-2 text-[15px] leading-relaxed text-[var(--sa-alias-label-primary)]">
        {attachments && attachments.length > 0 && (
          <div className="flex flex-col gap-1 pb-1.5">
            {attachments.map((a) => (
              <a
                key={a.file_id}
                href={`/api/v1/files/${a.file_id}/download`}
                onClick={(e) => {
                  // 裸跳转不带鉴权头（会存成 download.json）：走带 token 的 blob 下载
                  e.preventDefault()
                  void downloadFile(`/api/v1/files/${a.file_id}/download`, a.filename).catch(
                    (err: Error) => toast('error', `下载失败：${err.message}`),
                  )
                }}
                title={`下载 ${a.filename}`}
                className="flex items-center gap-2 rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-bg-layer-1)] px-2 py-1.5 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0 opacity-70"
                  aria-hidden="true"
                >
                  <path d="M5.5 3.5h5L15 8v8a.9.9 0 0 1-.9.9H5.5a.9.9 0 0 1-.9-.9V4.4a.9.9 0 0 1 .9-.9z" />
                  <path d="M10.3 3.5V8H15" />
                </svg>
                <span className="min-w-0 flex-1 truncate">{a.filename}</span>
                {a.size > 0 && (
                  <span className="shrink-0 text-xs text-[var(--sa-alias-label-caption)]">
                    {formatSize(a.size)}
                  </span>
                )}
              </a>
            ))}
          </div>
        )}
        {text && <div className="whitespace-pre-wrap break-words">{text}</div>}
      </div>
    </div>
  )
}
