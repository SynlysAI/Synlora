# AI4MS 专业工具 MCP 网关

把 poly_agent / spec_agent（核磁工具 + 五类谱图异步解析）/ sciverse 的上游
HTTP API 包装成 MCP 工具，供学生本地 ScienceDiscovery 经 custom MCP 调用。
上游凭证由网关集中持有，永不下发到学生 SD。

## 架构

直接复用 `catalog/plugins/*/tools.py` 的工具函数（登录、轮询、截断、错误
归一化零移植漂移），仅做 MCP 命名 sanitize（点号→下划线）与凭证注入；
谱图异步任务复用 `SpectraTaskConnector`（上传→提交→轮询→取结果）。

## 运行

```bash
cd apps/web/backend
MCP_GATEWAY_AUTH_TOKEN=<令牌> \
SPEC_AGENT_BASE_URL=http://10.26.15.93:<port> SPEC_AGENT_TOKEN=<token> \
POLY_AGENT_BASE_URL=http://10.26.15.93:8003 POLY_AGENT_USERNAME=<u> POLY_AGENT_PASSWORD=<p> \
SCIVERSE_BASE_URL=<地址> SCIVERSE_API_TOKEN=<token> \
python -m mcp_gateway.run_uvicorn
```

网络位置：需同时可达 AI⁴MS 内网服务与公网 Sciverse（校园内网部署）。

## 注册到学生 SD

```bash
python -m mcp_gateway.register_sd --sd-base-url http://192.168.1.10:5300 \
  --sd-token <SCIENCE_AGENT_AUTH_TOKEN> --gateway-url http://<campus-host>:8090/mcp
```

慢工具（谱图异步 350s+）以 `timeoutSeconds=600` 注册。注册后 SD 侧
specialist 可通过 connectorIds 引用网关工具。
