"""AI4MS 专业工具 MCP 网关（方案 §10 / P7）。

把 poly_agent / spec_agent（核磁工具与谱图异步解析）/ sciverse 的上游 HTTP API
包装成 MCP 工具，供学生本地 ScienceDiscovery 注册使用。上游凭证由网关集中
持有（环境变量），永不下发到学生 SD。
"""
