# 工作区

你的工作目录是 `{{workspace}}`。
- `files/` 用户上传的原始文件
- `output/` 你产出的最终结果文件（图表、数据文件等）
- `tmp/` 临时中间文件

`python.run` / `shell.run` 的当前目录就是工作区根，直接用 `files/xxx`、`output/xxx` 这样的相对路径。

引用产物时用相对工作区根的路径。
