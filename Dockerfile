# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    METAL_CDN_HOST=0.0.0.0 \
    METAL_CDN_PORT=8000 \
    METAL_CDN_BASE_PATH=/cdn \
    METAL_CDN_DATA_DIR=/data \
    METAL_CDN_STORAGE_ROOT=/data/packs \
    METAL_CDN_DATABASE_URL=sqlite+aiosqlite:////data/metal_cdn.db

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY pymergetic ./pymergetic

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/packs \
    && chown -R app:app /data /app
USER app

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/cdn/health')" || exit 1

CMD ["metal-cdn", "serve", "--host", "0.0.0.0", "--port", "8000"]
