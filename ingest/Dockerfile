# Ingestion job image (Service A) — one-shot, non-root (GEO-2).
# Pinning/hardening polish is GEO-33/35; this is already slim + non-root.

# ---- tippecanoe builder (GEO-14): compile the vector-tile tool once, in a throwaway stage ----
# tippecanoe is a native C++ binary (NOT a pip dep) and is not packaged in Debian, so we build the
# maintained felt/tippecanoe fork from source and copy just the resulting binary into the final
# image below. This bakes the `tiles` step (parcels.geojson → parcels.pmtiles) INTO the image so it
# runs offline via `docker compose run --rm tiles`, instead of needing tippecanoe on the host PATH
# (the docstring in pipeline/tiles.py anticipated this as GEO-33 deployment work). Tracks the fork's
# default branch — the previous host-PATH workflow (GEO_TIPPECANOE_BIN / `make tiles`) still works.
FROM debian:bookworm-slim AS tippecanoe
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential git ca-certificates libsqlite3-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/felt/tippecanoe.git /tmp/tippecanoe \
    && make -C /tmp/tippecanoe -j"$(nproc)" \
    && make -C /tmp/tippecanoe install

# ---- main ingest image ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HOME=/home/ingest

WORKDIR /app

# rasterio's wheel (GEO-9 slope) bundles GDAL/GEOS/PROJ but still dlopens libexpat at runtime,
# which python:3.12-slim omits (import fails with "libexpat.so.1: cannot open shared object
# file"). Install the minimal runtime lib so the slope fetcher can actually run in-container.
# libsqlite3-0 + zlib1g + libstdc++6 are the runtime shared libs the tippecanoe binary (copied
# from the builder stage below) dynamically links — without them it fails to start (GEO-14).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libsqlite3-0 zlib1g libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# tippecanoe binary built above — bakes GEO-14 tiling into the image (no host tippecanoe needed).
COPY --from=tippecanoe /usr/local/bin/tippecanoe /usr/local/bin/tippecanoe

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
