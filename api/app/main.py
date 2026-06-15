"""Site-Selection API — FastAPI skeleton (GEO-15).

Opens the read-only DuckDB artifact ONCE at startup (``read_only=True``, spatial
loaded, ``PRAGMA threads``) and stores the shared handle on ``app.state``. The
connection is NOT thread-safe, so every request gets its own ``cursor()`` (a cheap
view onto the shared read-only instance) and blocking ``.execute().fetchall()``
calls run in the threadpool via ``run_in_threadpool``.

Startup is tolerant: if the artifact is missing/unopenable (e.g. before the first
ingest), the app still boots — it logs a warning and stores ``con=None`` plus the
error string — so the container does not crashloop. ``GET /api/health`` then
reports unhealthy (503).

Scoring endpoints are GEO-16+ and the agent loop is GEO-21; this is a clean,
minimal skeleton only.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the read-only DuckDB handle once at startup, close it on shutdown.

    Env (DATA_DIR / DUCKDB_THREADS) is read HERE, at startup time, so tests can
    set it before the app boots. Failures are tolerated: ``con`` is left ``None``
    and the reason is recorded so ``/api/health`` can report it.
    """
    path = db.artifact_path()
    app.state.con = None
    app.state.con_error = None
    app.state.artifact_path = str(path)
    try:
        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")
        app.state.con = db.connect(path, read_only=True)
        log.info("opened read-only artifact: %s", path)
    except Exception as exc:  # tolerant startup — never crashloop before first ingest
        app.state.con_error = f"{type(exc).__name__}: {exc}"
        log.warning("artifact unavailable at startup (%s): %s", path, app.state.con_error)
    try:
        yield
    finally:
        con = getattr(app.state, "con", None)
        if con is not None:
            con.close()
            log.info("closed read-only artifact")
            app.state.con = None


app = FastAPI(
    title="Site-Selection API",
    docs_url="/api/docs",
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Wrap 503s in the ``{"status": "unavailable", "detail": ...}`` envelope.

    Other HTTP errors keep FastAPI's default ``{"detail": ...}`` shape.
    """
    if exc.status_code == 503:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


def get_cursor():
    """Per-request dependency: yield a fresh cursor on the shared read-only handle.

    The shared connection is not thread-safe, so each request operates on its own
    ``cursor()`` (a cheap view onto the one read-only instance). Raises 503 if no
    connection was opened at startup. This is the reusable building block for the
    GEO-16+ scoring/query endpoints.
    """
    con = getattr(app.state, "con", None)
    if con is None:
        raise HTTPException(
            status_code=503,
            detail=getattr(app.state, "con_error", None) or "database unavailable",
        )
    cur = con.cursor()
    try:
        yield cur
    finally:
        cur.close()


@app.get("/api/health")
async def health(cur=Depends(get_cursor)) -> dict:
    """Liveness/readiness: confirm the DB answers and spatial is loaded.

    Healthy     -> 200 ``{"status": "ok", "spatial": true, ...}``.
    Unavailable -> 503 ``{"status": "unavailable", "detail": <reason>}`` (the
    ``get_cursor`` dependency raises 503 if the handle never opened; the query
    branch below raises 503 if an open handle stops answering).

    Runs the (blocking) DuckDB calls on a fresh per-request ``cursor()`` in the
    threadpool. Kept deliberately cheap.
    """
    def _check():
        one = cur.execute("SELECT 1").fetchall()
        spatial = cur.execute(
            "SELECT count(*) FROM duckdb_extensions() "
            "WHERE extension_name = 'spatial' AND loaded"
        ).fetchone()[0]
        return one, spatial

    try:
        one, spatial_loaded = await run_in_threadpool(_check)
    except Exception as exc:  # open handle, query failed
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}")

    if one != [(1,)] or not spatial_loaded:
        raise HTTPException(status_code=503, detail="database check failed")

    return {
        "status": "ok",
        "spatial": True,
        "artifact": getattr(app.state, "artifact_path", None),
    }


@app.get("/")
def root() -> dict:
    return {"service": "api", "status": "ok", "docs": "/api/docs", "health": "/api/health"}
