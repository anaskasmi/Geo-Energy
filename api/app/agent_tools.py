"""In-process agent tools (GEO-20).

The four callables the Gemini / Pydantic-AI agent (GEO-21) registers as tools. They call the
in-process scoring engine DIRECTLY (Review C5 — not over HTTP), so the FastAPI/DuckDB engine
stays the single source of truth and the agent only orchestrates/narrates: it NEVER computes
geometry or scores itself.

Design note — FLAT, provider-agnostic schemas (see :data:`TOOL_SPECS`): the LLM-facing tool
signatures use only scalars (string / number / enum). Gemini's function-calling schema is a
strict OpenAPI subset that rejects unions, free-form objects/records and deeply-nested arrays,
so a drawn polygon is NOT passed to the model as a coordinate array. Instead :func:`resolve_area`
caches the geometry server-side and returns an opaque ``area_ref`` token; :func:`score_parcels`
takes that token. The agent shuttles a string, the engine owns the geometry.

Tools raise :class:`ToolError` on bad input (unknown area, bad use case, parcel not found); the
agent wrapper (GEO-21) catches it and narrates the failure to the user. Everything else is a
plain JSON-serialisable dict, ready to hand back to the model and to assemble the response from.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import duckdb

from app import landvalue, scoring, serialize
from app.models import MAX_POSITIONS, _GEOMETRY_TYPES, _count_positions


class ToolError(ValueError):
    """An agent tool was called with input the engine cannot satisfy (surface to the user)."""


# --- resolve_area: local gazetteer (FR-A5: NO network on the request path) ---------------------
# The agent resolves a place name to a coarse search bbox; scoring then filters to the actual
# parcels that intersect it. These are deliberately APPROXIMATE centres for Kern County, CA
# places (WGS84), expanded to a bounding box by _PLACE_PAD. The whole-county case prefers the
# precise `county_boundary` polygon from the artifact (see resolve_area); this static bbox is the
# fallback when the table is absent (e.g. a sample build).
KERN_COUNTY_BBOX = (-120.20, 34.79, -117.62, 35.81)

# place key -> (centre_lng, centre_lat). Aliases share a centre.
_PLACE_CENTERS: dict[str, tuple[float, float]] = {
    "bakersfield": (-119.018, 35.373),
    "delano": (-119.247, 35.769),
    "shafter": (-119.272, 35.501),
    "wasco": (-119.341, 35.594),
    "mcfarland": (-119.229, 35.678),
    "arvin": (-118.833, 35.209),
    "lamont": (-118.913, 35.259),
    "taft": (-119.456, 35.142),
    "maricopa": (-119.401, 35.059),
    "tehachapi": (-118.449, 35.132),
    "mojave": (-118.174, 35.052),
    "california city": (-117.986, 35.126),
    "rosamond": (-118.163, 34.864),
    "boron": (-117.650, 34.994),
    "ridgecrest": (-117.671, 35.622),
    "lake isabella": (-118.473, 35.614),
    "frazier park": (-118.945, 34.822),
    "buttonwillow": (-119.468, 35.401),
    "lost hills": (-119.692, 35.616),
    "oildale": (-119.020, 35.420),
    "edwards": (-117.891, 34.905),  # Edwards AFB vicinity
}
# A handful of common aliases the agent might emit.
_PLACE_ALIASES: dict[str, str] = {
    "cal city": "california city",
    "edwards afb": "edwards",
    "edwards air force base": "edwards",
    "isabella": "lake isabella",
    "ridge crest": "ridgecrest",
}
# Whole-county phrasings.
_COUNTY_ALIASES = {
    "kern", "kern county", "county", "all", "all of kern", "entire county", "whole county",
    "kern county, ca", "kern county california",
}
_PLACE_PAD = 0.06  # ~6.5 km half-width box around a place centre


def _norm_place(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" ,.")


def _bbox_to_polygon(bbox: tuple[float, float, float, float]) -> dict:
    """A closed GeoJSON Polygon ring from (minLng, minLat, maxLng, maxLat)."""
    x0, y0, x1, y1 = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _round6(v: float) -> float:
    return round(float(v), 6)


def _geometry_bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            if len(node) >= 2 and all(isinstance(c, (int, float)) for c in node[:2]):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(geometry.get("coordinates"))
    if not xs or not ys:
        raise ToolError("geometry has no coordinates")
    return (min(xs), min(ys), max(xs), max(ys))


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


# A point/box is only accepted if it falls within (a padded) Kern County — the app only has Kern
# parcels. This also stops an incidental number in a phrase from resolving to a confident point
# elsewhere on the globe: "I-5 and CA-58" parses to [-5, -58] (the regex grabs the hyphens), which
# is far outside Kern, so it correctly falls through to a ToolError instead of a silent wrong area.
_KERN_PAD_DEG = 0.5


def _near_kern(lng: float, lat: float) -> bool:
    x0, y0, x1, y1 = KERN_COUNTY_BBOX
    return (x0 - _KERN_PAD_DEG) <= lng <= (x1 + _KERN_PAD_DEG) and (y0 - _KERN_PAD_DEG) <= lat <= (y1 + _KERN_PAD_DEG)


def _try_numeric(text: str) -> dict | None:
    """Parse an explicit bbox (4 numbers) or a point (2 numbers) from free text.

    Returns ``None`` (so the caller falls through to the gazetteer, then a ToolError) unless the
    numbers look like lng/lat coordinates WITHIN Kern County — otherwise an incidental number in a
    place phrase (e.g. "Section 14 30 28", "I-5 and CA-58") would be misread as an area, and the
    app has no parcels outside Kern anyway.
    """
    nums = [float(m) for m in _NUM_RE.findall(text)]
    if len(nums) == 4:
        a, b, c, d = nums
        if _near_kern(a, b) and _near_kern(c, d):  # lng,lat,lng,lat (GeoJSON order)
            bbox = (min(a, c), min(b, d), max(a, c), max(b, d))
        elif _near_kern(b, a) and _near_kern(d, c):  # lat,lng,lat,lng (swap, like the point path)
            bbox = (min(b, d), min(a, c), max(b, d), max(a, c))
        else:
            return None
        return {"geometry": _bbox_to_polygon(bbox), "label": "custom bounding box", "kind": "bbox"}
    if len(nums) == 2:
        lng, lat = nums
        # GeoJSON order is lng,lat; if the first looks like a Kern latitude and the second a
        # longitude, assume the user typed "lat, lng" and swap.
        if 34.0 <= lng <= 36.0 and -121.0 <= lat <= -117.0:
            lng, lat = lat, lng
        if not _near_kern(lng, lat):
            return None
        bbox = (lng - _PLACE_PAD, lat - _PLACE_PAD, lng + _PLACE_PAD, lat + _PLACE_PAD)
        return {"geometry": _bbox_to_polygon(bbox), "label": f"area around ({_round6(lng)}, {_round6(lat)})", "kind": "point"}
    return None


def validate_geometry(geometry: Any) -> dict:
    """Mirror the /api/score geometry guard (type + non-empty + vertex cap) for tool inputs."""
    if not isinstance(geometry, dict):
        raise ToolError("geometry must be a GeoJSON object")
    if geometry.get("type") not in _GEOMETRY_TYPES:
        raise ToolError(f"geometry.type must be one of {sorted(_GEOMETRY_TYPES)}")
    coords = geometry.get("coordinates")
    if not coords:
        raise ToolError("geometry.coordinates is required and must be non-empty")
    if _count_positions(coords) > MAX_POSITIONS:
        raise ToolError("geometry too complex")
    return geometry


# --- AreaStore: token -> geometry, so the LLM never handles coordinates -----------------------
class AreaStore:
    """Thread-safe, size-bounded LRU mapping an opaque ``area_ref`` to a resolved geometry.

    The token is a content hash of the geometry, so resolving the same area twice yields the same
    ref (idempotent) and identical-area score calls share a cache key downstream. Bounded so a long
    chat session can't grow it without limit; the connection is not thread-safe but this map is, as
    tool calls may run in the FastAPI threadpool.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._d: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def put(self, geometry: dict) -> str:
        canonical = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
        ref = "area_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            self._d[ref] = geometry
            self._d.move_to_end(ref)
            while len(self._d) > self._maxsize:
                self._d.popitem(last=False)
        return ref

    def get(self, ref: str) -> dict | None:
        with self._lock:
            geom = self._d.get(ref)
            if geom is not None:
                self._d.move_to_end(ref)
            return geom

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


# Module-level store shared across requests in a process (refs are content-hashed, so collisions
# are intended dedup, not leakage between users).
area_store = AreaStore()


def _county_geometry(cur) -> dict | None:
    """The precise Kern County polygon from the artifact, if the table exists."""
    if cur is None:
        return None
    try:
        row = cur.execute(
            "SELECT ST_AsGeoJSON(geom) FROM county_boundary LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        geom = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    geom["coordinates"] = serialize.round_coords(geom.get("coordinates"))
    return geom


def resolve_area(text: str, *, cur=None) -> dict:
    """Resolve free text to a GeoJSON search area + an opaque ``area_ref`` token.

    Resolution order: explicit bbox/point numbers → whole-county (precise boundary if available,
    else the county bbox) → curated Kern place gazetteer (approximate boxes). Returns the geometry,
    its ``area_ref`` (pass to :func:`score_parcels`), a human label, the bbox and centroid, and
    whether the box is approximate. Raises :class:`ToolError` if nothing matches — the agent should
    then ask the user to clarify. NO network is used (FR-A5): the gazetteer is local.
    """
    if not text or not text.strip():
        raise ToolError("resolve_area needs a place name, bounding box, or point")
    raw = text.strip()
    norm = _norm_place(raw)

    numeric = _try_numeric(raw)
    if numeric is not None:
        return _finalize_area(numeric["geometry"], label=numeric["label"], source=numeric["kind"], approximate=True)

    if norm in _COUNTY_ALIASES:
        county = _county_geometry(cur)
        if county is not None:
            try:
                return _finalize_area(county, label="Kern County, CA (06029)", source="county_boundary", approximate=False)
            except ToolError:
                pass  # boundary too complex for the vertex cap (or invalid) -> fall back to the bbox
        return _finalize_area(_bbox_to_polygon(KERN_COUNTY_BBOX), label="Kern County, CA (bounding box)", source="county_bbox", approximate=True)

    key = _PLACE_ALIASES.get(norm, norm)
    center = _PLACE_CENTERS.get(key)
    if center is None:
        # Forgiving WHOLE-WORD match ("score parcels near mojave" -> "mojave"). Use word
        # boundaries (so "taft" doesn't match "craft") and pick the LONGEST match (most specific)
        # rather than dict order when several names appear.
        matches = [(name, c) for name, c in _PLACE_CENTERS.items() if re.search(rf"\b{re.escape(name)}\b", norm)]
        if matches:
            key, center = max(matches, key=lambda kc: len(kc[0]))
    if center is not None:
        lng, lat = center
        bbox = (lng - _PLACE_PAD, lat - _PLACE_PAD, lng + _PLACE_PAD, lat + _PLACE_PAD)
        label = key.title() + ", Kern County, CA"
        return _finalize_area(_bbox_to_polygon(bbox), label=label, source="gazetteer", approximate=True)

    raise ToolError(
        f"could not resolve {text!r} to an area in Kern County. Try a city name "
        "(e.g. Mojave, Bakersfield), 'Kern County', a 'minLng,minLat,maxLng,maxLat' box, "
        "or a 'lng,lat' point."
    )


def _finalize_area(geometry: dict, *, label: str, source: str, approximate: bool) -> dict:
    validate_geometry(geometry)
    bbox = _geometry_bbox(geometry)
    ref = area_store.put(geometry)
    return {
        "area_ref": ref,
        "geometry": geometry,
        "label": label,
        "source": source,
        "approximate": approximate,
        "bbox": [_round6(v) for v in bbox],
        "centroid": [_round6((bbox[0] + bbox[2]) / 2), _round6((bbox[1] + bbox[3]) / 2)],
    }


# --- engine glue (same path as the /api/score, /api/explain, /api/context endpoints) ----------
def _fetch(cur, sql: str, params: dict) -> list[dict]:
    rel = cur.execute(sql, params)
    cols = [c[0] for c in rel.description]
    return [dict(zip(cols, r)) for r in rel.fetchall()]


def _resolve_geometry(cur, area_ref: str | None, geometry: dict | None) -> dict:
    """Get the geometry for a score call: explicit geometry, a stored area_ref, or a place name."""
    if geometry is not None:
        return validate_geometry(geometry)
    if not area_ref:
        raise ToolError("score_parcels needs an area_ref (from resolve_area) or a geometry")
    stored = area_store.get(area_ref)
    if stored is not None:
        return stored
    # Convenience: an unrecognised ref that isn't a token is treated as a place name to resolve.
    if not area_ref.startswith("area_"):
        return resolve_area(area_ref, cur=cur)["geometry"]
    raise ToolError(f"unknown area_ref {area_ref!r}; call resolve_area first")


def score_parcels(
    cur,
    *,
    area_ref: str | None = None,
    geometry: dict | None = None,
    use_case: str = "utility_solar",
    weights: dict[str, float] | None = None,
    min_acres: float | None = None,
    max_slope_pct: float | None = None,
    exclude_sfha: bool | None = None,
    apply_optional_exclusions: bool | None = None,
    prohibited_zoning: list[str] | None = None,
    zoning_rules: dict | None = None,
    limit: int = 200,
    offset: int = 0,
    affordability_score: float | None = None,
    affordability_weight: float | None = None,
) -> dict:
    """Rank parcels in an area for a use case (calls the scoring engine; never computes geometry).

    Returns the same ranked GeoJSON ``FeatureCollection`` (+ ``meta``) as POST /api/score. Geometry
    comes from ``area_ref`` (preferred) or an explicit ``geometry``. Raises :class:`ToolError` for
    bad parameters (mapped from :class:`scoring.ScoringError`).

    ``affordability_score`` (0..1, from :func:`check_affordability`) optionally folds the area's
    land affordability into the suitability score via an order-preserving convex blend (see
    :func:`scoring.blend_affordability`) — cheaper land lifts the score. It is AREA-level (uniform
    across the area), so it shifts scores without reordering parcels within a single area; the local
    engine and the candidate ranking are untouched (FR-A5 holds — no network here).
    """
    geom = _resolve_geometry(cur, area_ref, geometry)
    threshold_overrides = {
        "min_acres": min_acres,
        "max_slope_pct": max_slope_pct,
        "exclude_sfha": exclude_sfha,
        "apply_optional_exclusions": apply_optional_exclusions,
    }
    try:
        resolved_weights = scoring.resolve_weights(use_case, weights)
        resolved_thresholds = scoring.resolve_thresholds(use_case, threshold_overrides)
        prohibited = scoring.prohibited_codes(zoning_rules or {}, use_case, prohibited_zoning)
    except scoring.ScoringError as exc:
        raise ToolError(str(exc)) from exc

    try:
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
    except (TypeError, ValueError) as exc:
        raise ToolError("limit and offset must be integers") from exc
    sql, params = scoring.build_score_sql(
        weights=resolved_weights, thresholds=resolved_thresholds, prohibited=prohibited,
        polygon=True, limit=limit, offset=offset,
    )
    params["poly"] = json.dumps(geom)
    # Map engine errors to ToolError so the agent narrates a clean failure (mirrors how /api/score
    # turns a duckdb.Error into 422 'invalid geometry' / 503) — never let a raw duckdb.Error escape.
    try:
        rows = _fetch(cur, sql, params)
    except duckdb.Error as exc:
        msg = str(exc)
        if "GeoJSON" in msg or "geometry" in msg.lower():
            raise ToolError("invalid geometry") from exc
        raise ToolError("scoring temporarily unavailable") from exc
    meta = {
        "use_case": use_case,
        "weights": {k: round(v, 4) for k, v in resolved_weights.items()},
        "thresholds": resolved_thresholds,
        "prohibited_zoning": prohibited,
        "zoning_rules_available": bool(zoning_rules),
        "limit": limit,
        "offset": offset,
    }
    fc = serialize.score_feature_collection(rows, offset=offset, meta=meta)
    if affordability_score is not None:
        _apply_affordability(fc, affordability_score, affordability_weight)
    return fc


def _apply_affordability(fc: dict, affordability_score: float, weight: float | None) -> None:
    """Blend an area-level ``affordability_score`` (0..1) into each feature's score, in place.

    Order-preserving (uniform affine blend), so ranks are unchanged — only the displayed scores
    shift toward the area's affordability. Records what was applied in ``fc['meta']``.
    """
    try:
        aff = float(affordability_score)
    except (TypeError, ValueError) as exc:
        raise ToolError("affordability_score must be a number in [0, 1]") from exc
    if not math.isfinite(aff) or not (0.0 <= aff <= 1.0):
        raise ToolError("affordability_score must be a number in [0, 1]")
    w = scoring.AFFORDABILITY_WEIGHT_DEFAULT if weight is None else float(weight)
    for feat in fc.get("features", []):
        score = feat["properties"].get("score")
        if score is not None:
            feat["properties"]["score"] = round(scoring.blend_affordability(score, aff, w), 1)
    fc.setdefault("meta", {})["affordability"] = {
        "applied": True,
        "affordability_score": round(aff, 3),
        "weight": round(min(max(w, 0.0), 1.0), 3),
    }


def check_affordability(
    cur, *, area_ref: str | None = None, geometry: dict | None = None, use_case: str = "utility_solar"
) -> dict:
    """Live, area-level land-affordability check for the selected area (GEO-41).

    The ONE outbound-network agent tool (scoped FR-A5 exception): fetches a Kern-County
    land/property-cost signal from free public APIs (FHFA price trend via FRED + Census ACS median
    home value) and derives an ``affordability_score`` in [0, 1] (higher = cheaper land). Pass that
    score to :func:`score_parcels` to fold affordability into the ranking.

    ``area_ref``/``geometry`` ties the check to the user's selection (and validates a stale token);
    the free data is COUNTY-level, so the result is the same for any sub-area drawn inside Kern —
    surfaced honestly in ``note``. Raises :class:`ToolError` if the area is invalid or the data
    services are unreachable, so the agent narrates a clean failure.
    """
    # Validate the area (also catches a stale/unknown token) — the signal itself is county-wide.
    if area_ref or geometry:
        _resolve_geometry(cur, area_ref, geometry)

    data = landvalue.area_affordability()
    if not data.get("ok"):
        raise ToolError(data.get("error") or "live land-value data is unavailable")

    ref = area_ref if (area_ref and area_ref.startswith("area_")) else None
    return affordability_summary(data, area_ref=ref)


def affordability_summary(data: dict, *, area_ref: str | None = None) -> dict:
    """Shape an ``ok`` :func:`landvalue.area_affordability` result into the public Affordability dict.

    Shared by the agent's :func:`check_affordability` tool and the ``/api/affordability`` endpoint so
    the voice and text surfaces (and their cards) get an IDENTICAL shape. Adds the derived 0..1
    ``affordability_score`` + a coarse band, and the honest county-level caveat.
    """
    median = data.get("median_home_value_usd")
    aff = scoring.affordability_score_from_median(median)
    if aff is None:
        band = "unknown"
    elif aff >= 0.6:
        band = "affordable"
    elif aff >= 0.35:
        band = "moderate"
    else:
        band = "expensive"
    return {
        "type": "Affordability",
        "area_ref": area_ref,
        "geography": "Kern County, CA (FIPS 06029)",
        "median_home_value_usd": median,
        "acs_vintage": data.get("acs_vintage"),
        "hpi_index": data.get("hpi_index"),
        "price_trend_yoy_pct": data.get("price_trend_yoy_pct"),
        "hpi_as_of": data.get("hpi_as_of"),
        "affordability_score": round(aff, 3) if aff is not None else None,
        "affordability_band": band,
        "sources": data.get("sources", []),
        "approximate": True,
        "note": (
            "Area-level land-cost signal from free public data (Census ACS median home value + "
            "FHFA price trend); county-wide for Kern, not a per-parcel appraisal."
        ),
    }


def explain_parcel(cur, *, parcel_id: int, use_case: str = "utility_solar", zoning_rules: dict | None = None) -> dict:
    """Per-factor suitability breakdown for one parcel (preset weights). Raises if not found."""
    try:
        weights = scoring.resolve_weights(use_case, None)
        thresholds = scoring.resolve_thresholds(use_case, None)
        prohibited = scoring.prohibited_codes(zoning_rules or {}, use_case, None)
    except scoring.ScoringError as exc:
        raise ToolError(str(exc)) from exc
    try:
        pid = int(parcel_id)
    except (TypeError, ValueError) as exc:
        raise ToolError("parcel_id must be an integer") from exc
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=prohibited, parcel_id=True,
    )
    params["parcel_id"] = pid
    try:
        rows = _fetch(cur, sql, params)
    except duckdb.Error as exc:
        raise ToolError("lookup temporarily unavailable") from exc
    if not rows:
        raise ToolError(f"parcel {pid} not found")
    return serialize.explain_response(rows[0], use_case=use_case, weights=weights)


def grid_context(cur) -> dict:
    """CAISO Kern interconnection-queue summary (context only — never part of scoring)."""
    sql = (
        "SELECT category, key, n_projects, total_mw, active_n_projects, active_total_mw "
        "FROM caiso_queue_summary"
    )
    try:
        rows = _fetch(cur, sql, {})
    except Exception:
        return serialize.context_response([])
    return serialize.context_response(rows)


def focus_parcel(cur, *, parcel_id: int) -> dict:
    """Look up a parcel's centroid so the UI can zoom/pan to it and select it. Raises if not found.

    Returns a small ``{"type": "Focus", parcel_id, apn, centroid: [lng, lat]}`` the SSE handler
    forwards to the client (the agent never moves the map itself — it just names the target).
    """
    try:
        pid = int(parcel_id)
    except (TypeError, ValueError) as exc:
        raise ToolError("parcel_id must be an integer") from exc
    sql = (
        "SELECT id, apn, ST_X(centroid_4326) AS lng, ST_Y(centroid_4326) AS lat "
        "FROM parcels WHERE id = $parcel_id"
    )
    try:
        rows = _fetch(cur, sql, {"parcel_id": pid})
    except duckdb.Error as exc:
        raise ToolError("lookup temporarily unavailable") from exc
    if not rows:
        raise ToolError(f"parcel {pid} not found")
    r = rows[0]
    if r.get("lng") is None or r.get("lat") is None:
        raise ToolError(f"parcel {pid} has no location")
    return {
        "type": "Focus",
        "parcel_id": r["id"],
        "apn": r.get("apn"),
        "centroid": [_round6(r["lng"]), _round6(r["lat"])],
    }


def export_pdf(*, parcel_ids: str = "") -> dict:
    """Request a client-side PDF report for one or more parcels (the BROWSER renders it).

    ``parcel_ids`` is a comma-separated list of parcel ids; empty means "all parcels currently shown
    in the results". This tool only relays the intent — the SPA builds the PDF from the ranked
    results + /api/explain + /api/context (the agent/engine never renders a PDF itself). Returns
    ``{"type": "ExportPdf", "parcel_ids": [...]}``; an empty list signals "all shown".
    """
    ids: list[int] = []
    for tok in (parcel_ids or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            ids.append(int(tok))
        except ValueError as exc:
            raise ToolError(f"invalid parcel id {tok!r}; use comma-separated integers") from exc
    return {"type": "ExportPdf", "parcel_ids": ids}


# --- Shared agent-tool REGISTRY (GEO-42) -------------------------------------------------------
# ONE source of truth for the agent tools' shared contract + the TEXT (Gemini) surface. The VOICE
# (OpenAI Realtime, client-side) surface is an ENFORCED MIRROR — frontend/src/agent/voiceTools.json
# (metadata) + voiceExecutors.ts (bodies) — kept in sync by api/tests/test_tool_registry_parity.py
# (asserts the LIVE Gemini schema == this registry AND the voice mirror matches) plus the frontend
# tsc build (Record<VoiceToolName, …> exhaustiveness). Execution is per-runtime and CANNOT be
# shared (text = in-process DuckDB; voice = browser REST); the registry shares the CONTRACT.
#
# Gemini's OpenAPI subset rejects unions/records/deep nesting, so every parameter is a flat scalar
# (string/number/integer/boolean/enum). GEO-21 binds `cur` + `zoning_rules` server-side; the model
# only ever supplies the parameters below. ``required`` MIRRORS each @agent.tool wrapper's actual
# optionality (a defaulted param like ``use_case`` is NOT required) so the live-schema parity check
# passes; the parity test fails loudly if a wrapper and its registry entry ever diverge.
_USE_CASE_ENUM = list(scoring.SUPPORTED_USE_CASES)


@dataclass(frozen=True)
class ResultSpec:
    """How a tool's result is routed into the SSE ``result`` event (text surface only)."""

    sse_key: str          # result field: featureCollection|area|affordability|focus|exportPdf
    type_tag: str | None  # required content["type"] discriminator; None = no guard (resolve_area)
    field: str | None     # None = forward the whole dict; else forward content[field] (e.g. "label")
    relay: bool           # True = browser ACTS on the payload (focus/export); False = display/narrate


@dataclass(frozen=True)
class ToolDef:
    """One agent tool's shared contract. ``parameters`` is a flat JSON-schema (scalars only)."""

    name: str                  # canonical name == the text @agent.tool function name
    description: str           # text/canonical description
    parameters: dict           # flat JSON-schema
    surfaces: tuple[str, ...]  # ("text",) | ("voice",) | ("text", "voice")
    phase: str | None          # text-side UI step label key (None = no step)
    result: ResultSpec | None  # text-side SSE routing (None = narrate-only, no capture)
    parity: str                # "text_only" | "props" (mirror prop set) | "existence" (name only)


REGISTRY: list[ToolDef] = [
    ToolDef(
        name="resolve_area",
        description=(
            "Resolve a place in Kern County, CA to a search area. Accepts a city/area name "
            "(e.g. 'Mojave', 'Bakersfield'), 'Kern County' for the whole county, a "
            "'minLng,minLat,maxLng,maxLat' bounding box, or a 'lng,lat' point. Returns an "
            "area_ref token to pass to score_parcels. Call this BEFORE score_parcels."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Place name, bounding box, or point."}
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        surfaces=("text",),
        phase="resolving_area",
        result=ResultSpec("area", None, "label", False),
        parity="text_only",
    ),
    ToolDef(
        name="score_parcels",
        description=(
            "Rank parcels in a resolved area by suitability (0-100) for a use case. Returns a "
            "ranked GeoJSON FeatureCollection. Geometry is NOT passed here — resolve the area "
            "first and pass its area_ref. The engine computes all geometry and scores."
        ),
        parameters={
            "type": "object",
            "properties": {
                "area_ref": {"type": "string", "description": "Token from resolve_area."},
                "use_case": {"type": "string", "enum": _USE_CASE_ENUM, "description": "Scoring profile."},
                "min_acres": {"type": "number", "description": "Optional minimum parcel size override (acres)."},
                "max_slope_pct": {"type": "number", "description": "Optional maximum slope override (percent)."},
                "limit": {"type": "integer", "description": "Max results (1-1000, default 200)."},
                "affordability_score": {
                    "type": "number",
                    "description": (
                        "Optional 0..1 land-affordability score from check_affordability (higher = "
                        "cheaper land). When set, folds the area's affordability into the suitability "
                        "ranking."
                    ),
                },
            },
            "required": ["area_ref"],  # use_case is defaulted by the wrapper -> optional in the live schema
            "additionalProperties": False,
        },
        surfaces=("text",),  # voice peer is the find_sites composite (VOICE_ONLY_TOOLS)
        phase="scoring",
        result=ResultSpec("featureCollection", "FeatureCollection", None, False),
        parity="text_only",
    ),
    ToolDef(
        name="check_affordability",
        description=(
            "Check live, area-level LAND AFFORDABILITY for a resolved area (the user's selected "
            "area). Returns a Kern-County land/property-cost signal from free public data (Census "
            "median home value + FHFA price trend) and an affordability_score (0..1, higher = "
            "cheaper). Pass that score to score_parcels to factor affordability into the ranking. "
            "Resolve the area first and pass its area_ref."
        ),
        parameters={
            "type": "object",
            "properties": {
                "area_ref": {"type": "string", "description": "Token from resolve_area (the selected area)."},
                "use_case": {"type": "string", "enum": _USE_CASE_ENUM, "description": "Scoring profile."},
            },
            "required": ["area_ref"],
            "additionalProperties": False,
        },
        surfaces=("text", "voice"),
        phase="checking_affordability",
        result=ResultSpec("affordability", "Affordability", None, False),
        parity="existence",  # voice uses a place-name param; text uses area_ref (divergent by design)
    ),
    ToolDef(
        name="explain_parcel",
        description=(
            "Explain one parcel's suitability: a per-factor breakdown (raw value, normalised "
            "score, weight, contribution) and which hard exclusions it fails, for a use case."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parcel_id": {"type": "integer", "description": "The parcel id (from score_parcels results)."},
                "use_case": {"type": "string", "enum": _USE_CASE_ENUM, "description": "Scoring profile."},
            },
            "required": ["parcel_id"],  # use_case is defaulted by the wrapper -> optional in the live schema
            "additionalProperties": False,
        },
        surfaces=("text", "voice"),
        phase="explaining",
        result=None,
        parity="props",
    ),
    ToolDef(
        name="grid_context",
        description=(
            "Get the CAISO interconnection-queue summary for Kern County (totals, by technology, "
            "by status). Background context about grid congestion — not part of parcel scoring."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        surfaces=("text", "voice"),
        phase="grid_context",
        result=None,
        parity="props",
    ),
    ToolDef(
        name="focus_parcel",
        description=(
            "Zoom and pan the map to a specific parcel and select it. Use a parcel id from "
            "score_parcels results."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parcel_id": {"type": "integer", "description": "The parcel id to zoom to."}
            },
            "required": ["parcel_id"],
            "additionalProperties": False,
        },
        surfaces=("text", "voice"),
        phase="focusing_parcel",
        result=ResultSpec("focus", "Focus", None, True),
        parity="props",
    ),
    ToolDef(
        name="export_pdf",
        description=(
            "Generate a downloadable PDF report of one or more parcels (score, per-factor "
            "breakdown, grid context). Pass a comma-separated list of parcel ids (e.g. '5,12'), or "
            "leave empty to include all parcels currently shown in the results."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parcel_ids": {
                    "type": "string",
                    "description": "Comma-separated parcel ids; empty means all parcels shown.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        surfaces=("text", "voice"),
        phase="exporting_pdf",
        result=ResultSpec("exportPdf", "ExportPdf", None, True),
        parity="props",
    ),
]

# Voice-only composites (no 1:1 text peer): find_sites ≈ resolve_area + score_parcels, focus_map.
# Documented here so the parity test treats them as legitimate voice surface, not drift.
VOICE_ONLY_TOOLS: tuple[str, ...] = ("find_sites", "focus_map")

# Backwards-compatible derived view: the flat schemas the Gemini/text surface advertises. The live
# tools are still built from the @agent.tool wrappers (see agent.py); this list is the documented
# contract the parity test pins the wrappers to.
TOOL_SPECS: list[dict] = [
    {"name": t.name, "description": t.description, "parameters": t.parameters}
    for t in REGISTRY
    if "text" in t.surfaces
]
