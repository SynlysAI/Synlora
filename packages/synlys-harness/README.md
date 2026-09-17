# synlys-harness

SynlysAgent 的核心 Agent 运行时（纯 Python，零 Web 依赖）。

## 架构

- `types.py`：核心数据模型（事件、消息、工具定义、Agent 配置）
- `events.py`：`EventLog` 事件日志（seq 分配 + sinks 广播）
- `session.py`：`derive_messages()` 事件 → LLM 消息投影（含连续 tool_call 聚合、孤儿 call 补合成结果）
- `tools/`：注册表、四段执行管线、`ExecutionRequest` 通用执行协议、本机/Docker 执行器及通用工具（file.* / python.run / shell.run / skill.* / job.*）
- `models/`：LLM 后端协议与 OpenAI 兼容流式实现（tool-call delta 聚合）
- `agent.py`：`RunSession`（turn/step 循环、steering、cancel、扩展钩子、LLM 错误映射）

## 核心约定

- 事件流是唯一事实源：所有进入 LLM 上下文的内容都先落 session 事件
- 工具 = 声明 + 管线：权限/超时/截断统一在管线处理，注册工具只写 schema 与 execute
- `RunSession.run()` 每轮对话调用一次；cancel 每轮自动重置；宿主消费建议用 `contextlib.aclosing` 包裹
- local `python.run` 是可信开发事故围栏，不是安全边界；`shell.run` 只在 Docker 模式提供，Docker 不可用时绝不降级到宿主 shell
- `RunSession.run(input_metadata=...)` 只接受宿主元数据；`text`/`attachments` 为保护字段
- 工具审批回调必须返回 `ApprovalDecision`，字符串等旧返回类型一律拒绝

## 执行协议

`CodeExecutor.execute(ExecutionRequest)` 接收 argv、工作区、相对 cwd、只读资源、超时和输出上限。调用方不能让模型指定镜像、挂载、环境变量、网络或用户。Docker 每次创建临时容器，工作区挂载为读写，技能目录逐项挂载为只读；`CodeExecutor.run()` 和 `run_python()` 继续作为 Python 兼容入口。

## 最小用法

```python
from synlys_harness import (
    AgentConfig, EventLog, ModelProviderConfig, OpenAICompatibleBackend,
    RunSession, ToolPipeline, ToolRegistry, register_builtin_tools,
)

registry = ToolRegistry()
register_builtin_tools(registry)
log = EventLog(sinks=[...])          # 宿主接持久化/SSE
backend = OpenAICompatibleBackend(ModelProviderConfig(
    name="local", base_url="http://localhost:8000/v1",
    api_key="sk-...", model_id="qwen3-32b",
))
session = RunSession(
    config=AgentConfig(system_prompt="你是科研助手", tool_names=["python.run"]),
    registry=registry, pipeline=ToolPipeline(registry=registry),
    backend=backend, event_log=log, user_id="u1", run_id="r1",
)
async for event in session.run("帮我算 1+1"):
    print(event.type, event.payload)
```

## 测试

```bash
conda run -n synlysagent python -m pytest packages/synlys-harness/tests -v
```
