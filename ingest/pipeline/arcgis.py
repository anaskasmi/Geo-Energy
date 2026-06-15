"""Paginated ArcGIS FeatureServer → GeoJSON download (GEODAT parcels + Shafter fallback).

An ArcGIS REST `query` returns at most the server's `maxRecordCount` features per call and
sets `exceededTransferLimit` when more remain. We page with `resultOffset` /
`resultRecordCount` until the server stops asking for more, then write a single GeoJSON
`FeatureCollection` for `ST_Read`. `outSR=4326` so output is already in storage CRS.

The HTTP transport is injectable (httpx.MockTransport) so pagination/fallback logic is
fully testable offline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .logging_setup import get_logger, log_event
from .sources import SourceError

_log = get_logger("ingest.arcgis")


def _query_url(layer_url: str) -> str:
    return layer_url.rstrip("/") + "/query"


def _count_features(client: httpx.Client, layer_url: str, base: dict, *, log) -> int | None:
    """Best-effort upfront feature count (ArcGIS `returnCountOnly`) so the page logs below can
    show progress as a percentage / ETA. Returns None if the server doesn't answer — the
    download still proceeds, just without a denominator. Never fatal (single attempt)."""
    params = {k: v for k, v in base.items() if k in ("where", "geometry", "geometryType", "inSR", "spatialRel")}
    params.update(f="json", returnCountOnly="true", returnGeometry="false")
    try:
        resp = client.get(_query_url(layer_url), params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("count"), int):
            return data["count"]
    except Exception as err:  # noqa: BLE001 — purely informational; downloads continue
        log_event(log, "arcgis.count_unavailable", url=layer_url, error=str(err))
    return None


def _get_json(client: httpx.Client, url: str, params: dict, *, retries: int, log) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as err:  # noqa: BLE001
            last_err = err
            log_event(log, "arcgis.retry", url=url, attempt=attempt, error=str(err))
    raise SourceError(f"ArcGIS request failed after {retries} attempts: {url}: {last_err}") from last_err


def fetch_featureserver_geojson(
    layer_url: str,
    dest: str | Path,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int = 4326,
    bbox: tuple[float, float, float, float] | None = None,
    in_sr: int = 4326,
    page_size: int = 2000,
    max_pages: int = 10_000,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 120.0,
    retries: int = 3,
    logger=None,
) -> int:
    """Download all features of an ArcGIS FeatureServer layer to `dest` as one GeoJSON.

    Returns the feature count. Raises SourceError on an ArcGIS error payload or if
    pagination fails to terminate within `max_pages`.

    `bbox` (xmin, ymin, xmax, ymax in `in_sr`, default 4326) adds a server-side envelope
    intersection filter — essential for national layers (HIFLD) so only the study-area
    subset is downloaded instead of the whole country. A finer per-feature clip to the
    actual county polygon still happens in the fetcher (the envelope is a coarse prefilter).
    """
    dest = Path(dest)
    log = logger or _log
    base = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "geojson",
        "returnGeometry": "true",
    }
    if bbox is not None:
        xmin, ymin, xmax, ymax = bbox
        base.update(
            geometry=f"{xmin},{ymin},{xmax},{ymax}",
            geometryType="esriGeometryEnvelope",
            inSR=str(in_sr),
            spatialRel="esriSpatialRelIntersects",
        )
    features: list[dict] = []
    offset = 0
    page_no = 0
    started = time.monotonic()
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as client:
        # Best-effort total so the page logs below can show progress / ETA. This download is
        # paginated and can be large (the full Kern parcels layer is hundreds of thousands of
        # features) — without per-page logging a healthy multi-minute pull is indistinguishable
        # from a hang.
        total_expected = _count_features(client, layer_url, base, log=log)
        log_event(
            log, "arcgis.download.start", url=layer_url, page_size=page_size,
            total_expected=total_expected, has_bbox=bbox is not None,
        )
        for _ in range(max_pages):
            params = dict(base, resultOffset=str(offset), resultRecordCount=str(page_size))
            data = _get_json(client, _query_url(layer_url), params, retries=retries, log=log)
            if isinstance(data, dict) and "error" in data:
                raise SourceError(f"ArcGIS error from {layer_url}: {data['error']}")
            page = data.get("features") or []
            features.extend(page)
            if not page:
                break
            offset += len(page)
            page_no += 1
            elapsed = time.monotonic() - started
            pct = round(100.0 * len(features) / total_expected, 1) if total_expected else None
            rate = round(len(features) / elapsed, 1) if elapsed > 0 else None
            # Emit a heartbeat every page so a long download visibly advances (offset, cumulative
            # total, percent of the expected count, elapsed seconds, features/sec).
            log_event(
                log, "arcgis.page", url=layer_url, page=page_no, fetched=len(page),
                total=len(features), total_expected=total_expected, pct=pct,
                elapsed_s=round(elapsed, 1), rate_per_s=rate,
            )
            exceeded = bool(
                data.get("exceededTransferLimit")
                or (data.get("properties") or {}).get("exceededTransferLimit")
            )
            # Continue only while the server signals more, or a full page hints at more
            # (servers that don't set the flag). A partial, non-exceeded page means done.
            if not exceeded and len(page) < page_size:
                break
        else:
            raise SourceError(f"ArcGIS pagination exceeded {max_pages} pages for {layer_url}")

    dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    log_event(
        log, "arcgis.fetched", url=layer_url, features=len(features), dest=str(dest),
        elapsed_s=round(time.monotonic() - started, 1),
    )
    return len(features)


def fetch_with_fallback(urls, dest: str | Path, *, logger=None, **kwargs) -> tuple[str, int]:
    """Try each FeatureServer URL in order; return (url_used, feature_count) for the first
    that yields ≥1 feature. Raises SourceError if every source fails or is empty.
    """
    log = logger or _log
    last_err: Exception | None = None
    for url in urls:
        if not url:
            continue
        try:
            count = fetch_featureserver_geojson(url, dest, logger=log, **kwargs)
            if count > 0:
                return url, count
            last_err = SourceError(f"{url} returned 0 features")
            log_event(log, "arcgis.empty_source", url=url)
        except Exception as err:  # noqa: BLE001 — fall through to the next mirror
            last_err = err
            log_event(log, "arcgis.source_failed", url=url, error=str(err))
    raise SourceError(f"all ArcGIS sources failed: {last_err}")
