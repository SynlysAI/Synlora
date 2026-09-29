# Synlora python.run 沙箱镜像（分析全家桶，与本地 conda 能力对齐）
# 构建：docker build -t synlora-sandbox:latest docker/sandbox/
# 运行形态见 packages/synlys-harness/src/synlys_harness/tools/sandbox.py
# （临时容器：workspace 单目录挂载、默认断网、资源限额、非 root 用户；
#  联网开关 SANDBOX_DOCKER_NETWORK 只影响运行参数，不改本镜像）
FROM python:3.12-slim

# CJK 字体 + 分析沙箱命令行工具链
# 注意：容器默认断网（sandbox.py network_disabled=not network_enabled），
# curl/wget/git 仅在 SANDBOX_DOCKER_NETWORK 放开的部署里可用；其余工具离线态直接可用。
# tesseract 补扫描件 OCR（pdf 技能正文曾注明「未预装 OCR」）。
# apt 源换清华镜像 + 高重试 + 失败二次补齐：官方 deb.debian.org 与镜像站
# 在本网络下都会间歇性返回 502，需靠重试/补齐兜住（ffmpeg 依赖树庞大易触发失败，未纳入）。
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
    fi; \
    APT_OPTS='-o Acquire::Retries=10 -o Acquire::http::Pipeline-Depth=0'; \
    apt-get $APT_OPTS update; \
    PKGS='bash fonts-noto-cjk curl wget git unzip zip file jq sqlite3 poppler-utils ghostscript imagemagick tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng build-essential vim-tiny'; \
    apt-get $APT_OPTS install -y --no-install-recommends --fix-missing $PKGS \
    || apt-get $APT_OPTS install -y --no-install-recommends --fix-missing $PKGS; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
# 国内镜像源加速构建（基础层体积大，走官方源下载慢）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt

# 非 root 运行（容器内写 / 根文件系统会被拒，逃逸用例的依赖项）。
# 运行期宿主会动态传 user=<工作区属主uid:gid>（Linux bind mount 权限对齐，
# 见 sandbox.py DockerCodeExecutor._resolve_user_param）；此默认用户仅在
# Windows 开发机或宿主属主为 root 时生效。
RUN useradd --create-home --uid 10000 sandbox
USER sandbox
ENV MPLCONFIGDIR=/tmp/mplconfig
WORKDIR /workspace
