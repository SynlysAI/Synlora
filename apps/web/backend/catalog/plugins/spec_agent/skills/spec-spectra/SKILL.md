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
- `params.params`（可选）：透传给上游的**任务参数对象**。它是嵌套的 JSON 对象，
  字段名必须用上游定义的参数名——**不要自己发明字段名**（写
  `{"function_groups": true}` 这类自造字段不会被识别，上游会忽略它并套用默认值）。

```
job.submit(
  kind="spec.task.raman",
  params={"path": "files/sample.txt", "params": {"mode": "function_groups"}},
  label="样品 A 的拉曼解析")
```

## 五种任务

| kind | 谱图类型 | 参数 |
|---|---|---|
| `spec.task.nmr` | 核磁共振（NMR） | 可给 `nucleus`、`threshold`、`min_distance` 等峰检测参数 |
| `spec.task.ir` | 红外（IR） | — |
| `spec.task.gpc` | 凝胶渗透色谱（GPC） | — |
| `spec.task.raman` | 拉曼（Raman） | **必须给 `mode`**，见下 |
| `spec.task.lcms` | 液质联用（LC-MS） | — |

### Raman 必须显式指定 mode（重要）

上游 Raman 的参数默认值是 `mode="greedy_decode"`，**但 Raman 实现不支持它**——
不传 `mode` 一定失败，返回 `暂不支持Raman的greedy_decode模式`。所以 Raman 任务
**每次都要在 `params.params.mode` 里显式指定**：

| mode | 说明 |
|---|---|
| `function_groups` | 官能团归属（实测可跑通；谱图特征不明显时可能返回空结果） |
| `beam_search` | 候选式解析，可配合 `k` 指定候选数 |
| `retrieval` | 库检索式解析，可配合 `k` |

**不要传 `greedy_decode`**（Raman 不支持，必失败），也不要原样重试——上一次
失败的原因会出现在 `job.status` 里，先看原因再决定怎么改。

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
