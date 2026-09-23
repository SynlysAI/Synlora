/** 勾选卡片组：能力中心与后台管理共用的多选控件（名称 + 备注两行、边框卡片式）。 */
export function Selection({
  title,
  items,
  selected,
  onToggle,
  empty,
}: {
  title: string
  items: Array<{ id: string; label: string; note: string }>
  selected: string[]
  onToggle: (id: string) => void
  empty?: string
}) {
  return <div className="flex flex-col gap-2"><span className="text-xs text-[var(--sa-alias-label-secondary)]">{title}</span>{items.length === 0 ? <span className="text-xs text-[var(--sa-alias-label-caption)]">{empty ?? '暂无可选项'}</span> : <div className="grid gap-1 sm:grid-cols-2">{items.map((item) => <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-2.5 py-2 text-xs hover:bg-[var(--sa-alias-interactive-bg-hover)]"><input type="checkbox" checked={selected.includes(item.id)} onChange={() => onToggle(item.id)} className="mt-0.5" /><span className="min-w-0"><span className="block truncate text-[var(--sa-alias-label-primary)]">{item.label}</span><span className="block truncate text-[10px] text-[var(--sa-alias-label-caption)]">{item.note}</span></span></label>)}</div>}</div>
}
