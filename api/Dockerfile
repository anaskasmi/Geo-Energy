# Service B (API) — production image (GEO-33): multi-stage, pinned, slim, non-root,
# HEALTHCHECK /api/health. The real FastAPI app (read-only DuckDB, per-request cursor,
# threadpool, PRAGMA threads) is GEO-15.

# ---- builder: install the pinned runtime deps into an isolated venv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ---- runtime: slim, non-root, with the spatial extension pre-baked ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data \
    HOME=/home/api
WORKDIR /app

# Copy only the venv (runtime deps) and the app — no build tools, no test deps.
COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# Non-root runtime user (owns HOME so DuckDB can keep its extension cache in ~/.duckdb).
RUN useradd --create-home --uid 10002 api \
    && chown -R api:api /app /home/api
USER api

# Pre-bake the DuckDB spatial extension into ~/.duckdb at BUILD time (as the runtime user) so
# the read-only handle LOADs it offline at startup — the request path never hits the network
# (FR-A5). httpfs is NOT installed: the API makes no remote reads.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL spatial;')"

EXPOSE 8000

# Readiness: /api/health is 200 only once the artifact is present + spatial loads, and 503
# otherwise (tolerant startup). urlopen raises on a 5xx, so any non-200 exits non-zero =
# unhealthy. start-period gives the first ingest time before failures count.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
