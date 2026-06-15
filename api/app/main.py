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

import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import agent as agent_mod
from app import db, perf, scoring, serialize
from app.models import ScoreRequest, UseCase

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
    app.state.zoning_rules = {}
    perf.score_cache.clear()  # fresh cache per process/artifact (a new build means a restart)
    try:
        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")
        app.state.con = db.connect(path, read_only=True)
        log.info("opened read-only artifact: %s", path)
    except Exception as exc:  # tolerant startup — never crashloop before first ingest
        app.state.con_error = f"{type(exc).__name__}: {exc}"
        log.warning("artifact unavailable at startup (%s): %s", path, app.state.con_error)
    # Curated zoning rules (per build) drive Stage-A prohibited zoning; best-effort, optional.
    try:
        app.state.zoning_rules = scoring.load_zoning_rules(db.zoning_rules_path())
        if app.state.zoning_rules:
            log.info("loaded zoning rules for use cases: %s", sorted(app.state.zoning_rules))
        else:
            log.info("no zoning_rules.csv found; zoning will not be a Stage-A filter")
    except Exception as exc:
        log.warning("failed to load zoning rules: %s: %s", type(exc).__name__, exc)
        app.state.zoning_rules = {}
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

# Performance layer (GEO-18) + request-timing observability (GEO-37). Middleware added last wraps
# outermost, so: ETag first (innermost — hashes the uncompressed body), then GZip (compresses the
# final bytes ≥ 512 B), then RequestTiming last (OUTERMOST — measures total handling and sees the
# final status/headers like X-Cache, without double counting).
app.add_middleware(perf.ETagMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=512)
app.add_middleware(perf.RequestTimingMiddleware)


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
        # Client-facing detail is generic (no artifact path / engine internals); the full reason
        # was logged server-side at startup. See app.state.con_error for ops.
        raise HTTPException(status_code=503, detail="database unavailable")
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


def _fetch(cur, sql: str, params: dict) -> tuple[list[str], list[tuple]]:
    """Run a blocking query on the per-request cursor; return (column names, rows)."""
    rel = cur.execute(sql, params)
    cols = [c[0] for c in rel.description]
    return cols, rel.fetchall()


def _resolve_scoring(use_case: str, weights, thresholds, zoning_override):
    """Resolve weights/thresholds/prohibited-zoning; map ScoringError -> HTTP 422."""
    try:
        resolved_weights = scoring.resolve_weights(use_case, weights)
        resolved_thresholds = scoring.resolve_thresholds(use_case, thresholds)
        rules = getattr(app.state, "zoning_rules", {}) or {}
        prohibited = scoring.prohibited_codes(rules, use_case, zoning_override)
    except scoring.ScoringError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return resolved_weights, resolved_thresholds, prohibited


@app.post("/api/score")
async def score(req: ScoreRequest, response: Response, cur=Depends(get_cursor)) -> dict:
    """Score parcels intersecting a drawn polygon (GEO-16/17).

    Body: ``{geometry, use_case, weights?, thresholds?, limit?, offset?}``. Returns a GeoJSON
    ``FeatureCollection`` of surviving parcels ranked by suitability (0..100), each feature
    carrying the score, rank, and per-factor raw values, plus a ``meta`` block describing the
    resolved profile. The candidate prefilter is an R-tree ``ST_Intersects`` scan.

    Identical requests are served from an in-memory LRU cache (GEO-18; ``X-Cache`` header).
    """
    threshold_overrides = req.thresholds.model_dump() if req.thresholds else None
    zoning_override = req.thresholds.prohibited_zoning if req.thresholds else None
    weights, thresholds, prohibited = _resolve_scoring(
        req.use_case, req.weights, threshold_overrides, zoning_override
    )

    cache_key = perf.score_cache_key(
        req.geometry, req.use_case, weights, thresholds, prohibited, req.limit, req.offset
    )
    cached = perf.score_cache.get(cache_key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=prohibited,
        polygon=True, limit=req.limit, offset=req.offset,
    )
    params["poly"] = json.dumps(req.geometry)

    try:
        cols, data = await run_in_threadpool(_fetch, cur, sql, params)
    except duckdb.Error as exc:
        msg = str(exc)
        if "GeoJSON" in msg or "geometry" in msg.lower():
            log.warning("score: rejecting invalid geometry: %s", msg)
            raise HTTPException(status_code=422, detail="invalid geometry")
        log.error("score query failed: %s: %s", type(exc).__name__, msg)
        raise HTTPException(status_code=503, detail="scoring temporarily unavailable")

    rows = [dict(zip(cols, r)) for r in data]
    meta = {
        "use_case": req.use_case,
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "thresholds": thresholds,
        "prohibited_zoning": prohibited,
        "zoning_rules_available": bool(getattr(app.state, "zoning_rules", {})),
        "limit": req.limit,
        "offset": req.offset,
    }
    result = serialize.score_feature_collection(rows, offset=req.offset, meta=meta)
    perf.score_cache.set(cache_key, result)
    response.headers["X-Cache"] = "MISS"
    return result


@app.get("/api/explain/{parcel_id}")
async def explain(
    parcel_id: int,
    use_case: UseCase = Query(default="utility_solar"),
    cur=Depends(get_cursor),
) -> dict:
    """Per-factor breakdown for a single parcel (GEO-17).

    Uses the preset weights for ``use_case`` (custom weights are a /api/score concern). Reports
    which Stage-A exclusions the parcel fails (it is not filtered out here). 404 if not found.
    """
    weights, thresholds, prohibited = _resolve_scoring(use_case, None, None, None)
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=prohibited, parcel_id=True,
    )
    params["parcel_id"] = parcel_id

    try:
        cols, data = await run_in_threadpool(_fetch, cur, sql, params)
    except duckdb.Error as exc:
        log.error("explain query failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail="lookup temporarily unavailable")
    if not data:
        raise HTTPException(status_code=404, detail=f"parcel {parcel_id} not found")

    row = dict(zip(cols, data[0]))
    return serialize.explain_response(row, use_case=use_case, weights=weights)


# Static map overlay layers served whole as GeoJSON for the SPA's Layers panel. County-scoped and
# small (hundreds of features each), so served un-tiled — ETag-cached + gzipped by the middleware.
# The table names are a fixed allowlist (never user input) so the f-string query is injection-safe.
_OVERLAY_TABLES = {
    "transmission": "transmission_lines",
    "substations": "substations",
    "flood": "flood_sfha",
}


@app.get("/api/layer/{name}")
async def layer(name: str, cur=Depends(get_cursor)) -> dict:
    """GeoJSON for a static map overlay layer (transmission / substations / flood).

    404 for an unknown name; an empty FeatureCollection when the table isn't in the current build
    (so the SPA degrades gracefully rather than erroring before those layers are ingested).
    """
    table = _OVERLAY_TABLES.get(name)
    if table is None:
        raise HTTPException(status_code=404, detail=f"unknown layer {name!r}")
    sql = f"SELECT * EXCLUDE (geom), ST_AsGeoJSON(geom) AS geometry_json FROM {table}"
    try:
        cols, data = await run_in_threadpool(_fetch, cur, sql, {})
    except duckdb.Error:
        return serialize.layer_feature_collection([])  # table absent in this build → empty
    rows = [dict(zip(cols, r)) for r in data]
    return serialize.layer_feature_collection(rows)


@app.get("/api/context")
async def context(cur=Depends(get_cursor)) -> dict:
    """CAISO Kern interconnection-queue summary (GEO-17), informational context only.

    Resilient to a build without the summary table: returns an empty summary rather than erroring.
    """
    sql = (
        "SELECT category, key, n_projects, total_mw, active_n_projects, active_total_mw "
        "FROM caiso_queue_summary"
    )
    try:
        cols, data = await run_in_threadpool(_fetch, cur, sql, {})
    except duckdb.Error:
        return serialize.context_response([])
    rows = [dict(zip(cols, r)) for r in data]
    return serialize.context_response(rows)


@app.post("/api/agent")
async def agent_endpoint(req: agent_mod.AgentRequest, request: Request) -> StreamingResponse:
    """Streaming site-selection agent (GEO-21): Server-Sent Events over POST.

    The pydantic ``AgentRequest`` rejects an oversized message with 422 BEFORE any model call
    (GEO-37 key-exhaustion guard). The SSE generator (:func:`app.agent.stream_agent`) owns the rest
    — per-process concurrency cap, a per-request cursor on the shared read-only handle,
    client-disconnect/timeout aborts, and graceful key-safe ``error`` events — so this handler never
    returns a 500. Event protocol: ``step`` / ``token`` / ``result`` / ``done`` / ``error``.
    """
    con = getattr(app.state, "con", None)
    zoning_rules = getattr(app.state, "zoning_rules", {}) or {}
    generator = agent_mod.stream_agent(
        message=req.message, request=request, con=con, zoning_rules=zoning_rules,
    )
    return StreamingResponse(
        generator, media_type="text/event-stream", headers=agent_mod.SSE_HEADERS,
    )


@app.get("/")
def root() -> dict:
    return {"service": "api", "status": "ok", "docs": "/api/docs", "health": "/api/health"}
