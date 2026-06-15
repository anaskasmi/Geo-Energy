"""NREL Solar Resource API client (GEO-10) — per-point GHI/DNI/lat-tilt with on-disk cache.

`developer.nlr.gov/api/solar/solar_resource/v1.json?lat=&lon=&api_key=` returns, per point,
annual + monthly `avg_ghi` / `avg_dni` / `avg_lat_tilt`. This module isolates that network I/O
(like arcgis.py) so the fetcher transform logic stays testable offline: the HTTP transport is
injectable (httpx.MockTransport) and responses are cached on disk so re-runs (and tests) don't
re-query. Network is used only during ingest, never on the request path (FR-A5).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Iterable

import httpx

from .logging_setup import get_logger, log_event
from .sources import SourceError

_log = get_logger("ingest.nrel")

# httpx logs every request line ("HTTP Request: GET <full-url>") at INFO on its own logger —
# and our api_key rides in the query string, so that line would leak the secret into any
# stdout/JSON log. Mute httpx to WARNING (its request lines are noise anyway). Done here, in
# the only module that sends api-key-bearing requests, so importing it is enough to protect.
logging.getLogger("httpx").setLevel(logging.WARNING)

# The api_key travels as a query param, so httpx error messages (and thus logs / raised
# exceptions / tracebacks) would embed `...?api_key=<SECRET>&...`. Redact it everywhere before
# it can reach a log line or an exception message — the key is the project's one secret and
# must stay out of repr/logs (config.Settings.nrel_api_key is field(repr=False)).
_API_KEY_RE = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _API_KEY_RE.sub(r"\1***", str(text))


def _annual(outputs: dict, key: str) -> float | None:
    node = outputs.get(key)
    if isinstance(node, dict) and node.get("annual") is not None:
        try:
            return float(node["annual"])
        except (TypeError, ValueError):
            return None
    return None


def _parse(data: dict | None, lon: float, lat: float) -> dict | None:
    """Pull the three annual values out of a solar_resource response, or None for a
    'no data' location (NREL returns null outputs offshore / outside coverage)."""
    outputs = (data or {}).get("outputs") or {}
    ghi = _annual(outputs, "avg_ghi")
    dni = _annual(outputs, "avg_dni")
    tilt = _annual(outputs, "avg_lat_tilt")
    if ghi is None and dni is None and tilt is None:
        return None
    return {"lon": float(lon), "lat": float(lat), "avg_ghi": ghi, "avg_dni": dni, "avg_lat_tilt": tilt}


def _cache_path(cache_dir: str | Path, lon: float, lat: float) -> Path:
    return Path(cache_dir) / f"sr_{lat:.4f}_{lon:.4f}.json"


def _read_cache(cp: Path) -> dict | None:
    """Read a cached response, self-healing past a truncated/corrupt file (delete + miss)."""
    try:
        return json.loads(cp.read_text())
    except (json.JSONDecodeError, OSError):
        cp.unlink(missing_ok=True)  # a half-written cache file must not wedge every re-run
        return None


def _write_cache(cp: Path, data: dict) -> None:
    """Write the cache atomically (.tmp + os.replace) so a crash never leaves a partial file."""
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_name(cp.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cp)


def fetch_solar_resource(
    lon: float,
    lat: float,
    *,
    api_key: str,
    url: str,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    backoff: float = 1.0,
    cache_dir: str | Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    logger=None,
) -> dict | None:
    """One point → {lon, lat, avg_ghi, avg_dni, avg_lat_tilt} (or None for no-data). Cached on
    disk by (lat, lon) so a repeat call / re-run never re-queries."""
    log = logger or _log
    if cache_dir is not None:
        cached = _read_cache(_cache_path(cache_dir, lon, lat))
        if cached is not None:
            return _parse(cached, lon, lat)

    last_err: str | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url, params={"api_key": api_key, "lat": lat, "lon": lon})
            resp.raise_for_status()
            data = resp.json()
            if cache_dir is not None:
                _write_cache(_cache_path(cache_dir, lon, lat), data)
            return _parse(data, lon, lat)
        except Exception as err:  # noqa: BLE001 — retry transport/HTTP/429 failures
            # str(err) can embed the request URL incl. ?api_key=<secret>; redact before logging.
            last_err = _redact(str(err))
            log_event(log, "nrel.retry", lat=lat, lon=lon, attempt=attempt, error=last_err)
            if attempt < retries:
                sleep(backoff * attempt)
    # `from None` suppresses the chained httpx exception so its (unredacted) URL never reaches
    # the traceback that the harness logs.
    raise SourceError(
        f"NREL solar_resource failed for ({lat},{lon}) after {retries} attempts: {last_err}"
    ) from None


def fetch_grid(
    points: Iterable[tuple[float, float]],
    *,
    api_key: str,
    url: str,
    transport: httpx.BaseTransport | None = None,
    rate_per_hour: int = 1000,
    cache_dir: str | Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    logger=None,
) -> list[dict]:
    """Query every (lon, lat) point, throttled to stay under `rate_per_hour`. Cache hits do
    not count against the rate (no sleep). No-data points are dropped. Requires an api_key."""
    log = logger or _log
    if not api_key:
        raise SourceError(
            "NREL_API_KEY is required for the live GHI grid fetch (or stage a CSV via "
            "GEO_NREL_GHI_SOURCE)"
        )
    interval = 3600.0 / float(rate_per_hour) if rate_per_hour and rate_per_hour > 0 else 0.0
    rows: list[dict] = []
    processed = 0
    live_calls = 0
    for lon, lat in points:
        cached = cache_dir is not None and _cache_path(cache_dir, lon, lat).exists()
        if live_calls and interval > 0 and not cached:
            sleep(interval)
        row = fetch_solar_resource(
            lon, lat, api_key=api_key, url=url, transport=transport,
            cache_dir=cache_dir, sleep=sleep, logger=log,
        )
        processed += 1
        if not cached:
            live_calls += 1
        if row is not None:
            rows.append(row)
    log_event(log, "nrel.grid_fetched", requested=processed, live_calls=live_calls, kept=len(rows))
    return rows
