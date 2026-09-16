/**
 * 启停开关。
 *
 * 尺寸与观感照抄 jiuwenswarm `components/ui/Switch`（42×24 圆角药丸 + 16px 滑块，
 * 选中态填主色、滑块右移 18px，120ms 过渡），token 换成本项目的 `--sa-*`。
 *
 * 用它取代此前的"图标按钮"：一条横线/一个箭头的图标表达不了"启用中"这个状态，
 * 开关本身就是状态的可见载体（参考项目在技能列表行尾也是这么用的）。
 */
interface SwitchProps {
  /** 当前是否启用。 */
  checked: boolean
  /** 切换回调（内部已阻止冒泡，避免连带触发卡片点击）。 */
  onChange: (checked: boolean) => void
  /** 操作进行中：禁用并降透明度。 */
  disabled?: boolean
  /** 无障碍标签（如「停用 谱图解析」）。 */
  label: string
}

/** 启停开关。 */
export default function Switch({ checked, onChange, disabled = false, label }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation()
        onChange(!checked)
      }}
      className={`relative h-6 w-[42px] shrink-0 rounded-[var(--sa-radius-full)] border transition-colors duration-[var(--sa-duration-fast)] disabled:cursor-not-allowed disabled:opacity-50 ${
        checked
          ? 'border-[var(--sa-alias-button-primary-fill)] bg-[var(--sa-alias-button-primary-fill)]'
          : 'border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-interactive-bg-hover)]'
      }`}
    >
      <span
        aria-hidden="true"
        className={`absolute left-[3px] top-[3px] h-4 w-4 rounded-[var(--sa-radius-full)] bg-[var(--sa-alias-label-primary-foreground)] shadow-sm transition-transform duration-[var(--sa-duration-fast)] ${
          checked ? 'translate-x-[18px]' : ''
        }`}
      />
    </button>
  )
}
