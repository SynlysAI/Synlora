---
name: pdf
description: PDF 解析与生成：提取正文/表格/表单、合并拆分旋转、填表单、reportlab 从零生成 PDF 报告。用户说"读下这份 PDF / 把表格抽出来 / 拆分合并 / 做成 PDF"时用。
version: "2.1"
author: 基于 Anthropic skills (pdf v2.1) Python 栈适配
tags:
  - 文档
  - PDF
---

# PDF 处理：提取、表单与生成

| 任务 | 路线 |
|---|---|
| **提取文本/表格** | `pdfplumber`（`extract_text` / `extract_tables`），纯文本粗读可用 `pypdf` |
| **表单填写** | `pypdf` 读 AcroForm 字段并回写（见 [references/forms.md](references/forms.md) 的字段语义说明） |
| **合并/拆分/旋转/加密** | `pypdf`（`PdfWriter`） |
| **从零生成 PDF** | `reportlab`（中文字体注册是第一坑，见下） |

> **Synlora 沙箱适配**：`pypdf` / `pdfplumber` / `reportlab` **已预装**，直接 import，
> **禁止 pip/uv 安装**（沙箱离线）。当前目录是会话工作区根，PDF 在 `files/`，
> 产物写 `output/`。无 pdftotext / LibreOffice / PyMuPDF；官方技能的 `scripts/`
> 不随附，等价能力按本技能的 Python 路线实现；高级参考见
> [references/reference.md](references/reference.md)。

## 提取文本

- 优先 `pdfplumber`：`with pdfplumber.open(p) as pdf: page.extract_text()`，
  按页处理、带坐标（`extract_words` 可拿到词级位置做版面分析）。
- **扫描件判定**：先抽 1-2 页统计字符数，平均每页字符数极低（如 < 50）即无文本层，
  判为扫描件——**提示用户需要 OCR 且本环境未预装 OCR**，不要臆造内容。
- 表格用 `page.extract_tables()`（返回行列表），跨页表格合并时保留表头并标注来源页码。
- 加密 PDF：`pypdf` 报 `needs_pass` 时如实报告，**不要尝试暴力破解**。
- 引用关键结论时标注来源页码。

## 表单

表单字段语义（text / checkbox / radio_group / choice 的取值规则、坐标体系）见
[references/forms.md](references/forms.md)；其中脚本用法换成 pypdf 等价实现：

```python
from pypdf import PdfReader, PdfWriter
reader = PdfReader('files/form.pdf')
fields = reader.get_fields()          # {字段名: 字典}，含 /FT 类型与选项
writer = PdfWriter(clone_from='files/form.pdf')
writer.update_page_form_field_value(writer.pages[0], {'name_1': '聚乳酸'})
with open('output/filled.pdf', 'wb') as f:
    writer.write(f)
```

非可填表单（扁平扫描表格）没有字段可填：向用户说明，必要时用 reportlab 生成
覆盖层，不要覆盖在原件上。

## 合并 / 拆分 / 旋转

```python
from pypdf import PdfReader, PdfWriter
writer = PdfWriter()
for p in ['files/a.pdf', 'files/b.pdf']:
    for page in PdfReader(p).pages:
        writer.add_page(page)         # 单页抽取/排序同理
with open('output/merged.pdf', 'wb') as f:
    writer.write(f)
```

## 用 reportlab 生成 PDF

**中文字体必须注册**（第一坑，不注册直接画中文是黑块）：

```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))   # 内置 CID 字体，中文可用
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
doc = SimpleDocTemplate('output/report.pdf', pagesize=A4)
style = ParagraphStyle('zh', fontName='STSong-Light', fontSize=10.5, leading=16)
doc.build([Paragraph('样品表征报告', style), Spacer(1, 12),
           Paragraph('DSC 曲线显示 Tg 约 62 ℃。', style)])
```

- 版面排布用 platypus 层（`SimpleDocTemplate` + flowables：`Paragraph` / `Table` /
  `Image` / `PageBreak`），不要手工 `canvas.drawString` 定位长文档。
- 图片插入用 `reportlab.platypus.Image('output/fig1.png', width=14*cm, height=8*cm)`，
  matplotlib 图先存 PNG。
- 表格样式用 `TableStyle`（`GRID` 边框、`BACKGROUND` 表头底色、对齐）。
- 用户明确要 PDF 交付时才做 PDF；Word 交付不在本技能范围内。

## 交付约定

产物写 `output/`，`file.send` 交付工作区相对路径；说明页数、判定类型
（文本层 / 扫描件）与关键产物的来源页码。
