/**
 * 版本徽标：从公开的 GET /api/health 读取后端版本（app/version.py 为全项目
 * 唯一口径）展示为小字，平台名右侧与登录页共用；读取失败静默不展示。
 */
import { useEffect, useState } from 'react'

/** 读取公开健康检查接口返回的版本号。 */
function useAppVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetch('/api/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { version?: string } | null) => {
        if (!cancelled && d?.version) setVersion(d.version)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  return version
}

/** 版本徽标组件。 */
export function VersionTag() {
  const version = useAppVersion()
  if (!version) return null
  return (
    <span className="text-[10px] leading-4 text-[var(--sa-alias-label-caption)]">
      v{version}
    </span>
  )
}
