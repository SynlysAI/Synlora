---
name: spec-spectra
description: 谱图解析异步任务（NMR / IR / GPC / Raman / LC-MS）。用户要求解析谱图文件、给出谱峰或分子量分布时使用；用 job.submit 提交，状态在运行信息中显示。含五种任务的完整参数表与默认值。
version: "1.3"
author: AI⁴MS
tags:
  - 谱图
  - 异步任务
---

# 谱图解析异步任务

把工作区里的**谱图文件**提交给 Spec_Agent 做解析。任务在后台运行，状态在右侧
运行信息中显示；需要继续分析时由用户主动要求查询。**不要重复提交同一文件**。

## 调用方法

```
job.submit(
  kind="spec.task.raman",              # 任务类型，见下表
  params={
    "path": "files/sample.txt",        # 必填：工作区内的相对路径
    "params": {"x0": 200, "x1": 3500}, # 可选：透传给上游的任务参数对象
  },
  label="样品 A 的拉曼解析")            # 可选：给用户看的简述
```

- `path`（**必填**）：工作区内的相对路径（用 `file.list` 看有哪些文件）。平台会自动
  把文件上传给上游换取 file_id，你不需要关心上传。
- `params.params`（可选）：**嵌套的任务参数对象**。里面的字段名必须用上游定义的
  参数名（见各任务小节）——**不要自己发明字段名**，自造字段会被上游静默忽略并套用
  默认值。
- `label`（可选）：任务简述，用于向用户展示。

失败时 `job.status` 会返回上游给出的失败原因（含错误码），先看原因再决定要不要重试。

## 五种任务

| kind | 谱图类型 |
|---|---|
| `spec.task.nmr` | 核磁共振（NMR） |
| `spec.task.ir` | 红外（IR） |
| `spec.task.gpc` | 凝胶渗透色谱（GPC） |
| `spec.task.raman` | 拉曼（Raman） |
| `spec.task.lcms` | 液质联用（LC-MS） |

---

## spec.task.nmr（核磁）

`params.params` 全部可选，默认值如下：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `nucleus` | `"1H"` | 核类型：`1H` / `13C` |
| `threshold` | `0.01` | 峰检测阈值（大于 0） |
| `min_distance` | `0.3` | 最小峰距（大于 0） |
| `min_prominence` | `0.01` | 最小显著性（大于 0） |
| `width_multiplier` | `1.0` | 峰宽倍率 |
| `baseline_degree` | `3` | 基线拟合阶数（1–10） |
| `smooth_window` | `5` | 平滑窗口（1–99） |
| `enable_multiplet` | `true` | 是否启用多重峰聚合 |
| `max_coupling_hz` | `20.0` | 多重峰最大耦合常数阈值 |
| `detection_range_mode` | `"full"` | `full` / `custom` |
| `detection_range_min` | 无 | 检测范围下限（`custom` 时给） |
| `detection_range_max` | 无 | 检测范围上限（`custom` 时给） |
| `ppm_offset` | `0.0` | ppm 偏移 |
| `integration_method` | `"voigt"` | 积分方法：`voigt` / `trapezoid` |
| `internal_standard_policy` | `"auto"` | 内标策略，固定 `auto` |
| `internal_standard_prefer` | `["solvent","tms"]` | 内标优先级 |

```
job.submit(kind="spec.task.nmr", params={
  "path": "files/sample.nmr",
  "params": {"nucleus": "13C", "threshold": 0.02}})
```

---

## spec.task.ir（红外）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `mode` | `"greedy_decode"` | 分析模式，IR 四种都支持 |
| `k` | `3` | 候选数量（1–10），仅 `beam_search` / `retrieval` 有效 |
| `x0` | `400.0` | 分析范围起点 |
| `x1` | `4000.0` | 分析范围终点（必须大于 `x0`） |
| `transmittance` | `false` | 是否把透射率转成吸光度（仅 IR 支持） |
| `device` | `"auto"` | 推理设备：`cpu` / `cuda` / `auto` |

`mode` 可选：`greedy_decode`（Top1 结构预测）/ `beam_search`（Top-K 候选）/
`retrieval`（库检索）/ `function_groups`（官能团识别）。

```
job.submit(kind="spec.task.ir", params={
  "path": "files/sample.ir",
  "params": {"transmittance": true}})
```

---

## spec.task.raman（拉曼）

参数与 IR 相同，但有**两个必须注意的差异**：

| 参数 | 实际默认值 | Raman 注意事项 |
|---|---|---|
| `mode` | `"function_groups"` | 平台兜的默认值，见下（上游自己的默认值是 Raman 不支持的） |
| `k` | `3` | 同 IR |
| `x0` / `x1` | `400.0` / `4000.0` | 同 IR |
| `transmittance` | `false` | **Raman 不能设为 `true`**（会报错） |
| `device` | `"auto"` | 同 IR |

### Raman 的 mode（平台已兜默认值）

上游 `mode` 的默认值是 `greedy_decode`，**而 Raman 的实现不支持它**——不指定
就会失败。所以平台在你没给 `mode` 时**会自动补 `function_groups`**：

**Raman 直接提交即可，不需要手动传 `mode`**：

```
job.submit(kind="spec.task.raman",
           params={"path": "files/sample.txt"},
           label="样品 A 的拉曼解析")
```

如果要换解析方式，可以显式指定：

| mode | 可用性 | 说明 |
|---|---|---|
| `function_groups` | ✅ 平台默认值 | 官能团识别 |
| `retrieval` | ⚠️ 资源齐备但未实测 | 库检索，配合 `k` 用 |
| `beam_search` | ❌ 当前部署缺模型文件 | 需要 `raman_generation.pth`，该文件缺失 |
| `greedy_decode` | ❌ 上游不支持 | 显式传了也会失败（平台不会替你改掉显式传的值） |

失败时先看 `job.status` 返回的原因再决定要不要改参数重试，不要原样重提。

---

## spec.task.gpc（凝胶渗透色谱）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `detect_mode` | `"auto"` | 峰检测模式：`auto` / `manual` |
| `manual_interval` | 无 | `manual` 时**必填**，形如 `[start, end]`（如 `[7.2, 8.9]`） |
| `three_color_arw_file_ids` | 无 | 三色曲线文件 ID，传则必须**传满 3 个** |
| `calibration_file_id` | 无 | 校准文件 ID |
| `comparison_report_pdf_file_id` | 无 | 对比报告 PDF 文件 ID |
| `source_file_name` | 无 | 上传的原始文件名（用于三色匹配） |

除 `detect_mode` 外都可省略；用默认值即可跑通常规单文件解析。

```
job.submit(kind="spec.task.gpc", params={"path": "files/sample.txt"})
```

---

## spec.task.lcms（液质联用）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `source_file_name` | 无 | 上传的原始文件名 |

基本无可调参数，通常直接提交即可：

```
job.submit(kind="spec.task.lcms", params={"path": "files/sample.raw"})
```

---

## 关于 `spectype`

IR 与 Raman 共用同一套上游接口，`spectype`（`ir` / `raman`）由平台按 `kind`
自动填好，**你不要自己传**。

## 其他操作

- 查进度或结果：`job.status(job_id)`（用户主动问或需要在当前对话继续处理时用）。
  失败时会带上上游给出的错误原因。
- 列任务：`job.list()`
- 取消：`job.cancel(job_id)`——**上游无取消接口**，取消是本地停止跟踪，
  上游任务仍会跑完；所以请在提交前确认文件与参数选对了

## 什么时候用同步三件套

`spec.nmr.forward` / `spec.nmr.reverse` / `spec.nmr.search` 是**同步**的分子结构
预测与库检索（输入 SMILES 或化学位移，不走文件），适合快速试算；本技能是**异步**
的谱图文件解析，适合跑真实谱图数据。按用户诉求选：给了文件用这里，给了结构或
位移串用同步三件套。
