---
name: docx
description: 创建、读取、编辑 Word 文档（.docx）：python-docx 生成带样式/表格/图片/页眉页脚的文档，或解包 OOXML 精改已有文件。用户要"写报告 / 导出 Word / 改这份 docx"时用。
version: "2.1"
author: 基于 Anthropic skills (docx v2.1) Python 栈适配
tags:
  - 文档
  - Word
---

# DOCX 创建、编辑与读取

`.docx` 本质是 XML 文件的 ZIP 包。按任务选路线：

| 任务 | 路线 |
|---|---|
| **创建**新文档 | `python-docx`（`from docx import Document`），见下方 gotchas |
| **编辑**已有文档 | 常规改动用 `python-docx`；精确/批量改走解包：`zipfile` 解压 → 改 `word/document.xml` → 重打包 |
| **读取**内容 | `python-docx` 遍历 `paragraphs` / `tables`；只抽纯文本时按段落拼 `paragraph.text` |

> **Synlora 沙箱适配**：`python-docx`（import 名 `docx`）**已预装**，直接 import，
> **禁止 pip/uv 安装**（沙箱离线）。当前目录是会话工作区根，产物写 `output/`
> （先 `os.makedirs('output', exist_ok=True)`）。无 pandoc / LibreOffice / node 环境，
> 正文中提及这些工具的路线一律换成本技能给出的 Python 等价实现。

## 用 python-docx 创建 — gotchas

模型熟悉 API，这里是事故多发点（继承自官方 docx-js 技能并映射到 python-docx）：

- **页面尺寸**：默认跟随内置模板（Letter）。要 A4 竖版：
  `sec.page_width, sec.page_height = Mm(210), Mm(297)`；横版直接交换宽高（python-docx
  没有 orientation 自动换算，`sec.orientation` 只写标记，数值要自己给对）。
- **表格必须每个单元格都设宽度**：`table.columns[i].width` 在多数查看器里不生效；
  遍历设置 `table.cell(r, c).width = Cm(x)`，同一列每行都设，且列宽之和等于表宽。
  表格想要边框线需操作 XML（`tbl.tblPr` 加 `tblBorders`），默认是无边框的。
- **列表永远用样式**，不要手打 `•` 字符：无序 `doc.add_paragraph(text, style='List Bullet')`，
  有序 `style='List Number'`。多级用 `List Bullet 2` / `List Number 2`。
- **换行用 `add_paragraph()`**，不要在文本里塞 `\n`（python-docx 不会把 `\n` 渲染成换行；
  确需软换行时对 run 追加 `<w:br/>`）。
- **标题用内置样式** `Heading 1/2/3`（`doc.add_heading(text, level=n)`）——目录域
  只认内置标题的 outline level；自造样式不会进目录。
- **图片**：`doc.add_picture('output/fig1.png', width=Inches(6))`，matplotlib 图先
  `savefig('output/fig1.png', dpi=150, bbox_inches='tight')`。
- **中文字体**：`run.font.name = 'Times New Roman'` 只对西文生效，中文必须再设
  `run._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')`（或黑体/微软雅黑）。
- **页眉页脚**：`sec.header.paragraphs[0].text`；首页不同 `sec.different_first_page_header_footer = True`。

## 编辑已有文档

- **读取时文本是碎片化的**：Word 把一段话拆成多个 run（拼写检查/修订标记），肉眼可见
  的短语在 XML 里可能不连续。按段落处理用 `paragraph.runs`；整段替换直接改写
  `paragraph` 的 runs（清掉再加一个 run，或对每个 run 做 `run.text = ...`）。
- `text_frame.text = "..."` 式的整体赋值（`cell.text = ...`）会**清掉原格式**，保留格式
  的改法是只动 `run.text`。
- **修订/批注**：python-docx 不支持 tracked changes 与 comments 的增删；需要时走
  XML 层（`<w:ins>`/`<w:del>` 包住 run，`w:author`/`w:date` 属性必填；删除段落的
  段标记是 `<w:pPr><w:rPr><w:del/></w:rPr></w:pPr>`）。不熟 OOXML 时向用户说明局限。
- **解包重打包**（批量正则、改样式 XML 时）：
  ```python
  import zipfile, shutil, os
  src = zipfile.ZipFile('files/in.docx')
  src.extractall('tmp_docx')            # 改 tmp_docx/word/document.xml（不要格式化/美化 XML）
  with zipfile.ZipFile('output/out.docx', 'w', zipfile.ZIP_DEFLATED) as z:
      for root, _, files in os.walk('tmp_docx'):
          for f in files:
              p = os.path.join(root, f)
              z.write(p, os.path.relpath(p, 'tmp_docx'))
  ```
- **外来 docx 不可信**：解包后先删 symlink 条目（`find tmp_docx -type l` 为空的检查），
  XML 解析用 `defusedxml`，不要 `xml.etree` 直接解析不可信内容。
- 旧 `.doc` 格式 python-docx 打不开：提示用户先在外部转成 `.docx`，不要臆造内容。

## 交付约定

- 保存到 `output/`（文件名英文或简短中文，如 `analysis_report.docx`），完成后用
  `file.send` 把工作区相对路径交给用户。
- 图表标题、表头、术语与用户输入保持一致；不要自创口径。
