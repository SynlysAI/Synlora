---
name: pptx
description: 创建、读取、编辑 PowerPoint 幻灯片（.pptx/.potx）：python-pptx 生成版式、文本、图表与图片，汇报/组会/答辩 PPT 都走这里。用户要"做 PPT / 汇报幻灯片 / 讲下这份 deck"时用。
version: "2.1"
author: 基于 Anthropic skills (pptx v2.1) Python 栈适配
tags:
  - 文档
  - PPT
---

# PPTX 创建、编辑与分析

`.pptx` 本质是 XML 文件的 ZIP 包。按任务选路线：

| 任务 | 路线 |
|---|---|
| **创建**新演示 | `python-pptx`（`from pptx import Presentation`），见 gotchas |
| **编辑**已有演示 / 套模板 | `python-pptx` 改文本与形状；结构性改动（增删页）走解包 XML |
| **读取**内容 | 遍历 `slide.shapes` 取 `text_frame.text`，逐页拼节（标注页码） |

> **Synlora 沙箱适配**：`python-pptx`（import 名 `pptx`）**已预装**，直接 import，
> **禁止 pip/npm 安装**（沙箱离线）。当前目录是会话工作区根，数据在 `files/`，
> 产物写 `output/`。无 pptxgenjs / LibreOffice / node：官方技能的 JS 路线全部换成
> 本技能的 python-pptx 等价实现。

## 用 python-pptx 创建 — gotchas

- **画布先设尺寸**：默认是 4:3（10"×7.5"）。宽屏 16:9 要显式设：
  `prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)`，必须在加页之前。
- **颜色用 `RGBColor.from_string('1E2761')`**——hex 字符串**不带 `#`**；
  透明度 python-pptx 不直接支持（需操作 XML `<a:alpha>`）。
- **`text_frame.text = "..."` 会清格式**：整段赋值把段落塌缩成单个无样式 run。
  保留格式的改法是操作 `paragraph.runs[i].text`。新建时用
  `tf.paragraphs[0].text = ...` 后续段落 `tf.add_paragraph()`。
- **列表用段落层级**，不要手打 `•`（会双重圆点）：无序列表给段落设
  `buChar` XML 或套用版式带的项目符号；新建简单场景可用 `• ` 前缀 + 统一缩进，
  但同页风格必须一致。
- **`add_picture` 认不出 SVG/EMF**（模板美术常用格式）——需要位图（PNG/JPG）。
  matplotlib 图先 `savefig('output/fig.png', dpi=150, bbox_inches='tight')` 再插入。
- **演讲者备注**：`slide.notes_slide.notes_text_frame.text = "..."`，一页一次，
  不要把备注做成页面上的文本框。
- **图表保持原生**：数据图用 `slide.shapes.add_chart(XL_CHART_TYPE...., x, y, cx, cy, chart_data)`
  （可编辑、样式可控），不要贴渲染图片；只有 PPT 没有原生形态的图（网络图、和弦图）
  才用图片。默认图表裸奔：补 `chart.has_title = True`、
  `plot.has_data_labels = True`、`chart.series[i].format.line.color.rgb` 上色、
  单系列 `chart.has_legend = False`。
- **python-pptx 三不做**：复制幻灯片（只有 `add_slide(layout)`，复制需解包 XML 手工
  注册）、恢复被 `text` 赋值毁掉的样式、读取 SVG/EMF——遇到就换路线或向用户说明。
- 结构性增删页：解包后改 `ppt/presentation.xml` 的 `<p:sldIdLst>`，
  页内容在 `ppt/slides/slideN.xml`；**先做完增删再改内容**。
  XML 解析用 `defusedxml`（`xml.etree` 往返会重写命名空间前缀，文件损坏）。
- 一个列表项一个 `<a:p>`，永远不要把多项拼进同一段；文本带首尾空格要加
  `xml:space="preserve"`。

## 设计要点（继承官方设计原则 — 不要做无聊的幻灯片）

**开始前**：
- **选一套与内容强相关的调色板**：把你的配色换到另一场完全不同的汇报里"也还行"，
  说明选得不够具体。
- **主次分明**：一个主色占 60-70% 视觉权重 + 1-2 个辅助色调 + 一个尖锐的强调色；
  永远不要各色均分。
- **深浅三明治**：封面与结论页深底，内容页浅底；或全程深底走高级感。
- **一个视觉母题贯穿全场**：圆角图片框、色圈图标……重复出现。
  **不要**用色条/装饰条纹当母题。

**调色板参考**（主题色 / 辅助色 / 强调色，hex 无 #）：

| 主题 | 主色 | 辅助 | 强调 |
|---|---|---|---|
| 深夜行政 | `1E2761` 藏青 | `CADCFC` 冰蓝 | `FFFFFF` 白 |
| 森林苔藓 | `2C5F2D` 森林绿 | `97BC62` 苔绿 | `F5F5F5` 米白 |
| 珊瑚活力 | `F96167` 珊瑚 | `F9E795` 金 | `2F3C7E` 藏青 |
| 海洋渐层 | `065A82` 深海蓝 | `1C7293` 青 | `21295C` 午夜蓝 |
| 炭灰极简 | `36454F` 炭灰 | `F2F2F2` 灰白 | `212121` 黑 |
| 蓝绿可信 | `028090` 青蓝 | `00A896` 海泡绿 | `02C39A` 薄荷 |

**逐页自检**：每页一个视觉焦点（大数字/大图/一句话），支撑文字做背景层；
字号阶梯至少 3 档（标题 32-40pt / 小节 20-24pt / 正文 14-16pt）；
科研汇报突出"一页一个结论"，图大字少，方法细节放备注页。

## 交付约定

保存到 `output/`（如 `group_meeting.pptx`），`file.send` 交付工作区相对路径；
逐页检查文本不溢出边框（文本框 `word_wrap = True`，长文本给足高度）。
