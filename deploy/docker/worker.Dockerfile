FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install uv==0.5.11 \
    && useradd --uid 1000 --create-home openmot \
    && mkdir -p /data/storage && chown openmot /data/storage

RUN uv pip install --system torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY tracking ./tracking
COPY jobs ./jobs
COPY app ./app
COPY worker ./worker
RUN uv pip install --system ".[service]"

WORKDIR /home/openmot
USER openmot
ENV OPENMOT_STORAGE_DIR=/data/storage OPENMOT_WEIGHTS=/models/yolox_x.pth
CMD ["python", "-m", "worker.run"]
