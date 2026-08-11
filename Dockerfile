# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# PYTHONPATH=/app: pip install puts the package in site-packages but omits
# gitignored REPL binaries (*.mjs/*.wasm). The COPY'd tree under /app keeps them;
# console scripts must prefer /app so the µPy shell can load assets.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0a5 \
    PYTHONPATH=/app \
    WASMMOD_CDN_HOST=0.0.0.0 \
    WASMMOD_CDN_PORT=8000 \
    WASMMOD_CDN_BASE_PATH=/cdn \
    WASMMOD_CDN_DATA_DIR=/data \
    WASMMOD_CDN_STORAGE_ROOT=/data/packs \
    WASMMOD_CDN_DATABASE_URL=sqlite+aiosqlite:////data/wasmmod_cdn.db \
    WASMMOD_CDN_EXPERIMENTAL=true \
    WASMMOD_CDN_EXPERIMENTAL_REPL=true \
    WASMMOD_CDN_REQUIRE_SIGNED=present

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Client package-dir is ".." relative to client/ — keep both under /app.
COPY pyproject.toml README.md LICENSE ./
COPY client ./client
COPY pymergetic ./pymergetic

# Install in-tree client first (not published to PyPI in this tree).
# Keep the micropython.mjs warning outside the &&-chain so a missing asset
# cannot mask a failed pip/useradd (shell: A && B || echo → success on echo).
RUN set -eux; \
    pip install --no-cache-dir --pre 'pymergetic-wasmmod-tools>=0.1.0a1'; \
    pip install --no-cache-dir ./client .; \
    useradd --create-home --uid 10001 app; \
    mkdir -p /data/packs; \
    chown -R app:app /data /app; \
    if [ ! -f pymergetic/wasmmod/cdn/web/static/repl/micropython.mjs ]; then \
      echo "WARNING: micropython.mjs missing — run scripts/sync-repl-assets.sh / scripts/dev-up.sh before docker build"; \
    fi; \
    if [ ! -f pymergetic/metal/inspect/adapter_fastapi.py ]; then \
      echo "ERROR: pymergetic.metal.inspect missing — run scripts/sync-metal-inspect.sh before docker build"; \
      exit 1; \
    fi
USER app

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/cdn/health')" || exit 1

CMD ["wasmmod-cdn", "serve", "--host", "0.0.0.0", "--port", "8000"]
