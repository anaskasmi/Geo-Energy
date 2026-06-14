"""PLACEHOLDER FastAPI app (GEO-1 scaffolding).

The real skeleton — read-only DuckDB handle opened at startup (read_only=True,
LOAD spatial), per-request cursor() pattern, run_in_threadpool for blocking queries,
PRAGMA threads = vCPUs, and the /api/health endpoint — is GEO-15. Scoring is GEO-16+,
and the agent loop is GEO-21. This stub exists only so the `api` service boots.
"""

from fastapi import FastAPI

app = FastAPI(title="Site-Selection API (scaffold)", docs_url="/api/docs")


@app.get("/")
def root() -> dict:
    return {"service": "api", "status": "scaffold", "see": "GEO-15 for the real skeleton"}
