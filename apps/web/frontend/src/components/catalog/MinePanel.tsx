/**
 * 「我的」面板：自建 + 已安装 + 内置只读（技能/专家/插件）。
 *
 * 子页签（全部/已启用/已停用/内置）与过滤逻辑照旧（选中值由列表页壳层注入，
 * 见 SubTab，以便进详情返回后保持）；渲染改为卡片网格
 * （复用 CapabilityCard，与市场页视觉统一）。
 *
 * 内置条目对普通用户只读（无启停/编辑/删除入口，标「自动可用」），
 * 与后端 `default_enabled` 口径一致。
 */
import { useEffect, useMemo, useState } from 'react'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'
import CapabilityCard, { CardBadge } from './CapabilityCard'
import Switch from './Switch'
import { ExpertModal, SkillModal } from './CapabilityModals'

/** 「我的」子页签（state 由列表页壳层持有，进详情返回后不丢）。 */
export type SubTab = 'all' | 'enabled' | 'disabled' | 'builtin'

const SUB_TABS: Array<{ key: SubTab; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'enabled', label: '已启用' },
  { key: 'disabled', label: '已停用' },
  { key: 'builtin', label: '内置' },
]

interface MinePanelProps {
  /** 当前类型（左导航选中项）。 */
  capabilityKind: 'expert' | 'skill' | 'plugin'
  /** 类型展示名（专家/技能/插件）：空态文案用，由列表页传入以免两处各写一份映射。 */
  kindLabel: string
  /** 搜索词（由列表页统一持有，与市场页各自独立）。 */
  query: string
  /** 子页签（由列表页壳层持有：本面板会随「进详情/返回」卸载重建，放这里会被重置）。 */
  sub: SubTab
  onSubChange: (sub: SubTab) => void
  /** 点卡片进详情。 */
  onOpen: (item: MyCapability) => void
  /** 卡片上的启用/停用快按钮。 */
  onToggle: (item: MyCapability) => void
  /** 快按钮进行中的条目键（`kind:id`）。 */
  busyKey: string | null
}

/** 「我的」面板。 */
export default function MinePanel({
  capabilityKind, kindLabel, query, sub, onSubChange, onOpen, onToggle, busyKey,
}: MinePanelProps) {
  const items = useMyCapabilitiesStore((s) => s.items)
  const builtinItems = useMyCapabilitiesStore((s) => s.builtinItems)
  const loaded = useMyCapabilitiesStore((s) => s.loaded)
  const loadMine = useMyCapabilitiesStore((s) => s.loadMine)
  const [editingSkill, setEditingSkill] = useState<MyCapability | null | undefined>(undefined)
  const [editingExpert, setEditingExpert] = useState<MyCapability | null | undefined>(undefined)

  useEffect(() => {
    loadMine().catch((err) => toast('error', `加载我的能力失败：${errorText(err)}`))
  }, [loadMine])

  // 内置页签走只读数据源，其余页签过滤自己的列表；再按当前类型与搜索词收窄
  const rows = useMemo(() => {
    const source = sub === 'builtin'
      ? builtinItems
      : items.filter((item) => {
          if (sub === 'enabled') return item.enabled
          if (sub === 'disabled') return !item.enabled
          return true
        })
    const byKind = source.filter((item) => item.kind === capabilityKind)
    const q = query.trim().toLowerCase()
    if (!q) return byKind
    return byKind.filter(
      (item) => item.name.toLowerCase().includes(q) || item.description.toLowerCase().includes(q),
    )
  }, [sub, items, builtinItems, capabilityKind, query])

  return (
    <div className="flex flex-col gap-3">
      {/* 子页签 + 自建入口 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {SUB_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => onSubChange(t.key)}
              className={
                sub === t.key
                  ? 'rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-sm)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        {/* 新建入口只保留当前类型对应的那一个（左导航已按类型分隔；
            插件不支持用户自建，故插件页不出现新建入口） */}
        <div className="flex items-center gap-3">
          {capabilityKind === 'skill' && (
            <button
              type="button"
              onClick={() => setEditingSkill(null)}
              className="text-sm text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
            >
              + 新建技能
            </button>
          )}
          {capabilityKind === 'expert' && (
            <button
              type="button"
              onClick={() => setEditingExpert(null)}
              className="text-sm text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
            >
              + 新建专家
            </button>
          )}
        </div>
      </div>

      {loaded && rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--sa-radius-lg)] border border-dashed border-[var(--sa-alias-border-l2)] py-16 text-[13px] text-[var(--sa-alias-label-caption)]">
          {sub === 'builtin'
            ? '暂无平台内置条目'
            : query
              ? '无匹配能力'
              : capabilityKind === 'plugin'
                ? '还没有安装插件，去「插件市场」安装'
                : `还没有${kindLabel}，去「${kindLabel}市场」安装，或点右上角新建`}
        </div>
      ) : (
        <div className="grid justify-center gap-4 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]">
          {rows.map((item) => (
            <CapabilityCard
              key={`${item.kind}:${item.id}`}
              title={item.name}
              description={item.description}
              avatar={item.avatar}
              badges={
                <>
                  {item.source === 'builtin' ? (
                    <CardBadge>内置 · 全员可用</CardBadge>
                  ) : (
                    <CardBadge>{item.source === 'mine' ? '自建' : '已安装'}</CardBadge>
                  )}
                  {item.source !== 'builtin' && (
                    <CardBadge>{item.enabled ? '已启用' : '已停用'}</CardBadge>
                  )}
                  {item.revoked && <CardBadge>已被管理员下架</CardBadge>}
                </>
              }
              actionSlot={
                // 内置对普通用户只读；下架条目后端启停会 404，只保留详情页的卸载入口。
                // 用开关而非图标按钮：图标表达不了"当前启用中"，开关本身就是状态载体。
                item.source === 'builtin' || item.revoked
                  ? undefined
                  : (
                    <Switch
                      checked={item.enabled}
                      onChange={() => onToggle(item)}
                      disabled={busyKey === `${item.kind}:${item.id}`}
                      label={item.enabled ? `停用 ${item.name}` : `启用 ${item.name}`}
                    />
                  )
              }
              onClick={() => onOpen(item)}
            />
          ))}
        </div>
      )}

      {editingSkill !== undefined && (
        <SkillModal
          initial={editingSkill}
          onClose={(changed) => {
            setEditingSkill(undefined)
            if (changed) {
              toast('success', '已保存技能')
              void loadMine()
            }
          }}
        />
      )}
      {editingExpert !== undefined && (
        <ExpertModal
          initial={editingExpert}
          onClose={(changed) => {
            setEditingExpert(undefined)
            if (changed) {
              toast('success', '已保存专家')
              void loadMine()
            }
          }}
        />
      )}
    </div>
  )
}
