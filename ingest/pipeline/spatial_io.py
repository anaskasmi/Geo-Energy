"""Read local geospatial source files into DuckDB via `ST_Read` (GDAL), with helpers to
introspect and resolve attribute names.

Fetchers acquire a source as a local file (see sources.py / arcgis.py), then read it here.
`ST_Read` returns one row per feature with the geometry in a column named `geom` plus the
source attributes as their own columns (names preserved from the source). Source field
names are not always known ahead of time (e.g. the [CONFIRM] parcels APN field), so
`pick_column` resolves a logical column from a list of candidates, case-insensitively.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .sqlutil import sql_str

GEOM_COLUMN = "geom"  # ST_Read names the geometry column `geom`


def vsizip(zip_path: str | Path, inner: str) -> str:
    """GDAL `/vsizip/` path to a member file inside a zip (e.g. a shapefile in its zip)."""
    return f"/vsizip/{Path(zip_path).resolve()}/{inner}"


def st_read_expr(source: str | Path, *, layer: str | None = None) -> str:
    """A `ST_Read(...)` table expression for a local source (path is escaped)."""
    if layer is not None:
        return f"ST_Read({sql_str(str(source))}, layer={sql_str(layer)})"
    return f"ST_Read({sql_str(str(source))})"


def source_columns(con: Any, read_expr: str) -> list[str]:
    """Column names exposed by a `ST_Read(...)` expression (geometry + attributes)."""
    return [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {read_expr}").fetchall()]


def pick_column(
    present: Sequence[str],
    candidates: Sequence[str],
    *,
    what: str = "column",
    required: bool = True,
) -> str | None:
    """First candidate present in `present` (case-insensitive), returned with its real casing.

    Raises ValueError when nothing matches and `required` (the message lists both the
    candidates tried and the columns actually available — the operator's cue to fix a
    [CONFIRM] field name).
    """
    by_lower = {c.lower(): c for c in present}
    for cand in candidates:
        hit = by_lower.get(cand.lower())
        if hit is not None:
            return hit
    if required:
        raise ValueError(
            f"could not resolve {what}: none of {list(candidates)} found in source "
            f"columns {list(present)}"
        )
    return None
