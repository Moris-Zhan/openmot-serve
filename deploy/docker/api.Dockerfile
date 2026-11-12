FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install uv==0.5.11 \
    && useradd --uid 1000 --create-home openmot \
    && mkdir -p /data/storage && chown openmot /data/storage

WORKDIR /build
COPY deploy/docker/api-requirements.txt ./
RUN uv pip install --system -r api-requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY tracking ./tracking
COPY jobs ./jobs
COPY metrics ./metrics
COPY app ./app
COPY worker ./worker
RUN uv pip install --system --no-deps .

WORKDIR /home/openmot
USER openmot
ENV OPENMOT_STORAGE_DIR=/data/storage
EXPOSE 8000
CMD ["python", "-m", "app.run"]
