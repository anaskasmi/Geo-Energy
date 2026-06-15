"""Request models + error contract for the read API (GEO-17).

Responses are GeoJSON / plain dicts assembled in :mod:`app.serialize` (following the skeleton's
plain-dict convention); only the request bodies are validated here with Pydantic so bad input
fails with a 422 before touching DuckDB.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app import scoring

UseCase = Literal["utility_solar", "data_center"]
_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
# Cap total vertices so a pathological polygon can't exhaust memory/CPU in ST_GeomFromGeoJSON
# (request-body size is additionally capped at the nginx edge — GEO-37).
MAX_POSITIONS = 50_000


def _count_positions(coords: object) -> int:
    """Count leaf coordinate positions ([lng, lat, ...]) in a GeoJSON coordinate tree."""
    if isinstance(coords, list):
        if coords and all(isinstance(c, (int, float)) for c in coords):
            return 1
        return sum(_count_positions(c) for c in coords)
    return 0


class Thresholds(BaseModel):
    """Stage-A threshold overrides; any omitted field falls back to the preset default."""

    model_config = {"extra": "forbid"}

    min_acres: float | None = Field(default=None, ge=0)
    max_slope_pct: float | None = Field(default=None, ge=0)
    exclude_sfha: bool | None = None
    apply_optional_exclusions: bool | None = None
    prohibited_zoning: list[str] | None = Field(default=None, max_length=200)


class ScoreRequest(BaseModel):
    """POST /api/score body: a drawn area + scoring profile (weights/thresholds optional)."""

    model_config = {"extra": "forbid"}

    geometry: dict = Field(..., description="A GeoJSON Polygon or MultiPolygon (EPSG:4326)")
    use_case: UseCase = "utility_solar"
    weights: dict[str, float] | None = Field(default=None, description="Partial factor-weight overrides")
    thresholds: Thresholds | None = None
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @field_validator("geometry")
    @classmethod
    def _check_geometry(cls, value: dict) -> dict:
        if not isinstance(value, dict):
            raise ValueError("geometry must be a GeoJSON object")
        gtype = value.get("type")
        if gtype not in _GEOMETRY_TYPES:
            raise ValueError(f"geometry.type must be one of {sorted(_GEOMETRY_TYPES)}, got {gtype!r}")
        coords = value.get("coordinates")
        if not coords:
            raise ValueError("geometry.coordinates is required and must be non-empty")
        positions = _count_positions(coords)
        if positions > MAX_POSITIONS:
            raise ValueError(f"geometry too complex: {positions} vertices exceeds {MAX_POSITIONS}")
        return value

    @field_validator("weights")
    @classmethod
    def _check_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        unknown = set(value) - set(scoring.FACTORS)
        if unknown:
            raise ValueError(f"unknown weight factors: {sorted(unknown)}; valid: {sorted(scoring.FACTORS)}")
        return value
