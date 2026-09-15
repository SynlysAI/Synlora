/**
 * 品牌字标「synlora」，照抄 DSH `ui-primitives/BrandWordmark.tsx` 的形态：
 * 一个内联 SVG（非图片、非字体），由 `height` 决定大小、宽度按 viewBox 比例自适应。
 *
 * 与 DSH 的差别：DSH 是实心轮廓（filled outline，笔画有粗细变化），这里是
 * **等线几何**（monoline，统一描边宽度 + 圆头圆角），用圆弧与直线拼出字母，
 * 没有手绘轮廓那种光学修正，所以风格更"几何/冷淡"一些。
 *
 * 描边为**品牌渐变**（--sa-specific-brand-wordmark-from/to，明暗各一档，
 * 与图标 S 缎带同源配色）——此前跟随主题前景色的墨色字标与彩色图标
 * "无彩色 vs 高饱和渐变"割裂，品牌色从图标延伸到字标。
 *
 * 度量（viewBox 0 0 65 28）：基线 y=20，大写 S 顶 y=6，
 * x-height 顶 y=12，升部顶 y=5（l），降部底 y=23（y）；描边 2.8。
 * 首字母用**大写 S**（cap-height）——小写 s 只有 x-height 高，
 * 在等线字标里起笔会显得弱，大写才立得住。
 * 字距：字母间隙统一约 1.0-1.4（描边 2.8 下不挤）；n 的宽度约为 o 的
 * 77%（几何无衬线的正常比例，最初 4.2 vs o 7.8 过窄已修正）。
 */
import { useId } from 'react'

const WORDMARK_STROKE_WIDTH = 2.8

/** 字标各字母的路径（S y n l o r a）。 */
const GLYPHS = [
  // S：大写，cap-height（y 6→20），上下两个碗
  'M9.0 10.3C9.0 7.8 7.7 6.0 5.7 6.0C3.9 6.0 2.6 7.4 2.6 9.6C2.6 11.5 3.7 12.6 5.5 13.1C7.3 13.6 8.4 14.7 8.4 16.6C8.4 18.8 7.1 20.0 5.4 20.0C3.5 20.0 2.2 18.4 2.2 16.1',
  // y：左臂落在右臂线上，右下延伸为降部
  'M12.2 12L15.1 18.8',
  'M17.5 12L13.6 23',
  // n：左竖 + 方肩 + 右竖（宽 6.0，字身后段整体右移 1.0 保持字距）
  'M21.6 20V12H25.2A2.4 2.4 0 0 1 27.6 14.4V20',
  // l：升部竖画
  'M31.8 5V20',
  // r：竖画 + 上肩
  'M48.4 20V12',
  'M48.4 14.8C48.4 13.2 49.4 12.1 51 12.1',
  // a：单层几何 a = 圆 + 右竖
  'M62.7 12V20',
]

/** 圆形的字（o、a 的碗）。 */
const CIRCLES = [
  { cx: 40.8, cy: 16, r: 3.9 },
  { cx: 58.8, cy: 16, r: 3.9 },
]

interface BrandWordmarkProps {
  /** 字标高度（px）；宽度按 viewBox 比例自适应（约 2.07 倍）。 */
  height?: number
  /** 附加类名（布局用）。 */
  className?: string
}

/** 品牌字标（几何等线 SVG，品牌渐变描边）。 */
export default function BrandWordmark({ height = 24, className }: BrandWordmarkProps) {
  // 渐变 id 按实例唯一：字标在侧栏与消息流头部同时在场，重复 id 会互相串引用
  const gradId = `synlora-wordmark-grad-${useId().replace(/[^a-zA-Z0-9-]/g, '')}`
  return (
    <svg
      width={(height * 65) / 28}
      height={height}
      viewBox="0 0 65 28"
      fill="none"
      stroke={`url(#${gradId})`}
      strokeWidth={WORDMARK_STROKE_WIDTH}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role="img"
      aria-label="Synlora"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="65" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--sa-specific-brand-wordmark-from)" />
          <stop offset="1" stopColor="var(--sa-specific-brand-wordmark-to)" />
        </linearGradient>
      </defs>
      {GLYPHS.map((d) => (
        <path key={d} d={d} />
      ))}
      {CIRCLES.map((c) => (
        <circle key={`${c.cx}-${c.cy}`} cx={c.cx} cy={c.cy} r={c.r} />
      ))}
    </svg>
  )
}
