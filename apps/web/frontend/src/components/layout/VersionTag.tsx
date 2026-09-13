/**
 * 版本徽标：从公开的 GET /api/health 读取后端版本（app/version.py 为全项目
 * 唯一口径）展示为小字，侧栏底部与登录页共用；读取失败静默不展示。
 */
import { useEffect, useState } from 'react'

/** 版本信息视图模型。 */
interface VersionInfo {
  version: string
  label: string
}

/** 版本徽标组件。 */
export function VersionTag() {
  const [info, setInfo] = useState<VersionInfo | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetch('/api/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { version?: string; version_label?: string } | null) => {
        if (!cancelled && d?.version) {
          setInfo({ version: d.version, label: d.version_label ?? '' })
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  if (!info) return null
  return (
    <span className="text-[10px] leading-4 text-[var(--sa-alias-label-caption)]">
      v{info.version}
      {info.label ? ` · ${info.label}` : ''}
    </span>
  )
}
