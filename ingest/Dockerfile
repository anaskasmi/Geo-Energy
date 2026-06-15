# Ingestion job image (Service A) — one-shot, non-root (GEO-2).
# Pinning/hardening polish is GEO-33/35; this is already slim + non-root.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HOME=/home/ingest

WORKDIR /app

# rasterio's wheel (GEO-9 slope) bundles GDAL/GEOS/PROJ but still dlopens libexpat at runtime,
# which python:3.12-slim omits (import fails with "libexpat.so.1: cannot open shared object
# file"). Install the minimal runtime lib so the slope fetcher can actually run in-container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY pipeline ./pipeline
COPY tests ./tests

# Non-root runtime user; owns /data so a fresh named volume inherits writable ownership,
# and HOME so DuckDB can install its spatial/httpfs extensions (~/.duckdb).
RUN useradd --create-home --uid 10001 ingest \
    && mkdir -p /data \
    && chown -R ingest:ingest /data /app /home/ingest
USER ingest

# Pre-bake the DuckDB spatial/httpfs extensions into the image (~/.duckdb) at BUILD time,
# as the runtime user, so each one-shot `--rm` run LOADs them offline instead of
# re-downloading from extensions.duckdb.org on every invocation (FR-A5 / air-gapped hosts).
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL spatial; INSTALL httpfs;')"

# One-shot: builds the artifact then exits. Never starts on `compose up` (profiled).
ENTRYPOINT ["python", "-m", "pipeline"]
