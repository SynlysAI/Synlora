# 公共 MCP（catalog 第 4 类条目）

位置即类型：`mcp/<id>/mcp.json` 即一个公共 MCP 服务，由 `app/catalog/loader.py::scan_catalog` 随启动扫入。

## manifest 字段

```json
{
  "id": "filesystem",
  "name": "文件系统",
  "description": "受限目录的文件读写工具",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "timeout_s": 60
}
```

- `id`：kebab-case，= 目录名，全局唯一（与用户自建 MCP 同一 id 空间，同名时用户自建优先）
- `transport`：`stdio`（`command` 必填，可带 `args` / `cwd` / `env`）或 `streamable-http`（`url` 必填，可带 `headers` / `bearer_token`）
- `timeout_s`：单次调用超时秒数（缺省 60）
- 凭证（headers / bearer_token / env）直接写 manifest——管理员维护、部署级信任，与插件 manifest 同级

## 覆盖与恢复

- 管理后台「MCP 服务」页编辑内置条目 = 同名写覆盖副本到 `{data_dir}/public/catalog/mcp/`（数据目录版优先，仓库文件不动）
- 「恢复默认」= 删覆盖副本回退仓库版；「删除」仅对有覆盖副本的条目开放
- 写入即热重载生效，无需重启
