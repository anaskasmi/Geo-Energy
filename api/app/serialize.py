"""Response assembly for the read API (GEO-17): GeoJSON FeatureCollection + breakdowns.

Pure functions over DuckDB result rows (dicts keyed by column name). Coordinates are rounded to
6 decimals (~0.1 m) to trim payload without losing parcel-level precision.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from app import scoring

COORD_DECIMALS = 6


def round_coords(obj, ndigits: int = COORD_DECIMALS):
    """Recursively round every number in a parsed GeoJSON coordinate tree."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, list):
        return [round_coords(v, ndigits) for v in obj]
    return obj


def _geometry(row: dict) -> dict | None:
    raw = row.get("geometry_json")
    if not raw:
        return None
    geom = json.loads(raw)
    if "coordinates" in geom:
        geom["coordinates"] = round_coords(geom["coordinates"])
    return geom


def _factor_props(row: dict) -> dict:
    """The raw per-factor values carried on each scored feature (the §7 'per-factor props')."""
    return {
        "ghi": row.get("ghi"),
        "mean_slope_pct": row.get("mean_slope_pct"),
        "dist_tx_m": row.get("dist_tx_m"),
        "dist_sub_m": row.get("dist_sub_m"),
        "nearest_sub_kv": row.get("nearest_sub_kv"),
        "poi_competition_mw": row.get("poi_competition_mw"),
        "poi_competition_n": row.get("poi_competition_n"),
        "eia_nearest_m": row.get("eia_nearest_m"),
    }


def _centroid(row: dict) -> list[float] | None:
    lng, lat = row.get("centroid_lng"), row.get("centroid_lat")
    if lng is None or lat is None:
        return None
    return [round(lng, COORD_DECIMALS), round(lat, COORD_DECIMALS)]


def score_feature(row: dict, rank: int) -> dict:
    """One GeoJSON Feature for a scored parcel."""
    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": _geometry(row),
        "properties": {
            "id": row["id"],
            "apn": row.get("apn"),
            "rank": rank,
            "score": row.get("score"),
            "acres": row.get("acres"),
            "zoning_class": row.get("zoning_class"),
            "sfha_flag": row.get("sfha_flag"),
            "centroid": _centroid(row),
            "factors": _factor_props(row),
        },
    }


def score_feature_collection(rows: list[dict], *, offset: int, meta: dict) -> dict:
    """Assemble the /api/score response: a GeoJSON FeatureCollection + a ``meta`` member."""
    features = [score_feature(row, rank=offset + i + 1) for i, row in enumerate(rows)]
    return {"type": "FeatureCollection", "features": features, "meta": {**meta, "count": len(features)}}


def layer_feature_collection(rows: list[dict]) -> dict:
    """Plain GeoJSON FeatureCollection for a static map overlay layer (transmission / substations
    / flood). Each row carries a ``geometry_json`` column (``ST_AsGeoJSON``); every other column
    becomes a feature property. Coordinates are rounded like the scored features."""
    features = []
    for row in rows:
        geom = _geometry(row)
        if geom is None:
            continue
        props = {k: v for k, v in row.items() if k != "geometry_json"}
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": features}


def explain_response(row: dict, *, use_case: str, weights: dict[str, float]) -> dict:
    """Per-factor breakdown + raw values + which Stage-A exclusions a parcel fails."""
    breakdown = [asdict(b) for b in scoring.compute_breakdown(weights, row)]
    exclusions = {
        "min_acres": bool(row.get("excl_min_acres")),
        "sfha": bool(row.get("excl_sfha")),
        "slope": bool(row.get("excl_slope")),
        "zoning": bool(row.get("excl_zoning")),
        "optional": bool(row.get("excl_optional")),
    }
    return {
        "parcel_id": row["id"],
        "apn": row.get("apn"),
        "use_case": use_case,
        "score": row.get("score"),
        "acres": row.get("acres"),
        "zoning_class": row.get("zoning_class"),
        "centroid": _centroid(row),
        "excluded": bool(row.get("excluded")),
        "exclusions": exclusions,
        "factors": breakdown,
        "raw": {col: row.get(col) for col in scoring.RAW_COLUMNS},
    }


def context_response(summary_rows: list[dict]) -> dict:
    """Shape the CAISO Kern queue summary (caiso_queue_summary long form) for /api/context."""
    total: dict = {}
    by_type: list[dict] = []
    by_status: list[dict] = []
    for r in summary_rows:
        item = {
            "key": r.get("key"),
            "n_projects": r.get("n_projects"),
            "total_mw": r.get("total_mw"),
            "active_n_projects": r.get("active_n_projects"),
            "active_total_mw": r.get("active_total_mw"),
        }
        category = r.get("category")
        if category == "total":
            total = {k: v for k, v in item.items() if k != "key"}
        elif category == "by_type":
            by_type.append(item)
        elif category == "by_status":
            by_status.append(item)
    by_type.sort(key=lambda x: (x["total_mw"] or 0), reverse=True)
    by_status.sort(key=lambda x: (x["total_mw"] or 0), reverse=True)
    return {
        "county": "Kern County, CA (06029)",
        "total": total,
        "by_type": by_type,
        "by_status": by_status,
        "note": "CAISO interconnection queue, Kern-scoped. Context only — not part of parcel scoring.",
    }
