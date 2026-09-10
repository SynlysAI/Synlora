/**
 * 管理页共享表单常量与错误文案归一（纯工具，不含组件）。
 *
 * 与 shared.tsx 的 UI 组件分离存放，保持各自文件导出类型单一
 * （组件文件只导出组件，工具文件只导出常量/函数）。
 */
import { ApiError } from '@/api/client'

/** 表单输入框样式（与登录页一致的基础款）。 */
export const inputClass =
  'w-full rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] ' +
  'bg-[var(--sa-specific-input-major)] px-3 py-2 text-[14px] text-[var(--sa-alias-label-primary)] ' +
  'placeholder:text-[var(--sa-alias-label-caption)] outline-none transition-colors ' +
  'duration-[var(--sa-duration-base)] focus:border-[var(--sa-alias-border-l4)]'

/** 表单字段标签容器样式。 */
export const labelClass = 'flex flex-col gap-1.5 text-[13px] text-[var(--sa-alias-label-secondary)]'

/** 次级按钮（模态取消等）。 */
export const secondaryButtonClass =
  'rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-3 py-1.5 text-[13px] ' +
  'text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-base)] ' +
  'hover:bg-[var(--sa-alias-interactive-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50'

/** 主按钮（表单提交等）。 */
export const primaryButtonClass =
  'rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-button-primary-fill)] px-3 py-1.5 text-[13px] ' +
  'font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors ' +
  'duration-[var(--sa-duration-base)] hover:bg-[var(--sa-alias-button-primary-hover)] ' +
  'disabled:cursor-not-allowed disabled:opacity-50'

/**
 * 把 API 错误归一为用户可读文案。
 *
 * @param err 任意捕获的异常。
 * @param fallback 非 ApiError 时的兜底文案。
 * @returns detail 字符串原样；FastAPI 校验数组逐项拼接 loc+msg。
 */
export function errorText(err: unknown, fallback = '操作失败，请稍后重试'): string {
  if (err instanceof ApiError) {
    if (Array.isArray(err.detail)) {
      return err.detail
        .map((d) => {
          if (d && typeof d === 'object' && 'msg' in d) {
            const item = d as { loc?: unknown; msg: unknown }
            const loc = Array.isArray(item.loc)
              ? item.loc.filter((v) => v !== 'body').join('.')
              : ''
            return loc ? `${loc}: ${String(item.msg)}` : String(item.msg)
          }
          return String(d)
        })
        .join('；')
    }
    return err.message || fallback
  }
  return fallback
}
