/**
 * 常规设置页：外观与界面语言等平台级设置（占位，后续实现）。
 */
export default function GeneralAdmin() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[15px] font-medium text-[var(--sa-alias-label-primary)]">常规</h2>
        <p className="mt-1 text-[13px] text-[var(--sa-alias-label-tertiary)]">
          外观与界面语言等平台级设置。
        </p>
      </div>
      <div className="rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-5 py-6">
        <p className="text-[13px] text-[var(--sa-alias-label-tertiary)]">
          暂无可配置项（外观主题已可经右上角按钮切换；界面语言等后续版本提供）。
        </p>
      </div>
    </div>
  )
}
