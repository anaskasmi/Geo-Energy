"""GeoParquet intermediate convention (§4).

Layer fetchers emit intermediates as **GeoParquet 1.1** (DuckDB's Parquet writer emits
the file-level `geo` metadata automatically for a geometry-typed column: WKB encoding,
geometry types, and the column bbox; CRS is omitted, which per the GeoParquet spec means
the default OGC:CRS84 == EPSG:4326 lon/lat — our storage CRS). We additionally carry an
explicit `bbox` STRUCT(xmin, ymin, xmax, ymax) column for cheap spatial pre-filtering
(row-group pruning) from plain SQL without decoding geometry.

DuckDB (with spatial loaded) reads these straight back as a GEOMETRY column via
`read_intermediate_sql`.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .sqlutil import ident as _ident
from .sqlutil import sql_str as _sql_str


def bbox_struct_sql(geom_expr: str) -> str:
    """SQL for the `bbox` STRUCT covering column from a geometry expression."""
    return (
        "{"
        f"'xmin': ST_XMin({geom_expr}), 'ymin': ST_YMin({geom_expr}), "
        f"'xmax': ST_XMax({geom_expr}), 'ymax': ST_YMax({geom_expr})"
        "}"
    )


def write_intermediate(
    con: "duckdb.DuckDBPyConnection",
    *,
    select_sql: str,
    out_path: str | Path,
    geom_col: str = "geom",
) -> Path:
    """Write a GeoParquet intermediate from a SELECT yielding a geometry column.

    `select_sql` must produce a `{geom_col}` geometry column already in EPSG:4326.
    The geometry is preserved as a GEOMETRY column named `geometry` (so DuckDB writes
    GeoParquet `geo` metadata), alongside an explicit `bbox` STRUCT for pre-filtering.
    """
    out_path = Path(out_path)
    geom = _ident(geom_col)
    bbox = bbox_struct_sql(geom)
    con.execute(
        f"""
        COPY (
            SELECT * EXCLUDE ({geom}),
                   {geom} AS geometry,
                   {bbox} AS bbox
            FROM ({select_sql})
        ) TO {_sql_str(out_path)} (FORMAT PARQUET);
        """
    )
    return out_path


def read_intermediate_sql(path: str | Path, *, geom_alias: str = "geom") -> str:
    """SQL to read an intermediate back (geometry returns as a GEOMETRY column)."""
    geom = _ident(geom_alias)
    return (
        f"SELECT * EXCLUDE (geometry), geometry AS {geom} "
        f"FROM read_parquet({_sql_str(Path(path))})"
    )


# CRS that intermediate geometry is stored in, by convention (GeoParquet default CRS84).
INTERMEDIATE_CRS = config.CRS_STORAGE
