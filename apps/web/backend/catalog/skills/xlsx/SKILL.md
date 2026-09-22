---
name: xlsx
description: 创建、编辑、分析 Excel 电子表格（.xlsx/.xlsm）：openpyxl 处理公式/样式/多工作表，pandas 批量读写数据。用户要"导出数据表 / 整理 Excel / 做个带公式的表格"时用。
version: "2.2"
author: 基于 Anthropic skills (xlsx v2.2) Python 栈适配
tags:
  - 文档
  - Excel
---

# XLSX 创建、编辑与分析

| 任务 | 路线 |
|---|---|
| **创建/编辑**（公式、样式、合并单元格） | `openpyxl` — 见 gotchas |
| **批量数据**进出 | `pandas`（`read_excel` / `to_excel`，to_excel 依赖 openpyxl） |
| **读取**既有工作簿 | 两遍 `load_workbook`（见下） |

> **Synlora 沙箱适配**：`openpyxl` / `pandas` **已预装**，直接 import，
> **禁止 pip/uv 安装**（沙箱离线）。当前目录是会话工作区根，数据在 `files/`，
> 产物写 `output/`。无 LibreOffice / markitdown：正文提及的 `recalc.py`、
> `soffice` 校验路线不可用，公式验证用本技能的 Python 等价做法。

## 每次输出的要求（继承官方纪律）

- **用公式，不要硬编码结果**：写 `sheet['B10'] = '=SUM(B2:B9)'`，而不是 Python 算好的
  总数——表格要能在输入变化时重算。例外：用户明确要"静态快照"时在交付说明里讲明。
- **零公式错误交付**：openpyxl 写入的公式**没有缓存值**（读回是 `None`、预览器空白
  都正常），Excel/WPS 打开时才计算。因此交付前**自己验证公式逻辑**：取 2-3 个公式，
  用 pandas 把参与计算的原始数据读出来手工核对结果；范围差一行、引用错一行都不会
  报错，只会悄悄算错。
- **假设放独立单元格并被公式引用**（`=B5*(1+$B$6)`，永远不写 `=B5*1.05`），
  公式整行/整列保持一致——中间一格被单独改过是最常见的静默错误。
- **照用户的字面规格**：工作表名、列头、写明的公式逐字执行；自作主张重新设计
  即使更优雅也算失败。
- **给读者填的工作簿**要有一行图例标明哪些单元格可编辑 + 一行示例数据；
  编辑别人的文件时**完全沿用其既有约定**（输入单元格的字体色/底色标记先找到，
  只写那里，别动既有公式）。
- 分母可能为零的公式加 `IFERROR`。

## 选择能在验证下存活的公式（继承官方规则）

openpyxl 把公式字符串原样写进 XML，Excel 对 2007 后的新函数**带 `_xlfn.` 前缀存储**
（UI 里隐藏前缀）：

- 优先用 Excel 2007 时代函数：`SUMIFS` / `INDEX` / `MATCH` / `IFERROR` / `SUMPRODUCT`。
- `TEXTJOIN` / `CONCAT` / `IFS` / `SWITCH` / `MAXIFS` / `MINIFS` 能用但**必须写
  `_xlfn.` 前缀**（`=TEXTJOIN(...)` → `=_xlfn.TEXTJOIN(...)`），裸写就是 `#NAME?`。
- **禁用** `XLOOKUP` / `XMATCH` / `SORT` / `FILTER` / `UNIQUE` / `SEQUENCE`：它们是
  溢出数组函数，openpyxl 写出的文件没有溢出元数据，只有左上角一格有值且不报错。
  查找用 `INDEX`/`MATCH`；排序、过滤、去重在 Python 里做完再写单元格。

## openpyxl gotchas

- **读模型要两遍加载**：`load_workbook(path, data_only=True)` 得到计算缓存值
  （公式消失）；默认加载得到公式字符串（没有值）。一遍拿不到两者。
- **`data_only=True` 的 workbook 不能再保存**——保存会把所有公式永久替换为字面值。
- **openpyxl 刚写完的文件用 `data_only=True` 读全是 `None`**（没有缓存值，见上）。
- **合并单元格只写左上锚点**，其余是只读的 `MergedCell`。
- **`.xlsm` 保宏**必须 `load_workbook(path, keep_vba=True)`。
- **表名带空格的跨表引用要加引号**：`='Assumptions Inputs'!$B$5`，不带引号 `#VALUE!`。
- 大数据量（>10 万行）用 `pandas.to_excel` 或 `ws.append(list_like)` 逐行写，
  不要逐单元格赋值。

## 数字与格式约定（默认口径，用户另有说明则从之）

- 百分比**存小数**（存 `0.15` 显示 `15.0%`；存 `15` 会显示 `1500.0%`），格式串 `0.0%`。
- 数字格式：千分位 `#,##0`；零显示 `-`（`#,##0;(#,##0);-`）；负数加括号。
- 单位写进列头（`产率 (%)`、`Mn (g/mol)`），不要堆在数值里。
- 输入单元格蓝字 `(0,0,255)`、公式黑字、跨表引用绿字 `(0,128,0)`、
  待用户填写的关键假设黄底。

## 交付约定

保存到 `output/`，完成后用 `file.send` 交工作区相对路径；多工作表时说明每表内容。
