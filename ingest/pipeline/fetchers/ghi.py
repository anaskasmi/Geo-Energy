"""GEO-10 — NREL solar GHI grid (sampled over Kern County).

One fetcher that produces `ghi_grid`: a regular grid of points over the county (clipped to
the polygon), each carrying annual `avg_ghi`, `avg_dni` and `avg_lat_tilt` (kWh/m²/day). The
per-parcel GHI is sampled from this grid in enrichment (GEO-13) — never queried per parcel.

Two acquisition paths, converging on a CSV so DuckDB reads them identically:
* live — build the grid, query NREL solar_resource for each point (throttled to the key's
  1,000 req/hr limit, on-disk cached so re-runs don't re-query), write a CSV. See nrel.py.
* offline/test — a pre-staged CSV (lon,lat,avg_ghi,avg_dni,avg_lat_tilt; 4326) via
  GEO_NREL_GHI_SOURCE.

Output is storage CRS 4326 + GeoParquet (§4). Depends on the county_boundary table (GEO-3,
run_order=0) for the sample grid and the clip.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from .. import clip, config, geoparquet, nrel, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_Bbox = tuple[float, float, float, float]
_CSV_HEADER = ("lon", "lat", "avg_ghi", "avg_dni", "avg_lat_tilt")


def _csv_read_expr(path: str | Path) -> str:
    return f"read_csv_auto({sql_str(str(path))}, header=true, sample_size=-1)"


def _num(col: str | None) -> str:
    return f"TRY_CAST({ident(col)} AS DOUBLE)" if col else "CAST(NULL AS DOUBLE)"


def _build_grid(con: Any, bbox: _Bbox, *, spacing: float) -> list[tuple[float, float]]:
    """Regular lon/lat grid over the county bbox, clipped to the county polygon."""
    xmin, ymin, xmax, ymax = bbox
    candidates: list[tuple[float, float]] = []
    y = ymin
    while y <= ymax + 1e-9:
        x = xmin
        while x <= xmax + 1e-9:
            candidates.append((round(x, 6), round(y, 6)))
            x += spacing
        y += spacing
    if not candidates:
        return []
    con.execute("CREATE OR REPLACE TEMP TABLE _ghi_cand (lon DOUBLE, lat DOUBLE)")
    con.executemany("INSERT INTO _ghi_cand VALUES (?, ?)", candidates)
    kept = con.execute(
        f"SELECT c.lon, c.lat FROM _ghi_cand c, {clip.COUNTY_TABLE} b "
        f"WHERE ST_Intersects(ST_Point(c.lon, c.lat), b.geom)"
    ).fetchall()
    con.execute("DROP TABLE _ghi_cand")
    return [(float(lo), float(la)) for lo, la in kept]


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_CSV_HEADER))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in _CSV_HEADER})
    return path


def _resolve_source(ctx: FetchContext, *, bbox: _Bbox) -> tuple[str, str]:
    """Return (CSV path, label). Local override wins; else build the grid and query NREL."""
    override = sources.local_override(config.NREL_GHI_SOURCE_ENV)
    if override is not None:
        return str(override), f"local:{override.name}"

    url = os.environ.get(config.NREL_GHI_URL_ENV, config.NREL_SOLAR_RESOURCE_URL).strip()
    if not url:
        raise sources.SourceError(
            f"no source for ghi_grid: set {config.NREL_GHI_SOURCE_ENV} (local CSV) or "
            f"{config.NREL_GHI_URL_ENV}"
        )
    points = _build_grid(ctx.con, bbox, spacing=config.NREL_GHI_GRID_SPACING_DEG)
    if not points:
        raise sources.SourceError("ghi_grid: county produced no sample points (empty grid)")
    cache_dir = os.environ.get(config.NREL_GHI_CACHE_ENV, "").strip() or str(
        ctx.settings.data_dir / ".cache" / "nrel"
    )
    rows = nrel.fetch_grid(
        points, api_key=ctx.settings.nrel_api_key, url=url,
        rate_per_hour=config.NREL_RATE_PER_HOUR, cache_dir=cache_dir, logger=ctx.logger,
    )
    if not rows:
        raise sources.SourceError("ghi_grid: NREL returned no usable GHI for the county grid")
    csv_path = Path(ctx.work_dir) / "ghi_grid_source.csv"
    _write_csv(csv_path, rows)
    return str(csv_path), url


@register
class GhiGridFetcher(Fetcher):
    name = "ghi_grid"
    run_order = 51  # after county_boundary (0); independent of the other layers

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the sample-grid extent
        source, label = _resolve_source(ctx, bbox=bbox)

        read = _csv_read_expr(source)
        cols = spatial_io.source_columns(con, read)
        c_lon = spatial_io.pick_column(cols, config.NREL_LON_FIELDS, what="ghi grid longitude")
        c_lat = spatial_io.pick_column(cols, config.NREL_LAT_FIELDS, what="ghi grid latitude")
        c_ghi = spatial_io.pick_column(cols, config.NREL_GHI_FIELDS, what="avg_ghi", required=False)
        c_dni = spatial_io.pick_column(cols, config.NREL_DNI_FIELDS, what="avg_dni", required=False)
        c_tilt = spatial_io.pick_column(cols, config.NREL_LAT_TILT_FIELDS, what="avg_lat_tilt", required=False)

        # Clip to the county polygon so the stored grid is "over the county" regardless of
        # whether the source CSV was pre-clipped (the live path already clips; a staged CSV
        # may not). Points must carry GHI (the value enrichment samples).
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {config.GHI_GRID_TABLE} AS
            WITH src AS (
                SELECT {_num(c_lon)} AS lon, {_num(c_lat)} AS lat,
                       {_num(c_ghi)} AS avg_ghi, {_num(c_dni)} AS avg_dni,
                       {_num(c_tilt)} AS avg_lat_tilt
                FROM {read}
            ),
            pts AS (
                SELECT lon, lat, avg_ghi, avg_dni, avg_lat_tilt, ST_Point(lon, lat) AS geom
                FROM src
                WHERE lon IS NOT NULL AND lat IS NOT NULL AND avg_ghi IS NOT NULL
            )
            SELECT row_number() OVER (ORDER BY p.lat, p.lon) AS id,
                   p.lon, p.lat, p.avg_ghi, p.avg_dni, p.avg_lat_tilt, p.geom
            FROM pts p, {clip.COUNTY_TABLE} b
            WHERE ST_Intersects(p.geom, b.geom)
            """
        )

        n = con.execute(f"SELECT count(*) FROM {config.GHI_GRID_TABLE}").fetchone()[0]
        if n == 0:
            raise sources.SourceError(
                f"ghi_grid from {label!r} yielded 0 in-county points with GHI"
            )

        parquet = Path(ctx.work_dir) / config.GHI_GRID_PARQUET
        geoparquet.write_intermediate(
            con,
            select_sql=(
                f"SELECT id, lon, lat, avg_ghi, avg_dni, avg_lat_tilt, geom "
                f"FROM {config.GHI_GRID_TABLE}"
            ),
            out_path=parquet,
            geom_col="geom",
        )

        mean_ghi = con.execute(
            f"SELECT round(avg(avg_ghi), 3) FROM {config.GHI_GRID_TABLE}"
        ).fetchone()[0]
        log_event(log, "ghi_grid.built", points=n, mean_ghi=mean_ghi, source=label)
        return LayerResult(
            name=self.name,
            table=config.GHI_GRID_TABLE,
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"mean_ghi": mean_ghi},
        )
