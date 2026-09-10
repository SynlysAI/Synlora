/**
 * 轻量 toast store：右栏文件操作、左栏会话操作等全局反馈共用。
 *
 * 成功/提示类短暂停留（2.5s），错误类停留更久（6s）给用户阅读 code 与
 * 错误信息的时间；均可点击提前关闭。
 */
import { create } from 'zustand'

/** toast 类型：success 成功 / error 错误（红） / info 中性提示。 */
export type ToastKind = 'success' | 'error' | 'info'

/** 单条 toast 视图模型。 */
export interface Toast {
  id: number
  kind: ToastKind
  text: string
}

/** 各类型自动消失时长（ms）。 */
const AUTO_DISMISS_MS: Record<ToastKind, number> = {
  success: 2500,
  info: 2500,
  error: 6000,
}

/** 自增 id（模块级，避免进 state）。 */
let nextId = 1

interface ToastsState {
  /** 当前展示中的 toast（栈底在上）。 */
  toasts: Toast[]
  /** 推入一条 toast（自动到期移除）。 */
  push: (kind: ToastKind, text: string) => void
  /** 手动移除一条。 */
  dismiss: (id: number) => void
}

export const useToastsStore = create<ToastsState>((set) => ({
  toasts: [],

  push: (kind, text) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts.slice(-4), { id, kind, text }] }))
    window.setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, AUTO_DISMISS_MS[kind])
  },

  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

/** 便捷推入函数（非组件场景直接用）。 */
export function toast(kind: ToastKind, text: string): void {
  useToastsStore.getState().push(kind, text)
}
