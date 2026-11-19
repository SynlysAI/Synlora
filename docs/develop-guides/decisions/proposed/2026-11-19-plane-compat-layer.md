# 以 yuxi 分支实现 Plane(OpenEVA) 兼容面，替换老平台 Synlora

状态：proposed
类型：feature
Owner：backend/server/routers/plane_compat_router.py

## 问题

Plane(OpenEVA，`SynlysAI/plane` fork) 已通过 HTTP 契约深度集成老平台 Synlora
（Synlora-main develop 1.16.1，2026-10-14 ~ 11-16 的提交：联邦登录、委托令牌、
Agent Context v3、工作区会话、RWS 共享执行、审批）。现在要用 yuxi 分支
（开源 Yuxi 架构，PostgreSQL/ARQ/LangGraph）完全替换老平台，约束条件：

- **Plane 侧零代码改动**，最多修改 `SYNLORA_BASE_URL` 等集成配置；
- 不迁移老平台历史数据（会话、事件、绑定、PAT 一律重新开始）；
- 用户在 yuxi 上重新创建 `yxkey_` 个人密钥并在 Plane 重新绑定；
- yuxi 必须支持 OpenEVA 账号登录与本方账号登录（与老平台一致）。

两条分支零共同祖先，无法 cherry-pick，只能按契约在 yuxi 架构上重实现。

## 提案

在 yuxi 分支新增 `plane-compat` 兼容层，按 Plane 已上线调用面
（`plane/research/services/agent_runtime.py`、`plane/app/views/assistant/client.py`）
逐条实现协议，分三个切片交付：

1. **身份与凭证地基（本次已实现）**：`/api/health`（带 deployment_id）、
   `/api/v1/auth/me`、`/api/v1/auth/plane-login`（三步登录代理）、
   `/api/v1/research/delegated-token`、`/api/v1/research/account-links`、
   `plane_account_links` 表（schema v10）。
2. **v1 Sessions 兼容层（后续）**：会话/运行/事件端点全家桶、durable 事件表与
   事件字典转换、`run_id` 同步创建与 `external_run_id` 对账、审批桥。
3. **运行时集成（后续）**：research context 校验、page_context 注入、
   `pum_` unified MCP 客户端、RWS 共享执行。

### 实现方案（切片 1）

| 模块 | 职责 |
|---|---|
| `server/routers/plane_compat_router.py` | 兼容面 HTTP 端点、请求体 schema_version 冻结校验、服务凭证校验（`PLANE_SERVICE_TOKEN`，字节级常数时间比较，失败 403）、响应装配 |
| `package/yuxi/services/plane_auth_service.py` | 配置（`PLANE_*` env）、deployment_id、Plane 浏览器登录三步代理（错误码 5900→429/5000→503/5018→403/5019→401，其余 401 防枚举）、身份收敛（登录/委托共用）、绑定补账状态机、Plane 身份建号、令牌签发 |
| `package/yuxi/repositories/plane_account_link_repository.py` | 绑定记录查询与写入 |
| `package/yuxi/storage/postgres/models_business.py` | `PlaneAccountLink` 模型（core 分流、source；`plane_email`/`synlora_username` 为切片 P1 自服务绑定端点的既定消费字段，FK 级联 CASCADE） |
| `storage_migration.py` + `manager.py` | business schema v9→v10：`upgrade_plane_compat_schema()` 原子建表并记录版本 |

关键语义决策：

- **Plane 身份 = 真实 users 行**（uid=`plane:<pid>`，首登自动建号，默认部门
  `PLANE_DEFAULT_DEPARTMENT`），复用 OIDC 联邦先例；不引入老平台的虚拟
  subject（`plane:<pid>` 字符串直接作为会话 owner），保住全部外键、FIFO 与
  权限链。令牌沿用本方 JWT（Plane 从不解析令牌内容，只透传）。
- **core 分流照搬老平台**：`core=ai4ms`（缺省）→ OpenEVA 登录与委托令牌收敛
  到绑定账户；`core=plane` → 保持 Plane 身份只合并会话可见性（别名查询在
  切片 2 接入会话列表时实现）。绑定账户删除时回落 Plane 身份。
- **响应形状逐字段对齐老平台**（`subject`/`linked_user_id`/`expires_in`/
  `sessions_backfilled` 等），错误码与文案口径一致（403 服务凭证、409 绑定
  冲突、429/502/503 登录代理）。
- `delegated-token` 在无绑定时即时建号（老平台是虚拟身份不落库）；这是
  Design B 的必然结果，Plane 无感知。

## 替代方案

- **在老平台 develop 上继续演进**：功能最全但架构弃用，与替换目标矛盾。
- **数据迁移保持连续性（PAT 哈希/绑定/历史会话迁移 + deployment_id 同值）**：
  可做到用户无感，但用户已明确接受重新绑定与丢弃历史，砍掉整个迁移工作流
  换取更小的变更面。
- **虚拟 subject（老平台式 `plane:<pid>` 字符串身份）**：需放宽 users 外键与
  权限链，破坏 yuxi 架构不变量；真实用户行以一个自动建号代价换掉全部特例。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| `/api/health` 返回 `deployment_id` | Plane 绑定范围约束失效 | plane_compat_router | unit: test_health_returns_deployment_id_and_version | — | Passed |
| 登录代理错误码映射 | 限流/禁用被误报为密码错误 | plane_auth_service | unit: test_authenticate_maps_* | 5900→429、5018→403、5019→401、其余 401 | Passed |
| 302 无 session cookie 判定失败 | 伪造成功响应 | plane_auth_service | unit: test_authenticate_rejects_redirect_without_session_cookie | 502 | Passed |
| 委托令牌按 core=ai4ms 收敛 | 身份错绑 | plane_compat_router | unit: test_delegated_token_converges_to_bound_account | 已收敛时不回落 | Passed |
| 无绑定委托令牌即时建号 | 401 循环 | plane_compat_router | unit: test_delegated_token_provisions_plane_identity_when_unbound | linked_user_id 为空 | Passed |
| 服务凭证缺失/错误拒绝 | 未授权建绑定 | plane_compat_router | unit: test_delegated_token_rejects_missing_service_credential | 403 | Passed |
| 补账幂等/冲突语义 | 重复或错绑 | plane_auth_service | unit: test_repair_*（5 例，service 层） | 用户不匹配 403、一对多 409、绑定冲突 409 | Passed |
| core 分流收敛/回落/建号语义 | 身份错绑或 401 循环 | plane_auth_service | unit: test_login_resolution_*、test_delegated_resolution_*、test_get_or_create_*、test_unique_plane_username_* | 绑定删除回落、core=plane 跳过、用户名碰撞加后缀 | Passed |
| 服务凭证非 ASCII 拒绝 | TypeError 成 500 | plane_compat_router | unit: test_delegated_token_rejects_non_ascii_bearer_without_500 | 403 | Passed |
| 请求体 schema_version 冻结 | 未知协议被静默接受 | plane_compat_router | unit: test_delegated_body_rejects_unknown_schema_version、test_repair_body_* | 422 | Passed |
| schema v9→v10 存量升级 | 已部署库无法启动新代码 | storage_migration + manager | unit: test_supported_legacy_business_schema_is_converged_and_versioned_as_current[9] | 非支持版本拒绝 | Passed |
| 路由不直接执行持久化 | 架构不变量破坏 | verify_engineering_contracts | `python3 scripts/verify_engineering_contracts.py` | router 内 db.execute 被拒 | Passed |
| 全量 unit 无回归 | — | — | `docker compose exec api pytest test/unit -m "not slow"` | — | Passed |
| Plane 侧真实对账可用 | 契约形状偏差 | agent_runtime.py 调用面 | 切片 2 完成后 integration/E2E | verify_synlora_api_key、delegated-token 交换 | Not run |

## 风险

- **存量失败**：`test/unit/agents/test_builtin_discovery.py::test_builtin_skill_tool_dependencies_resolve`
  在未含本变更的工作树上同样失败（deep-research 依赖未注册的 `web_search`），
  属 yuxi 分支既有问题，需另行修复，不阻塞本提案。
- **共享出口 IP 限流**：全员经本服务代理 OpenEVA 登录时共享 Plane 侧
  10 次/分钟/IP 配额，高峰可能 429（老平台同样存在，已知行为）。
- **deployment_id 必须显式固定**：未配置 `SYNLORA_DEPLOYMENT_ID` 时按
  `YUXI_INSTANCE_ID` 派生，开发环境该值随进程漂移会导致 Plane 侧绑定失配；
  接入 Plane 的部署必须在 .env 中显式设置。
- **委托令牌即时建号**：任何配置了集成的工作区用户首次触发委托调用都会在
  本方建号；若不希望如此，需要后续引入白名单——当前按 OIDC auto_create 语义
  保持开放。
- 切片 2 的 `run_id` 同步创建与 durable 事件表是 Plane 30s 对账与断线续传的
  硬前提，实现时需 E2E 验证。
