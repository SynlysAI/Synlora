# 使用轻量级Python基础镜像
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
COPY --from=node:24-slim /usr/local/bin /usr/local/bin
COPY --from=node:24-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node:24-slim /usr/local/include /usr/local/include
COPY --from=node:24-slim /usr/local/share /usr/local/share

# 设置工作目录
WORKDIR /app

# 环境变量设置
ENV TZ=Asia/Shanghai \
    UV_PROJECT_ENVIRONMENT="/usr/local" \
    UV_COMPILE_BYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# 设置 npm 镜像源，为 MCP 和 Skills 安装依赖
RUN npm config set registry https://registry.npmmirror.com --global \
    && npm cache clean --force

# 设置代理和时区，更换镜像源，安装系统依赖 - 合并为一个RUN减少层数
RUN set -ex \
    # (A) 设置时区
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    # (B) 替换为中科大源 (针对 Debian 新版 sources 格式)
    && sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org/debian-security|mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    # (C) 安装必要的系统库
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends --fix-missing \
        curl \
        ffmpeg \
        fonts-liberation \
        fonts-noto-cjk \
        git \
        libpq5 \
        libsm6 \
        libxext6 \
        libreoffice-calc-nogui \
        libreoffice-impress-nogui \
        libreoffice-writer-nogui \
    # (D) 清理垃圾，减小体积
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 复制项目配置文件
COPY backend/pyproject.toml /app/pyproject.toml
COPY backend/.python-version /app/.python-version
COPY backend/uv.lock /app/uv.lock

# 先复制 package 目录，因为 pyproject.toml 中 yuxi = { path = "package", editable = true }
COPY backend/package /app/package

# 依赖下载：cache mount 让成功下载的包跨构建复用（失败重试不从头来），600s 超时容忍慢链路
# 注意：挂 VPN 时国内源会绕海外出口变慢，要么关 VPN，要么换 https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=600 uv sync --group test --no-dev --frozen \
    --index-url http://mirrors.aliyun.com/pypi/simple

# 复制 server 代码
COPY backend/server /app/server
COPY docker/api-entrypoint.sh /usr/local/bin/yuxi-entrypoint

RUN groupadd --gid 1000 yuxi \
    && useradd --uid 1000 --gid 1000 --create-home yuxi \
    && mkdir -p /app/runtime /home/yuxi/.cache/rapidocr/models \
    && chown -R 1000:1000 /app/runtime /home/yuxi \
    && chmod 0755 /usr/local/bin/yuxi-entrypoint

ENV HOME=/home/yuxi \
    RAPIDOCR_MODEL_DIR=/home/yuxi/.cache/rapidocr/models

USER 1000:1000

ENTRYPOINT ["/usr/local/bin/yuxi-entrypoint"]
