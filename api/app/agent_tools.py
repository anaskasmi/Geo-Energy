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
from typing import Any

import duckdb

from app import scoring, serialize
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
) -> dict:
    """Rank parcels in an area for a use case (calls the scoring engine; never computes geometry).

    Returns the same ranked GeoJSON ``FeatureCollection`` (+ ``meta``) as POST /api/score. Geometry
    comes from ``area_ref`` (preferred) or an explicit ``geometry``. Raises :class:`ToolError` for
    bad parameters (mapped from :class:`scoring.ScoringError`).
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
    return serialize.score_feature_collection(rows, offset=offset, meta=meta)


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


# --- Provider-agnostic, FLAT tool schemas (Pydantic-AI / Gemini / OpenAI all consume these) ----
# Gemini's OpenAPI subset rejects unions/records/deep nesting, so every parameter here is a
# scalar. GEO-21 binds `cur` + `zoning_rules` server-side; the model only ever supplies these.
_USE_CASE_ENUM = list(scoring.SUPPORTED_USE_CASES)

TOOL_SPECS: list[dict] = [
    {
        "name": "resolve_area",
        "description": (
            "Resolve a place in Kern County, CA to a search area. Accepts a city/area name "
            "(e.g. 'Mojave', 'Bakersfield'), 'Kern County' for the whole county, a "
            "'minLng,minLat,maxLng,maxLat' bounding box, or a 'lng,lat' point. Returns an "
            "area_ref token to pass to score_parcels. Call this BEFORE score_parcels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Place name, bounding box, or point."}
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "score_parcels",
        "description": (
            "Rank parcels in a resolved area by suitability (0-100) for a use case. Returns a "
            "ranked GeoJSON FeatureCollection. Geometry is NOT passed here — resolve the area "
            "first and pass its area_ref. The engine computes all geometry and scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "area_ref": {"type": "string", "description": "Token from resolve_area."},
                "use_case": {"type": "string", "enum": _USE_CASE_ENUM, "description": "Scoring profile."},
                "min_acres": {"type": "number", "description": "Optional minimum parcel size override (acres)."},
                "max_slope_pct": {"type": "number", "description": "Optional maximum slope override (percent)."},
                "limit": {"type": "integer", "description": "Max results (1-1000, default 200)."},
            },
            "required": ["area_ref", "use_case"],
            "additionalProperties": False,
        },
    },
    {
        "name": "explain_parcel",
        "description": (
            "Explain one parcel's suitability: a per-factor breakdown (raw value, normalised "
            "score, weight, contribution) and which hard exclusions it fails, for a use case."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parcel_id": {"type": "integer", "description": "The parcel id (from score_parcels results)."},
                "use_case": {"type": "string", "enum": _USE_CASE_ENUM, "description": "Scoring profile."},
            },
            "required": ["parcel_id", "use_case"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grid_context",
        "description": (
            "Get the CAISO interconnection-queue summary for Kern County (totals, by technology, "
            "by status). Background context about grid congestion — not part of parcel scoring."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
