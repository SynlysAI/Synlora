---
name: data-analysis
description: 对工作区里的数据文件做统计分析并出图（CSV/Excel/JSON）。用户说"分析数据""画个图""算一下"时使用。
version: "1.0"
author: SynlysAgent
tags:
  - 统计
  - 可视化
---

# 数据分析

## 目标
把 `files/` 里的数据文件变成结论与图表，产物落在 `output/`。

## 工作流
1. 用 `file.list` 看 `files/` 下有哪些数据文件
2. 用 `python.run` 读入（pandas），先打印形状 / dtype / 缺失值
3. 按需求做统计或作图，图存 `output/`
4. 回复给出结论 + 产物的工作区相对路径

## 决策规则
- 找不到数据文件时先问用户，不要凭空生成数据
- 画图中文标签要显式指定字体，否则乱码

## 输出要求
结论先行、附关键数字；产物给 `output/xxx.png` 路径
