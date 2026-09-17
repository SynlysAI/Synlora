# Synlora python.run 沙箱镜像（分析全家桶，与本地 conda 能力对齐）
# 构建：docker build -t synlora-sandbox:latest docker/sandbox/
# 运行形态见 packages/synlys-harness/src/synlys_harness/tools/sandbox.py
# （临时容器：workspace 单目录挂载、断网、资源限额、非 root 用户）
FROM python:3.12-slim

# CJK 字体：matplotlib 中文标签不缺字（技能里字体链需含 Noto Sans CJK SC）
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
# 国内镜像源加速构建（基础层体积大，走官方源下载慢）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt

# 非 root 运行（容器内写 / 根文件系统会被拒，逃逸用例的依赖项）
RUN useradd --create-home --uid 10000 sandbox
USER sandbox
ENV MPLCONFIGDIR=/tmp/mplconfig
WORKDIR /workspace
