/**
 * 品牌字标「synlora」，照抄 DSH `ui-primitives/BrandWordmark.tsx` 的形态：
 * 一个内联 SVG（非图片、非字体），`stroke="currentColor"` 跟随明暗主题，
 * 由 `height` 决定大小、宽度按 viewBox 比例自适应。
 *
 * 与 DSH 的差别：DSH 是实心轮廓（filled outline，笔画有粗细变化），这里是
 * **等线几何**（monoline，统一描边宽度 + 圆头圆角），用圆弧与直线拼出字母，
 * 没有手绘轮廓那种光学修正，所以风格更"几何/冷淡"一些。
 *
 * 度量（viewBox 0 0 60 28）：基线 y=20，大写 S 顶 y=6，
 * x-height 顶 y=12，升部顶 y=5（l），降部底 y=23（y）；描边 2.8。
 * 首字母用**大写 S**（cap-height）——小写 s 只有 x-height 高，
 * 在等线字标里起笔会显得弱，大写才立得住。
 */
const WORDMARK_STROKE_WIDTH = 2.8

/** 字标各字母的路径（S y n l o r a）。 */
const GLYPHS = [
  // S：大写，cap-height（y 6→20），上下两个碗
  'M9.0 10.3C9.0 7.8 7.7 6.0 5.7 6.0C3.9 6.0 2.6 7.4 2.6 9.6C2.6 11.5 3.7 12.6 5.5 13.1C7.3 13.6 8.4 14.7 8.4 16.6C8.4 18.8 7.1 20.0 5.4 20.0C3.5 20.0 2.2 18.4 2.2 16.1',
  // y：左臂落在右臂线上，右下延伸为降部
  'M11.4 12L14.3 18.8',
  'M16.7 12L12.8 23',
  // n：左竖 + 方肩 + 右竖
  'M20 20V12H22A2.2 2.2 0 0 1 24.2 14.2V20',
  // l：升部竖画
  'M28.4 5V20',
  // r：竖画 + 上肩
  'M43.4 20V12',
  'M43.4 14.8C43.4 13.2 44.4 12.1 46 12.1',
  // a：单层几何 a = 圆 + 右竖
  'M56.9 12V20',
]

/** 圆形的字（o、a 的碗）。 */
const CIRCLES = [
  { cx: 36.6, cy: 16, r: 3.9 },
  { cx: 53, cy: 16, r: 3.9 },
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
      width={(height * 60) / 28}
      height={height}
      viewBox="0 0 60 28"
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
