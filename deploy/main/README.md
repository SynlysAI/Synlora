# Synlora main 1.8.0 并行部署

该目录用于在 0.7.3 生产栈之外并行启动 main。默认端口：

- 后端：`0.0.0.0:8006`（PM2）
- MongoDB：`127.0.0.1:27019`
- Web/Caddy：`127.0.0.1:4174`

## 启动

```bash
# 仓库根目录
cd apps/web/frontend && pnpm install && pnpm build
cd ../../..
cd apps/web/backend && uv sync
cd ../../..
cp apps/web/backend/.env.example apps/web/backend/.env
# 按 .env.example 配置 Mongo、认证、Fernet、Plane 回连等持久密钥。
cat > deploy/main/.env <<'EOF'
MONGO_USERNAME=synlora
MONGO_PASSWORD=<持久强密码>
EOF
chmod 600 deploy/main/.env
# backend .env 的 MONGODB_URI 使用同一账号：
# mongodb://synlora:<持久强密码>@127.0.0.1:27019/?authSource=admin

pm2 start ecosystem.prod.cjs
docker compose --project-name synlora-main --file deploy/main/docker-compose.yml up --detach
```

## 切换与回滚

验证通过后，将 `.92` 上 frpc 的 `localPort` 从 `4173` 改为 `4174` 并重启 frpc。
回滚时改回 `4173` 即可；不要删除 0.7.3 的容器、镜像和数据目录。
