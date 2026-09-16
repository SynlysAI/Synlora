/**
 * 「我的」面板：自建 + 已安装的能力（技能/专家/插件），带子页签过滤。
 *
 * 结构照 jiuwen `SkillPanel/index.tsx`：子页签 全部/已启用/已停用/内置；
 * 自建条目可编辑/删除，内置条目只能启用/停用/卸载（规格 D5：内置不可定制）。
 */
import { useEffect, useState } from 'react'
import { GrayBadge } from '@/components/admin/shared'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'
import { ExpertModal, SkillModal } from './CapabilityModals'

type SubTab = 'all' | 'enabled' | 'disabled' | 'builtin'

const SUB_TABS: Array<{ key: SubTab; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'enabled', label: '已启用' },
  { key: 'disabled', label: '已停用' },
  { key: 'builtin', label: '内置' },
]

const KIND_LABEL: Record<MyCapability['kind'], string> = {
  skill: '技能',
  expert: '专家',
  plugin: '插件',
}

/** 「我的」面板。 */
export default function MinePanel() {
  const items = useMyCapabilitiesStore((s) => s.items)
  const builtinItems = useMyCapabilitiesStore((s) => s.builtinItems)
  const loaded = useMyCapabilitiesStore((s) => s.loaded)
  const loadMine = useMyCapabilitiesStore((s) => s.loadMine)
  const deleteSkill = useMyCapabilitiesStore((s) => s.deleteSkill)
  const deleteExpert = useMyCapabilitiesStore((s) => s.deleteExpert)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
  const uninstall = useCatalogStore((s) => s.uninstall)
  const [sub, setSub] = useState<SubTab>('enabled')
  const [editingSkill, setEditingSkill] = useState<MyCapability | null | undefined>(undefined)
  const [editingExpert, setEditingExpert] = useState<MyCapability | null | undefined>(undefined)

  useEffect(() => {
    loadMine().catch((err) => toast('error', `加载我的能力失败：${errorText(err)}`))
  }, [loadMine])

  // 「内置」页签走只读数据源（平台内置条目，用户无启停）；其余页签过滤自己的列表
  const rows =
    sub === 'builtin'
      ? builtinItems
      : items.filter((item) => {
          if (sub === 'enabled') return item.enabled
          if (sub === 'disabled') return !item.enabled
          return true
        })

  const handleToggle = async (item: MyCapability) => {
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
      await loadMine()
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  const handleRemove = async (item: MyCapability) => {
    try {
      if (item.source === 'mine' && item.kind === 'skill') await deleteSkill(item.id)
      else if (item.source === 'mine' && item.kind === 'expert') await deleteExpert(item.id)
      else await uninstall(item.kind, item.id)
      await loadMine()
      toast('success', `已删除 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 工具行：新建入口 + 子页签 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {SUB_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setSub(t.key)}
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
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setEditingSkill(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建技能
          </button>
          <button type="button" onClick={() => setEditingExpert(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建专家
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {rows.map((item) => (
          <div
            key={`${item.kind}:${item.id}`}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-[14px] font-medium">{item.name}</span>
                <GrayBadge>{KIND_LABEL[item.kind]}</GrayBadge>
                {item.source === 'builtin' ? (
                  <GrayBadge>内置 · 全员可用</GrayBadge>
                ) : (
                  <GrayBadge>{item.source === 'mine' ? '自建' : '已安装'}</GrayBadge>
                )}
                {item.revoked && <GrayBadge>已被管理员下架</GrayBadge>}
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={item.description}>
                {item.description || '无描述'}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {item.source === 'builtin' ? (
                // 内置条目：管理员配置强制全员可用，用户无启停/卸载概念
                <span className="text-[12px] text-[var(--sa-alias-label-caption)]">自动可用</span>
              ) : (
                <>
              {item.enabled ? <GrayBadge>已启用</GrayBadge> : <GrayBadge>已停用</GrayBadge>}
              {!item.revoked && (
                <button type="button" onClick={() => void handleToggle(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  {item.enabled ? '停用' : '启用'}
                </button>
              )}
              {item.source === 'mine' && item.kind === 'skill' && (
                <button type="button" onClick={() => setEditingSkill(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              {item.source === 'mine' && item.kind === 'expert' && (
                <button type="button" onClick={() => setEditingExpert(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              <button type="button" onClick={() => void handleRemove(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                {item.source === 'mine' ? '删除' : '卸载'}
              </button>
                </>
              )}
            </div>
          </div>
        ))}
        {loaded && rows.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            {sub === 'builtin'
              ? '暂无平台内置条目（管理员可在后台把条目配置为内置）'
              : '还没有技能或专家，去「市场」安装，或点右上角自己创建一个'}
          </div>
        )}
      </div>

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
