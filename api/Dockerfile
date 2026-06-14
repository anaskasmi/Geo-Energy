# PLACEHOLDER SKELETON (GEO-1).
# The production multi-stage, pinned, slim, non-root image with HEALTHCHECK /api/health
# is GEO-33; the real FastAPI app (read-only DuckDB, per-request cursor, threadpool,
# PRAGMA threads) is GEO-15. This stub only makes the `api` service buildable/bootable.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
