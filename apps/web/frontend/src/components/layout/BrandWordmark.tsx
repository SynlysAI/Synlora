/**
 * 品牌字标「synlora」，照抄 DSH `ui-primitives/BrandWordmark.tsx` 的形态：
 * 一个内联 SVG（非图片、非字体），`stroke="currentColor"` 跟随明暗主题，
 * 由 `height` 决定大小、宽度按 viewBox 比例自适应。
 *
 * 与 DSH 的差别：DSH 是实心轮廓（filled outline，笔画有粗细变化），这里是
 * **等线几何**（monoline，统一描边宽度 + 圆头圆角），用圆弧与直线拼出字母，
 * 没有手绘轮廓那种光学修正，所以风格更"几何/冷淡"一些。
 *
 * 度量（viewBox 0 0 58 28）：基线 y=20，x-height 顶 y=12，
 * 升部顶 y=5（l），降部底 y=23（y）；描边 2.8。
 * 全小写，与 DSH 的 deepseek 一致。
 */
const WORDMARK_STROKE_WIDTH = 2.8

/** 字标各字母的路径（s y n l o r a）。 */
const GLYPHS = [
  // s：上下两个碗，中段过渡
  'M6.9 14.5C6.9 13.1 5.8 12.1 4.2 12.1C2.7 12.1 1.6 12.9 1.6 14.1C1.6 15.2 2.5 15.8 4 16.1C5.5 16.4 6.4 17 6.4 18.1C6.4 19.3 5.3 20 3.9 20C2.4 20 1.3 19.1 1.3 17.8',
  // y：左臂落在右臂线上，右下延伸为降部
  'M9.8 12L12.7 18.8',
  'M15.1 12L11.2 23',
  // n：左竖 + 方肩 + 右竖
  'M18.4 20V12H20.4A2.2 2.2 0 0 1 22.6 14.2V20',
  // l：升部竖画
  'M26.8 5V20',
  // r：竖画 + 上肩
  'M41.8 20V12',
  'M41.8 14.8C41.8 13.2 42.8 12.1 44.4 12.1',
  // a：单层几何 a = 圆 + 右竖
  'M55.3 12V20',
]

/** 圆形的字（o、a 的碗）。 */
const CIRCLES = [
  { cx: 35, cy: 16, r: 3.9 },
  { cx: 51.4, cy: 16, r: 3.9 },
]

interface BrandWordmarkProps {
  /** 字标高度（px）；宽度按 viewBox 比例自适应（约 2.07 倍）。 */
  height?: number
  /** 附加类名（布局用）。 */
  className?: string
}

/** 品牌字标（几何等线 SVG）。 */
export default function BrandWordmark({ height = 24, className }: BrandWordmarkProps) {
  return (
    <svg
      width={(height * 58) / 28}
      height={height}
      viewBox="0 0 58 28"
      fill="none"
      stroke="currentColor"
      strokeWidth={WORDMARK_STROKE_WIDTH}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role="img"
      aria-label="Synlora"
    >
      {GLYPHS.map((d) => (
        <path key={d} d={d} />
      ))}
      {CIRCLES.map((c) => (
        <circle key={`${c.cx}-${c.cy}`} cx={c.cx} cy={c.cy} r={c.r} />
      ))}
    </svg>
  )
}
