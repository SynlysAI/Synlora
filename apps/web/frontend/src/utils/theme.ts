/**
 * 暗色主题共享 hook：AppShell 顶栏与管理页布局共用。
 *
 * 偏好持久化在 localStorage（sa.theme.dark），切换时同步
 * body[data-sa-dark-theme] 触发全局主题变量替换。
 */
import { useEffect, useState } from 'react'

/** 暗色主题 localStorage 持久化键。 */
export const DARK_STORAGE_KEY = 'sa.theme.dark'

/**
 * 暗色主题状态 hook。
 *
 * @returns [是否暗色, 切换函数]。
 */
export function useDarkTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(
    () => localStorage.getItem(DARK_STORAGE_KEY) === '1',
  )

  useEffect(() => {
    if (dark) document.body.dataset.saDarkTheme = ''
    else delete document.body.dataset.saDarkTheme
    localStorage.setItem(DARK_STORAGE_KEY, dark ? '1' : '0')
  }, [dark])

  return [dark, () => setDark((v) => !v)]
}
