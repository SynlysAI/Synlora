---
name: spec-spectra
description: 谱图解析异步任务（NMR / IR / GPC / Raman / LC-MS）。用户要求解析谱图文件、给出谱峰或分子量分布时使用；用 job.submit 提交，完成后系统会通知你。
version: "1.0"
author: AI⁴MS
tags:
  - 谱图
  - 异步任务
---

# 谱图解析异步任务

把工作区里的**谱图文件**提交给 Spec_Agent 做解析。任务在后台跑，完成后系统会
自动通知你继续处理——**提交后不要反复查询，也不要重复提交同一文件**。

## 用法

用 `job.submit` 提交，`kind` 按下表选，`params` 至少给 `path`：

```
job.submit(
  kind="spec.task.nmr",
  params={"path": "files/sample.nmr"},
  label="样品 A 的核磁解析")
```

- `path`：**工作区内的相对路径**（用 `file.list` 查看有哪些文件）
- `params.params`（可选）：透传给上游的任务参数对象

## 五种任务

| kind | 谱图类型 | 说明 |
|---|---|---|
| `spec.task.nmr` | 核磁共振（NMR） | 峰检测与积分；可给 `nucleus`、`threshold`、`min_distance` 等峰检测参数 |
| `spec.task.ir` | 红外（IR） | 官能团与谱峰归属 |
| `spec.task.gpc` | 凝胶渗透色谱（GPC） | 分子量与分子量分布 |
| `spec.task.raman` | 拉曼（Raman） | 拉曼峰位与归属 |
| `spec.task.lcms` | 液质联用（LC-MS） | 色谱峰与质谱解析 |

## 其他操作

- 查进度：`job.status(job_id)`（只在用户主动问、或完成通知信息不足时用）
- 列任务：`job.list()`
- 取消：`job.cancel(job_id)`——**上游无取消接口**，取消是本地停止跟踪，
  上游任务仍会跑完；所以请在提交前确认文件选对了

## 什么时候用同步三件套

`spec.nmr.forward` / `spec.nmr.reverse` / `spec.nmr.search` 是**同步**的分子结构
预测与库检索（输入 SMILES 或化学位移，不走文件），适合快速试算；本技能是**异步**
的谱图文件解析，适合跑真实谱图数据。按用户诉求选：给了文件用这里，给了结构或
位移串用同步三件套。
