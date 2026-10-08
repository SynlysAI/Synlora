---
name: sciverse-search
description: 'Sciverse 科技文献检索：智能语义检索、meta 结构化检索、文献全文分页读取与附件下载。用户要检索科学文献、查全文、下载原文附件时使用。'
version: "1.0"
tool_dependencies: ["sciverse_agentic_search", "sciverse_content", "sciverse_resource", "sciverse_meta_catalog", "sciverse_meta_search"]
---

# Sciverse 文献检索

## 检索策略
1. 开放式问题（综述、找相关工作）→ `sciverse_agentic_search` 语义检索；
   可用 `sub_queries`（0–4）让上游自动拆解子查询。
2. 精确条件（年份、字段筛选、排序）→ 先 `sciverse_meta_catalog` 查字段定义，
   再 `sciverse_meta_search` 结构化筛选（filters/sort/fields/page/page_size/cursor）。
3. 读到片段后用 `sciverse_content` 按 `offset` 分页取全文（limit 默认 700，最大 50000）。
4. 需要原文附件时用 `sciverse_resource`，给 `save_relative_path`（如 outputs/paper.pdf）
   保存到工作区后把路径告诉调用方。

## 使用纪律
- `query` 与 `sort` 互斥；`cursor` 与 `page > 1` 互斥。
- 检索范围不明确时先向调用方确认主题、时间范围与字段口径。
- 检索不到或证据不足时如实说明，不要编造文献。
- 回答时给出每条结论的文献来源（doc_id / 标题）。
