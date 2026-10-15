# 开发拓扑新增 web-prod 生产静态服务供公网域名访问

状态：implemented
类型：feature
Owner：docker-compose.yml

## 问题

公网域名此前经 frp 隧道直接指向 Vite 开发服务器（5173）：dev 模式首屏需数百个模块请求，经 浏览器 → 云 nginx → frp 隧道 → 本机 的高 RTT 链路放大（实测单请求约 150ms、隧道带宽约 3.5Mbps），首屏达数十秒，公网访问几乎不可用。目标是让公网入口消费前端生产构建，同时不影响局域网热更新开发与其他协作者的默认启动体验。

## 决策

### 实现方案

docker-compose.yml 新增 web-prod 服务，复用 docker/web.Dockerfile 现成 production target：构建产物由容器内 nginx 托管，default.conf 拥有静态路由与 /api、/minio 反向代理（SSE 无缓冲）。宿主端口仅绑定 127.0.0.1:4173，供本机 frpc 接入；restart 策略 unless-stopped。服务挂 public profile：默认 docker compose up 不构建、不启动，需 `docker compose --profile public up -d --build web-prod` 显式启用；不带 profile 的 docker compose down 也不会停止它（需 `--profile public down`）。docker/nginx/nginx.conf 启用 gzip 压缩静态产物，使首屏体积主张成为仓库拥有的事实。前端改动对公网生效需重新构建该服务；后端与 web-prod 共享同一 api（bind mount 热重载）。公网入口（域名、云侧反向代理与隧道指向）属部署环境配置，不在仓库维护。

### 边界

不改变默认开发拓扑的行为与启动成本；不引入新依赖或新网关组件；局域网 5173 热更新开发路径完全不变。

## 替代方案

维持隧道直连 dev 服务器，首屏数十秒，不可用。整套切换 docker-compose.prod.yml 生产拓扑：与当前开发/数据拓扑分离，迁移成本高且非本问题目标。仅在云 nginx 开 http2 与 gzip：减少并发与传输开销，但不消除模块瀑布，治标不治本。

## 后果

公网首屏从数十秒降至秒级：仓库内 nginx 以 gzip 压缩静态产物（主 JS 1.2MB 压至约 1/3），配合入口侧网关传输。代价是前端改动对公网生效需显式重建 web-prod，多一步发布动作；web-prod 与 web 的依赖由同一 lockfile 约束，不会漂移。新增本地 127.0.0.1:4173 端口占用与一个镜像。后端不受影响：web-prod 与 web 共享同一 api（bind mount 热重载）。

## 验证

`docker compose config --services` 不含 web-prod、`--profile public` 后包含，确认默认拓扑未变化（负向主张：若移除 profile，默认 services 将包含 web-prod 即失败）。`docker compose config --quiet` 语法通过。本机 `curl 127.0.0.1:4173/` 与 `/api/system/health` 均 200，带 Accept-Encoding 请求主 JS 返回 gzip；经公网域名 `https://synlora.xmuzc.com/` 与 `/api/system/health` 均 200，产物为带 hash 的静态资源。端到端页面加载耗时依赖用户网络，未在真实浏览器实测，列为未验证范围。工程契约检查通过。
