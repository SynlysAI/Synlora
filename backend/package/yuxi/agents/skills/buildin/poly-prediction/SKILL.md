---
name: poly-prediction
description: '高分子垂类模型预测：聚酰亚胺 Tg、氟基电解质配方、Raman 光谱解析、共聚竞聚率拟合、硅键谱图集成评分。用户要预测高分子性能、解析高分子 Raman 谱、拟合竞聚率时使用。'
version: "1.0"
tool_dependencies: ["poly_tg_predict", "poly_electrolyte_predict", "poly_raman_analyze", "poly_reactivity_fit", "poly_silicon_bond"]
---

# 高分子垂类预测

五个模型工具的分工与输入要求：

## poly_tg_predict：聚酰亚胺玻璃化转变温度
- 输入：单个 SMILES 字符串；多个分子请多次调用。
- 输出：Tg 预测值（℃）与模型版本。

## poly_electrolyte_predict：含氟电解质配方预测与推荐
- 输入：`formulations` 配方数组，每个元素包含 lithium_salt、lithium_salt_mol_L、
  electrolyte_component_1/2/3 与对应 _mol_ratio、monomer_1/2 与对应 _mass_ratio、
  electrolyte_mass_ratio_in_polymer_mix、filler_type、filler_wt_pct；
  无对应组分时传空串或 0，不要删字段。
- `recommendation_limit` 控制推荐配方数（默认 5）。

## poly_raman_analyze：Raman 光谱结构解析
- 输入：谱图文件的工作区相对路径 `path`（txt/dat/csv/xlsx，≤10MiB）。
- `mode` 默认 function_groups（官能团识别）；波数区间 x0/x1 可选。

## poly_reactivity_fit：共聚竞聚率拟合
- 输入：`data_rows` 实验数据行数组，每行含「比例」（投料比，如 "2:8"）、
  「转化率1」「转化率2」（如 "2.53%"）。

## poly_silicon_bond：硅键谱图集成评分
- 输入：`sample` 对象（五条对齐光谱 + 组成信息），按用户提供的结构原样组装透传。

## 使用纪律
- 上游同步执行模型推理，可能需要数分钟：一次只调一个工具，不要并发重复提交。
- 输入不完整时先向调用方确认并列出缺失项，不要用 0 或空值凑数。
- 预测结果是模型估计：输出时给出数值、单位与模型版本，提醒结合实验验证，
  不要臆造模型没有给出的结论；结果文件写 outputs/ 并在回复中给出相对路径。
