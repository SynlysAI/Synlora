---
name: pdf-extraction
description: 从工作区里的 PDF 提取正文文本、章节结构或表格，导出为 Markdown/CSV（扫描件走 OCR）。用户说"读下这份 PDF""提取论文内容""把表格抽出来"时使用。
version: "1.0"
author: SynlysAgent
tags:
  - PDF
  - 文本抽取
---

# PDF 内容抽取

## 目标
把 `files/` 里的 PDF 变成可读的文本或结构化表格，产物落在 `output/`。

## 工作流
1. 用 `file.list` 找到 `files/` 下的 PDF，确认页数与文件大小
2. 用 `python.run` 调 `pypdf` 逐页抽取纯文本，判断是否为扫描件（字符数极少即无文本层）
3. 需要版面或表格时改用 `pdfplumber`，按页 `extract_table()` 取表格
4. 无文本层的扫描件提示需要 OCR，不要臆造内容
5. 抽取结果写入 `output/`（正文 `.md`，表格 `.csv`）

## 决策规则
- 文本层为空（平均每页字符数远低于阈值）时判为扫描件，先问用户是否接受 OCR 或改用手工处理
- 表格跨页合并时保留表头，标注来源页码
- 加密 PDF 先报告 `needs_pass`，不要尝试暴力破解

## 输出要求
给出 PDF 的页数、判定类型（文本层 / 扫描件）、产物的工作区相对路径；引用的关键结论标注来源页码
