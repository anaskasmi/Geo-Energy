"""CRS / reprojection helpers — the single place axis order is handled.

Policy (docs/CONVENTIONS.md): store/serve EPSG:4326; compute in EPSG:26911 (UTM 11N) or
EPSG:3310 (CA Albers). **Every transform passes `always_xy := true`** so lon/lat (x,y)
order is never silently swapped. Always go through these helpers — never open-code an
`ST_Transform`.
"""

from __future__ import annotations

from functools import lru_cache

from .config import CRS_STORAGE, DEFAULT_METRIC_CRS


def epsg(code: int) -> str:
    return f"EPSG:{int(code)}"


def transform_sql(geom_expr: str, *, to_crs: int = DEFAULT_METRIC_CRS, from_crs: int = CRS_STORAGE) -> str:
    """DuckDB `ST_Transform` SQL with `always_xy := true` enforced.

    >>> transform_sql("geom", to_crs=26911)
    "ST_Transform(geom, 'EPSG:4326', 'EPSG:26911', always_xy := true)"
    """
    return (
        f"ST_Transform({geom_expr}, '{epsg(from_crs)}', '{epsg(to_crs)}', "
        f"always_xy := true)"
    )


def to_metric_sql(geom_expr: str, *, to_crs: int = DEFAULT_METRIC_CRS) -> str:
    """4326 (stored) → a metric CRS, for distance/area/slope compute."""
    return transform_sql(geom_expr, to_crs=to_crs, from_crs=CRS_STORAGE)


def to_storage_sql(geom_expr: str, *, from_crs: int = DEFAULT_METRIC_CRS) -> str:
    """A metric CRS → 4326 (stored/served)."""
    return transform_sql(geom_expr, to_crs=CRS_STORAGE, from_crs=from_crs)


@lru_cache(maxsize=None)
def transformer(from_crs: int = CRS_STORAGE, to_crs: int = DEFAULT_METRIC_CRS):
    """A cached pyproj Transformer (always_xy=True) for Python-side reprojection."""
    from pyproj import Transformer

    return Transformer.from_crs(epsg(from_crs), epsg(to_crs), always_xy=True)
