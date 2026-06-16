"""Two-stage parcel scoring engine (GEO-16).

Stage A — hard exclusions (binary keep/drop):
  * acres < ``min_acres``
  * ``sfha_flag`` (in a FEMA Special Flood Hazard Area)         [when exclude_sfha]
  * ``mean_slope_pct`` > ``max_slope_pct`` (30 m screening slope; unknown/NULL is NOT
    excluded — it is surfaced as "unknown" in the breakdown instead of silently passing)
  * ``zoning_class`` prohibited for the use case (per the build's curated zoning_rules.csv)
  * optional protected-area / open-water / built-up overlays    [when apply_optional_exclusions]

Stage B — weighted suitability over the survivors:
  Each factor is normalised to 0..1 against a fixed reference domain (clamped), oriented so
  higher = more suitable, then combined as a weighted sum and scaled to 0..100. A NULL factor
  imputes the neutral 0.5 (neither rewarded nor penalised). Presets ``utility_solar`` and
  ``data_center`` ship weights that sum to 1.0; callers may override weights/thresholds per
  request (weights are re-normalised to sum to 1.0).

CRS note: every scoring input is a precomputed column — distances (``dist_*_m``) and ``acres``
are already metric (computed in EPSG:26911 by the builder), so the scoring path performs NO
``ST_Transform``. The only geometry op is the R-tree ``ST_Intersects`` candidate prefilter,
which runs in the stored CRS (EPSG:4326) against the 4326 request polygon. See docs/CONVENTIONS.md.

The Stage-B score is computed in SQL (so ``ORDER BY score DESC LIMIT`` runs in the engine over
candidates only); :func:`factor_norm` mirrors the SQL arithmetic exactly so /api/explain can
reproduce the per-factor breakdown in Python. ``test_scoring`` asserts the two agree.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path


# --- Factor catalogue ---------------------------------------------------------
@dataclass(frozen=True)
class Factor:
    """A Stage-B suitability factor: one parcel column normalised over [lo, hi]."""

    key: str          # logical name used in weights + breakdown
    column: str       # parcels column it reads
    direction: str    # "higher" (more is better) | "lower" (less is better)
    lo: float         # value mapping to 0.0 after orientation
    hi: float         # value mapping to 1.0 after orientation
    label: str        # human label for the breakdown
    unit: str         # raw-value unit for the breakdown


# Domains are deliberately fixed (absolute), not min/max over the candidate set, so a parcel's
# score is stable and comparable across queries. Ranges are grounded in Kern County reality:
# GHI ~5.5–6.5 kWh/m²/day; slope exclusion at 15%; grid distance meaningful out to ~20 km.
FACTORS: dict[str, Factor] = {
    "ghi": Factor("ghi", "ghi", "higher", 4.5, 6.5, "Solar resource (GHI)", "kWh/m²/day"),
    "slope": Factor("slope", "mean_slope_pct", "lower", 0.0, 15.0, "Terrain slope", "%"),
    "tx_distance": Factor("tx_distance", "dist_tx_m", "lower", 0.0, 20000.0, "Distance to transmission line", "m"),
    "sub_distance": Factor("sub_distance", "dist_sub_m", "lower", 0.0, 20000.0, "Distance to substation", "m"),
    "sub_capacity": Factor("sub_capacity", "nearest_sub_kv", "higher", 0.0, 500.0, "Nearest substation voltage", "kV"),
    "acreage": Factor("acreage", "acres", "higher", 0.0, 640.0, "Parcel size", "acres"),
    "competition": Factor("competition", "poi_competition_mw", "lower", 0.0, 2000.0, "Queued interconnection competition", "MW"),
}


@dataclass(frozen=True)
class Preset:
    """A named scoring profile: zoning use case + Stage-A thresholds + Stage-B weights."""

    name: str
    zoning_use_case: str          # maps to zoning_rules.csv use_case
    weights: dict[str, float]     # factor key -> weight (sum 1.0)
    min_acres: float
    max_slope_pct: float
    label: str


PRESETS: dict[str, Preset] = {
    "utility_solar": Preset(
        name="utility_solar",
        zoning_use_case="solar",
        weights={
            "ghi": 0.25,
            "slope": 0.20,
            "tx_distance": 0.20,
            "sub_distance": 0.15,
            "acreage": 0.15,
            "sub_capacity": 0.05,
        },
        min_acres=20.0,
        max_slope_pct=15.0,
        label="Utility-scale solar",
    ),
    "data_center": Preset(
        name="data_center",
        zoning_use_case="data_center",
        weights={
            "tx_distance": 0.25,
            "sub_capacity": 0.20,
            "sub_distance": 0.20,
            "slope": 0.15,
            "acreage": 0.10,
            "competition": 0.10,
        },
        min_acres=5.0,
        max_slope_pct=15.0,
        label="Data center",
    ),
}

SUPPORTED_USE_CASES: tuple[str, ...] = tuple(PRESETS)

# Zoning codes are short tokens (e.g. "M-1", "R-2", "OTHER"); validate overrides against this
# shape before inlining them into SQL (the curated codes are also validated at load time).
_ZONE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./-]{0,31}$")

NEUTRAL_NORM = 0.5  # imputed normalised value for a NULL/unknown factor

# --- Affordability blend (GEO-41) --------------------------------------------
# Affordability is NOT a Stage-B SQL Factor: its value comes from a LIVE, request-time, AREA-LEVEL
# lookup (app.landvalue), so it can't be a parcels column. Because the area signal is uniform
# across the drawn area, folding it in is an ORDER-PRESERVING affine blend of the 0..100 suitability
# score — so we apply it post-query in app.agent_tools.score_parcels with no change to the SQL or
# the candidate ranking. Cheaper area -> higher affordability score -> higher blended suitability.
AFFORDABILITY_WEIGHT_DEFAULT = 0.12
# Median owner-occupied home value ($) domain for Kern County, CA (ACS B25077). Lower = more
# affordable. Grounded in Kern reality (county median ~$310k; cheap rural tracts ~$150k, pricier
# town/foothill tracts approach $600k). Clamped, oriented so cheaper -> 1.0.
AFFORDABILITY_MEDIAN_DOMAIN = (150_000.0, 600_000.0)


def affordability_score_from_median(median_usd: float | None) -> float | None:
    """Normalise a median home value ($) to 0..1 (higher = more affordable). ``None`` if unknown.

    Mirrors :func:`factor_norm`'s clamp-then-orient for a "lower is better" factor.
    """
    if median_usd is None or not math.isfinite(median_usd):
        return None
    lo, hi = AFFORDABILITY_MEDIAN_DOMAIN
    ratio = (median_usd - lo) / (hi - lo)
    clamped = min(max(ratio, 0.0), 1.0)
    return 1.0 - clamped  # lower value -> more affordable -> higher score


def blend_affordability(score_0_100: float, affordability_0_1: float, weight: float) -> float:
    """Convex blend of a 0..100 suitability score with a 0..1 affordability score.

    ``(1 - w) * score + w * 100 * affordability``. ``weight`` is clamped to [0, 1]; an out-of-range
    ``affordability`` is clamped to [0, 1]. Order-preserving for a fixed (affordability, weight), so
    applying it after ranking/LIMIT yields the same set as applying it before.
    """
    w = min(max(float(weight), 0.0), 1.0)
    aff = min(max(float(affordability_0_1), 0.0), 1.0)
    return (1.0 - w) * float(score_0_100) + w * 100.0 * aff


class ScoringError(ValueError):
    """Invalid scoring parameters (bad use case, weights, thresholds, or zone codes)."""


# --- Weight / threshold resolution -------------------------------------------
def resolve_weights(use_case: str, overrides: dict[str, float] | None) -> dict[str, float]:
    """Merge request weight overrides onto the preset, then re-normalise to sum 1.0.

    Unknown factor keys or negative weights raise :class:`ScoringError`. An all-zero weight
    set is rejected (nothing to rank on).
    """
    if use_case not in PRESETS:
        raise ScoringError(f"unknown use_case {use_case!r}; expected one of {SUPPORTED_USE_CASES}")
    weights = dict(PRESETS[use_case].weights)
    for key, value in (overrides or {}).items():
        if key not in FACTORS:
            raise ScoringError(f"unknown weight factor {key!r}; expected one of {sorted(FACTORS)}")
        if value < 0 or not math.isfinite(value):
            raise ScoringError(f"weight for {key!r} must be a finite, non-negative number")
        weights[key] = float(value)
    total = sum(weights.values())
    if total <= 0:
        raise ScoringError("weights must sum to a positive value")
    return {k: v / total for k, v in weights.items() if v > 0}


def resolve_thresholds(use_case: str, overrides: dict | None) -> dict:
    """Resolve Stage-A thresholds from the preset + optional request overrides.

    Returns a dict with keys ``min_acres``, ``max_slope_pct``, ``exclude_sfha`` (bool),
    ``apply_optional_exclusions`` (bool). Numeric overrides must be finite and >= 0.
    """
    if use_case not in PRESETS:
        raise ScoringError(f"unknown use_case {use_case!r}; expected one of {SUPPORTED_USE_CASES}")
    preset = PRESETS[use_case]
    out = {
        "min_acres": preset.min_acres,
        "max_slope_pct": preset.max_slope_pct,
        "exclude_sfha": True,
        "apply_optional_exclusions": False,
    }
    overrides = overrides or {}
    for key in ("min_acres", "max_slope_pct"):
        if key in overrides and overrides[key] is not None:
            val = float(overrides[key])
            if val < 0 or not math.isfinite(val):
                raise ScoringError(f"threshold {key!r} must be a finite, non-negative number")
            out[key] = val
    for key in ("exclude_sfha", "apply_optional_exclusions"):
        if key in overrides and overrides[key] is not None:
            out[key] = bool(overrides[key])
    return out


def load_zoning_rules(path: str | Path) -> dict[str, dict[str, list[str]]]:
    """Load the curated zoning rules CSV into ``{use_case: {permission: [codes]}}``.

    The CSV (FR-A2, written per build) has columns ``zone_code, zone_name, use_case,
    permission, basis``. Returns an empty dict if the file is missing or unreadable (the
    scorer then treats zoning as a non-filter and flags it). Malformed rows are skipped.
    """
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("zone_code") or "").strip()
            use = (row.get("use_case") or "").strip()
            perm = (row.get("permission") or "").strip()
            if not code or not use or not perm:
                continue
            out.setdefault(use, {}).setdefault(perm, []).append(code)
    return out


def prohibited_codes(
    zoning_rules: dict | None, use_case: str, override: list[str] | None
) -> list[str]:
    """The list of prohibited zone codes for this use case.

    Uses the request override when provided (validated against the zone-code shape), else the
    build's curated rules (``zoning_rules[use_case]['prohibited']``). Returns ``[]`` when no
    rules are available (e.g. a build without a zoning_rules.csv) — zoning is then not a Stage-A
    filter and the caller flags it in the response meta.
    """
    if override is not None:
        codes = [c.strip() for c in override if c and c.strip()]
    elif zoning_rules and use_case in PRESETS:
        codes = list(zoning_rules.get(PRESETS[use_case].zoning_use_case, {}).get("prohibited", ()))
    else:
        codes = []
    for code in codes:
        if not _ZONE_CODE_RE.match(code):
            raise ScoringError(f"invalid zone code {code!r}")
    return sorted(set(codes))


# --- Stage-B normalisation (Python mirror of the SQL) -------------------------
def factor_norm(factor: Factor, value: float | None) -> float:
    """Normalise a raw factor value to 0..1, oriented so higher = more suitable.

    NULL/unknown imputes :data:`NEUTRAL_NORM`. Mirrors the SQL emitted by
    :func:`_factor_sql` exactly (clamp then orient).
    """
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return NEUTRAL_NORM
    ratio = (value - factor.lo) / (factor.hi - factor.lo)
    clamped = min(max(ratio, 0.0), 1.0)
    return clamped if factor.direction == "higher" else 1.0 - clamped


def score_value(weights: dict[str, float], raw: dict) -> float:
    """Stage-B score 0..100 for one parcel from its raw factor values (Python path)."""
    total = 0.0
    for key, weight in weights.items():
        total += weight * factor_norm(FACTORS[key], raw.get(FACTORS[key].column))
    return total * 100.0


@dataclass(frozen=True)
class FactorBreakdown:
    key: str
    label: str
    unit: str
    raw: float | None
    normalized: float
    weight: float
    contribution: float   # weight * normalized * 100 (points contributed to the 0..100 score)
    known: bool           # False when the raw value was NULL (neutral imputed)


def compute_breakdown(weights: dict[str, float], raw: dict) -> list[FactorBreakdown]:
    """Per-factor breakdown for /api/explain, ordered by contribution descending."""
    items: list[FactorBreakdown] = []
    for key, weight in weights.items():
        factor = FACTORS[key]
        value = raw.get(factor.column)
        # A NaN/Inf reads as unknown (it was scored neutrally), not as a known value.
        known = value is not None and not (isinstance(value, float) and not math.isfinite(value))
        norm = factor_norm(factor, value)
        items.append(
            FactorBreakdown(
                key=key,
                label=factor.label,
                unit=factor.unit,
                raw=value if known else None,
                normalized=round(norm, 4),
                weight=round(weight, 4),
                contribution=round(weight * norm * 100.0, 2),
                known=known,
            )
        )
    items.sort(key=lambda b: b.contribution, reverse=True)
    return items


# --- SQL generation -----------------------------------------------------------
def _factor_sql(factor: Factor) -> str:
    """SQL expression for one factor's normalised 0..1 value (NULL -> neutral).

    The ``CASE WHEN col IS NULL`` guard is load-bearing: DuckDB's ``GREATEST(NULL, 0.0)``
    returns ``0.0`` (it ignores NULLs), so a bare ``COALESCE(..., 0.5)`` would never fire —
    a NULL lower-better factor would collapse to a perfect 1.0 and a NULL higher-better factor
    to a worst 0.0. We short-circuit NULL to the neutral value, matching :func:`factor_norm`.
    """
    span = factor.hi - factor.lo
    ratio = f"(({factor.column} - {factor.lo!r}) / {span!r})"
    clamped = f"LEAST(GREATEST({ratio}, 0.0), 1.0)"
    oriented = clamped if factor.direction == "higher" else f"(1.0 - {clamped})"
    # NULL OR non-finite (NaN/±Inf) -> neutral, matching factor_norm(). DuckDB orders NaN as the
    # largest value, so without this a NaN would clamp to an extreme instead of the neutral 0.5.
    guard = f"{factor.column} IS NULL OR NOT isfinite({factor.column})"
    return f"CASE WHEN {guard} THEN {NEUTRAL_NORM!r} ELSE {oriented} END"


# Columns every scored row returns (raw values double as the /api/score per-factor props and
# the /api/explain raw values). Geometry + centroid are rounded to 6 decimals at serialisation.
RAW_COLUMNS: tuple[str, ...] = (
    "id", "apn", "acres",
    "mean_slope_pct", "mean_slope_pct_final", "ghi",
    "dist_tx_m", "dist_sub_m", "nearest_sub_kv",
    "poi_competition_mw", "poi_competition_n",
    "sfha_flag", "zoning_class",
    "excl_protected_area", "excl_open_water", "excl_built_up",
    "eia_nearest_m",
)


def build_score_sql(
    *,
    weights: dict[str, float],
    thresholds: dict,
    prohibited: list[str],
    polygon: bool = False,
    parcel_id: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[str, dict]:
    """Build the parameterised scoring query and its named-parameter dict.

    Exactly one of ``polygon`` / ``parcel_id`` selects the candidate set:
      * ``polygon=True``  -> ``ST_Intersects(geom, ST_GeomFromGeoJSON($poly))`` (R-tree prefilter)
      * ``parcel_id=True`` -> ``id = $parcel_id`` (single parcel, for /explain; no Stage-A filter)

    The drawn polygon / ``parcel_id`` and the ``limit``/``offset`` are BOUND parameters (``$name``).
    Scalar thresholds, weights and factor domains are server-validated numbers (each ``float()``-cast
    / range-checked) inlined into the arithmetic — NOT bound parameters. Prohibited zone codes are
    validated against :data:`_ZONE_CODE_RE` then inlined as quoted literals.

    For /explain (parcel_id) Stage-A is reported, not applied, so the breakdown can show which
    exclusions a specific parcel fails; for /score (polygon) Stage-A is a hard outer filter.

    R-tree note (verified by EXPLAIN, asserted in test_scoring): DuckDB's spatial optimiser only
    rewrites a scan to ``RTREE_INDEX_SCAN`` when ``ST_Intersects`` is the SOLE predicate on that
    scan. So the spatial predicate is isolated in a candidate-id subquery (where the R-tree
    fires); Stage-A flags + the Stage-B score are computed in the ``scored`` CTE over those
    candidates, and the hard Stage-A filter is applied in the outer query on the derived flags —
    keeping the expensive geometry test on R-tree candidates only.
    """
    params: dict = {}
    score_expr = " + ".join(f"{weight!r} * {_factor_sql(FACTORS[key])}" for key, weight in weights.items())
    select_cols = ", ".join(RAW_COLUMNS)
    excl_names = ("excl_min_acres", "excl_sfha", "excl_slope", "excl_zoning", "excl_optional")
    excluded_expr = " OR ".join(excl_names)

    # Stage-A exclusion booleans (computed for every candidate; the outer query filters on them
    # for /score and reports them for /explain). EVERY term is NULL-safe so the combined
    # ``excluded`` is never NULL — a NULL term would make ``NOT (excluded)`` evaluate to NULL and
    # silently drop a valid parcel (real bug: NULL zoning_class + non-empty prohibited list). The
    # policy mirrors slope: an UNKNOWN value never triggers an exclusion (it is surfaced in the
    # breakdown instead).
    excl_parts = [
        f"(acres IS NOT NULL AND acres < {float(thresholds['min_acres'])!r}) AS excl_min_acres",
        (
            "(COALESCE(sfha_flag, FALSE)) AS excl_sfha"
            if thresholds["exclude_sfha"]
            else "FALSE AS excl_sfha"
        ),
        f"(mean_slope_pct IS NOT NULL AND mean_slope_pct > {float(thresholds['max_slope_pct'])!r}) AS excl_slope",
    ]
    if prohibited:
        in_list = ", ".join("'" + c.replace("'", "''") + "'" for c in prohibited)
        # NULL/unmapped zoning_class is NOT prohibited (matches the curated rules' OTHER->conditional).
        excl_parts.append(f"(zoning_class IS NOT NULL AND zoning_class IN ({in_list})) AS excl_zoning")
    else:
        excl_parts.append("FALSE AS excl_zoning")
    if thresholds["apply_optional_exclusions"]:
        excl_parts.append(
            "(COALESCE(excl_protected_area, FALSE) OR COALESCE(excl_open_water, FALSE) "
            "OR COALESCE(excl_built_up, FALSE)) AS excl_optional"
        )
    else:
        excl_parts.append("FALSE AS excl_optional")
    excl_select = ",\n    ".join(excl_parts)

    if polygon:
        # Sole-spatial subquery -> R-tree; outer restricts parcels to these candidate ids.
        candidate_where = (
            "id IN (SELECT id FROM parcels WHERE ST_Intersects(geom, ST_GeomFromGeoJSON($poly)))"
        )
        params["poly"] = None  # caller fills in
    elif parcel_id:
        candidate_where = "id = $parcel_id"
        params["parcel_id"] = None
    else:  # pragma: no cover - guarded by callers
        raise ScoringError("build_score_sql requires polygon=True or parcel_id=True")

    # The CTE keeps the UNROUNDED score (score_raw) so ORDER BY ranks by true suitability — the
    # displayed `score` is rounded only in the final projection (not before ordering). geom is
    # carried through but ST_AsGeoJSON runs only in the final projection, AFTER LIMIT/OFFSET, so
    # geometry is serialised for the returned page only (not every Stage-A survivor).
    scored_select = (
        f"SELECT {select_cols}, geom,\n"
        "    ST_X(centroid_4326) AS centroid_lng, ST_Y(centroid_4326) AS centroid_lat,\n"
        f"    {excl_select},\n"
        f"    100.0 * ({score_expr}) AS score_raw\n"
        "  FROM parcels\n"
        f"  WHERE {candidate_where}"
    )
    final_projection = (
        f"{', '.join(RAW_COLUMNS)}, centroid_lng, centroid_lat, {', '.join(excl_names)},\n"
        "  round(score_raw, 1) AS score, ST_AsGeoJSON(geom) AS geometry_json,\n"
        f"  ({excluded_expr}) AS excluded"
    )

    if polygon:
        page = ""
        if limit is not None:
            page = "LIMIT $limit OFFSET $offset"
            params["limit"] = int(limit)
            params["offset"] = int(offset)
        sql = (
            f"WITH scored AS (\n  {scored_select}\n),\n"
            f"ranked AS (\n  SELECT * FROM scored WHERE NOT ({excluded_expr})\n"
            f"  ORDER BY score_raw DESC, id\n  {page}\n)\n"
            f"SELECT {final_projection}\nFROM ranked\nORDER BY score_raw DESC, id\n"
        )
    else:  # parcel_id: one row, Stage-A reported (not filtered)
        sql = f"WITH scored AS (\n  {scored_select}\n)\nSELECT {final_projection}\nFROM scored\n"
    return sql, params
