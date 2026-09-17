/**
 * 通用格式化工具：相对时间与文件大小的人性化展示。
 */

/** 分钟/小时/天对应的秒数。 */
const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * Unix 秒时间戳 → 中文相对时间（"刚刚" / "3 分钟前" / "2 小时前" / "5 天前"），
 * 超过 7 天回退为 "MM-DD"（跨年补年份）。
 *
 * @param ts Unix 秒时间戳（后端 created_at/updated_at 约定）。
 * @returns 人性化相对时间文本。
 */
export function formatRelativeTime(ts: number): string {
  if (!Number.isFinite(ts)) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < MINUTE) return '刚刚'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)} 天前`
  const d = new Date(ts * 1000)
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === new Date().getFullYear() ? md : `${d.getFullYear()}-${md}`
}

/**
 * 秒数 → 至多两个相邻单位的时长（"45s" / "3m05s" / "1h02m"）。
 *
 * 照抄 DSH JobListAction 的 formatDuration：后台任务超过 1 小时已属异常，
 * 故小时就是最大单位（不再长出天/月这类没有产出方会到达的词汇）。
 *
 * @param seconds 时长（秒，负数按 0 处理）。
 * @returns 时长文本。
 */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m${String(s).padStart(2, '0')}s`
  return `${s}s`
}

/** 各数量级对应的字节数与单位。 */
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/**
 * 字节数 → 人性化大小（B 整数显示，KB 起保留一位小数）。
 *
 * @param bytes 字节数。
 * @returns 如 "512 B" / "13.5 KB" / "2.4 MB"；非法输入返回 ""。
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  let value = bytes
  let i = 0
  while (value >= 1024 && i < UNITS.length - 1) {
    value /= 1024
    i += 1
  }
  return i === 0 ? `${value} B` : `${value.toFixed(1)} ${UNITS[i]}`
}
