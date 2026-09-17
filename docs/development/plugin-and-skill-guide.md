# Synlora 插件、技能与后台任务开发指南

## 职责分层

- `synlys_harness`：Agent loop、事件、工具管线、审批类型、任务状态和通用进程执行协议，不含科研产品身份、SearXNG 或外部平台连接器。
- Web 宿主：用户身份、能力授权、会话插件开关、技能来源解析、工作区、任务持久化和运行装配。
- catalog 内容：随产品发布或由管理员部署的专家、技能和插件包。

## 部署目录

仓库内容放在 `apps/web/backend/catalog/`，部署私有扩展放在 `{DATA_DIR}/public/catalog/`。后者同 ID 覆盖仓库版本。新增包后重启后端，再配置目录策略、插件配置和用户安装/会话启用。

插件代码属于管理员可信部署内容。用户安装市场插件只改变授权记录，不允许上传或执行 Python 插件代码。用户自建技能可以包含说明，但附属脚本应由可信文件部署流程放置，并且只在 Docker 沙箱执行。

## 插件 manifest

必填字段为 `id`、`name`、`version`。插件必须至少贡献工具模块、连接器模块、技能或专家之一；声明的模块和技能必须真实存在，错误包会在扫描阶段被拒绝。

工具插件：

```json
{
  "id": "example_tools",
  "name": "示例工具",
  "version": "1.0.0",
  "tools_module": "tools.py"
}
```

外部任务连接器插件：

```json
{
  "id": "example_jobs",
  "name": "示例外部任务",
  "version": "1.0.0",
  "connectors_module": "connectors.py"
}
```

`connectors.py` 从 `app.plugins.contracts` 导入 `JobConnector` 相关异常和注册契约，并导出 `CONNECTORS = [...]`。连接器用于外部科研服务，负责 `submit/poll/cancel` 和状态映射；不要用它包装平台内置沙箱任务。

纯技能/专家插件：

```json
{
  "id": "example_content",
  "name": "示例内容包",
  "version": "1.0.0",
  "skills": ["example-skill"],
  "expert": {
    "name": "示例专家",
    "system_prompt": "按示例技能完成任务。",
    "tool_whitelist": ["skill.list", "skill.read", "python.run"]
  }
}
```

插件技能放在 `skills/<name>/SKILL.md`，不会复制到独立公共技能根。

## 技能包

```text
example-skill/
  SKILL.md
  scripts/run.py
  references/protocol.md
  assets/template.csv
```

`SKILL.md` frontmatter 必须提供与目录一致的 kebab-case `name` 和非空 `description`。运行期一次解析出正文、元数据和来源目录；同名覆盖后的脚本与说明始终来自同一胜出目录。包内符号链接和 reparse 跳转会使技能被排除。

模型用 `skill.read(name)` 读取正文，用 `skill.read(name, path)` 读取包内 UTF-8 文本参考资料。返回的 `resource_root` 是本轮实际路径：Docker 为 `/skills/<name>`，本机可信开发为宿主路径。技能根只读，产物必须写到工作区 `output/`。只读绑定不是内容快照，管理员不要原地更新正在执行的公共技能。

## 前台执行

- `python.run`：兼容原接口；Docker 与 local 均可用，local 不提供只读隔离承诺。
- `shell.run`：只在 Docker 模式提供，调用间不保留 `cd`、环境变量、后台进程或终端状态。
- Docker 工作区为 `/workspace`；本轮获准技能分别挂载到 `/skills/<name>`。

通用执行环境不会继承插件配置、AI⁴MS 凭证或模型服务密钥。依赖由管理员预先构建进镜像，不允许模型运行期提权、换镜像、加挂载或开放网络。

## 统一后台任务入口

`job.submit(kind, params, label)` 同时支持平台沙箱任务和外部插件任务：

```json
{"kind":"sandbox.python","params":{"code":"print(sum(range(1000000)))","cwd":"tmp"},"label":"后台计算"}
```

```json
{"kind":"sandbox.shell","params":{"command":"python /skills/data-analysis/scripts/summarize_csv.py --input /workspace/files/data.csv --output /workspace/output/summary.json"},"label":"生成摘要"}
```

```json
{"kind":"sandbox.skill","params":{"skill":"data-analysis","script":"scripts/summarize_csv.py","args":["--input","/workspace/files/data.csv","--output","/workspace/output/summary.json"]},"label":"运行数据技能"}
```

```json
{"kind":"spec.task.nmr","params":{"path":"files/sample.nmr"},"label":"解析核磁谱图"}
```

平台任务不要求插件 ID；它们检查本轮 `python.run`/`shell.run` 和技能可见性。外部任务检查所属插件的用户授权与会话开关。`sandbox.skill` 只执行 `scripts/` 下明确的 `.py` 或 `.sh` 文件，不把 `SKILL.md` 当作可执行工作流。

提交只负责校验、登记和交接，立即返回 job ID。任务拥有独立超时；停止当前回答或 SSE 断连不会停止 Job。取消使用 `job.cancel` 或 `POST /api/v1/jobs/{id}/cancel`。平台任务只有确认容器停止后才记为 cancelled；外部连接器没有真实取消能力时，上游可能继续执行。

任务完成、失败或取消后只更新 Job 文档和右侧“运行信息”，不会自动向聊天区注入消息，也不会自动发起新的 Agent run。用户可直接查看面板，或在后续对话中要求 Agent 调用 `job.status` / `job.list` 查询并总结结果。

活跃平台任务会阻止关联会话或项目删除并返回 409。正常停机先停止轮询，再清理后台容器，最后关闭数据库；重启只清理当前 `SANDBOX_DEPLOYMENT_ID` 的遗留容器，并将任务标为 `failed/process_interrupted`，不会重跑代码或改动外部任务。后台日志采用容器轮转并只保存尾部 64 KiB，不提供实时无限日志流。

所有新 Job 文档必须显式写 `backend: sandbox|external`。当前为开发内测版本，不兼容缺少 `backend` 的旧任务文档，也不保留旧的连接器协议导入路径。

## 部署注意

宿主传给 Docker daemon 的技能和工作区路径必须在 daemon 视角可见。当前不支持远程 daemon；如果后端将来容器化，必须提供 daemon 可见的同一源路径。源码部署必须同时携带 `app/`、`catalog/`、产品提示词和前端 `dist/`；当前 wheel 配置只包含 `app`，不能宣称 wheel 可独立部署完整产品。
