# API Key 支持 reveal 重新获取完整密钥

状态：implemented
类型：feature
Owner：backend/server/routers/user_router.py

## 问题

页面创建 API Key 后完整密钥仅在创建响应中返回一次，用户关闭弹窗后无法再次复制，只能删除重建。需要让密钥所有者后续仍能取回同一密钥。

## 决策

### 实现方案

新增 `POST /api/user/apikey/{id}/reveal`：沿用 get_accessible 既有可见性（本人或 superadmin），按行内 request_id 以 `user-request:` 幂等域调用 AuthUtils.derive_api_key 重算完整密钥，重算哈希与存储 key_hash 经 compare_digest 一致才返回，不一致（CLI 授权等其他派生域、request_id 缺失）返回 409。前端列表操作列新增复制按钮调用该接口并走 copyTextToClipboard；创建弹窗与集成文档同步更新"仅一次可见"的过时表述。

### 安全权衡

"仅创建时可见"从展示约定放宽为"所有者随时可取回"：会话被盗或 XSS 场景下长生命周期密钥可被静默读取，这是产品决策接受的降级。同时 superadmin 新增跨用户取回任意用户密钥明文的能力（此前创建后无人可取回），与其既有可读/改/删任何 Key 的管理边界一致，一并接受。数据库仍只存哈希；无法重建派生域的密钥 fail-closed。该决定与上游 2026-07 移除的 regenerate（轮换出新密钥）不同：reveal 返回同一密钥，不轮换。

## 替代方案

删除重建（用户已否决，打断现有集成）；恢复 regenerate 轮换端点（换密钥会让旧集成失效，且该端点曾被上游移除）；数据库加密存储完整密钥（引入新密钥管理面，收益不高于确定性重算）。

## 后果

页面创建的密钥随时可复制；CLI 授权创建的密钥在用户接口中不支持 reveal（明确报错）。reveal 与读取/更新共享同一权限边界，不产生新的跨用户可见性。

## 验证

真实 HTTP 集成测试（backend/test/integration/api/test_apikey_router.py，真实 PostgreSQL 与登录态）：reveal 返回与创建响应相同的 secret，且该 secret 可通过 Bearer 通过 /api/auth/me 鉴权；未登录 401；构造 CLI 派生域密钥行后 reveal 在哈希比对处失败返回 409（负向守卫，保证拒绝原因是派生域不可重建而非其他错误）。18 项全过。前端 unit（apiKeyManagement.test.js）：复制成功路径写入剪贴板并复位 loading，reveal 拒绝路径透出后端错误文案，web lint 与 396 项 unit 通过。后端全量 unit 2493 过（1 项存量失败与本变更无关）。
