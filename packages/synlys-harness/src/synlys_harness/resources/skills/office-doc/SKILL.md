---
name: office-doc
description: 生成 Word / Excel / PPT 办公文档（分析报告、数据表、汇报幻灯片）。用户要"整理成报告/做成 PPT/导出 Excel"时使用。
version: "1.0"
author: SynlysAgent
tags:
  - 文档
  - 报告
---

# Office 文档生成

## 目标
把分析结果交付成可下载的 Word（.docx）/ Excel（.xlsx）/ PowerPoint（.pptx）文件，产物落在 `output/` 并用 `file.send` 交给用户。

## 环境约定（重要）
运行环境**已预装**以下库，直接 import 即可，**不要执行 pip install**：
- `docx`（python-docx）— Word
- `pptx`（python-pptx）— PPT
- `openpyxl` — Excel（pandas 写 xlsx 也依赖它）
- `pandas` / `matplotlib` — 数据与图表

注意：`python.run` 是隔离模式（`-I`），当前目录是 `tmp/`；产物写到 `output/` 用相对路径 `../output/文件名`（或先 `os.makedirs('../output', exist_ok=True)`）。

## 工作流
1. 先把内容结构想清楚（标题层级 / 表格 / 图表位置），一次 `python.run` 写完整生成脚本
2. matplotlib 图先 `savefig('../output/fig1.png', dpi=150, bbox_inches='tight')`
3. 文档里嵌入图片：Word 用 `doc.add_picture('../output/fig1.png', width=Inches(6))`；
   PPT 用 `slide.shapes.add_picture('../output/fig1.png', left, top, width)`
4. 保存文档到 `../output/`，文件名用英文或简短中文（如 `analysis_report.docx`）
5. 用 `file.send` 交付：`path` 填 `output/文件名`，附一句 `note` 说明内容
6. 回复里概括文档结构（几个章节/页/表/图），不要粘贴全文

## 中文与排版要点
- matplotlib 中文字体必须显式设置，否则方框：
  `plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']`，
  负号 `plt.rcParams['axes.unicode_minus'] = False`
- Word 正文默认即可；标题用 `doc.add_heading(级数)`；表格用 `doc.add_table` 后填 `cell.text`
- PPT 每页一个观点：`prs.slide_layouts[1]`（标题+内容）起步，避免整段文字堆一页
- Excel 用 `pd.ExcelWriter('../output/x.xlsx')` 多 sheet 写出，关键结论放第一个 sheet

## 决策规则
- 用户没说要什么格式时：汇报/综述 → Word；数据表 → Excel；对外展示 → PPT；拿不准就用 ask_user 问一句
- 内容以已有分析结果为准，不要编造数据；缺数据先读文件或检索
- 单个文件超过 ~30 页/10 图时提醒用户篇幅，确认后再生成
