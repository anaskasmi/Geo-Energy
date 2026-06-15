"""GEO-11 — EIA-860/860M generators (optional, off the critical path).

`eia_generators`: a points layer of generators (plant lat/lon, capacity, fuel/technology,
status) clipped to Kern County, for cross-checking the CAISO interconnection queue (GEO-7).

This layer is OPTIONAL and must never fail the build (review C8): if no source is configured
it creates an EMPTY table and logs a WARNING rather than raising. Source is a pre-staged CSV
via GEO_EIA860_SOURCE (the live EIA-860 spreadsheet download is deferred). Output is storage
CRS 4326 + GeoParquet (§4). Depends on county_boundary (GEO-3) for the clip.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import clip, config, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_EMPTY_COLS = (
    "id BIGINT, plant_id VARCHAR, name VARCHAR, capacity_mw DOUBLE, fuel VARCHAR, "
    "status VARCHAR, county VARCHAR, lon DOUBLE, lat DOUBLE, geom GEOMETRY"
)
_SELECT_COLS = "id, plant_id, name, capacity_mw, fuel, status, county, lon, lat, geom"


def _csv_read_expr(path: str | Path) -> str:
    return f"read_csv_auto({sql_str(str(path))}, header=true, all_varchar=true, sample_size=-1)"


def _num(col: str | None) -> str:
    if not col:
        return "CAST(NULL AS DOUBLE)"
    return (
        f"TRY_CAST(nullif(regexp_replace(CAST({ident(col)} AS VARCHAR), '[^0-9.-]', '', 'g'), '') "
        "AS DOUBLE)"
    )


def _resolve_source(ctx: FetchContext) -> tuple[str, str] | None:
    """Return (CSV path, label), or None when the optional source is not configured."""
    override = sources.local_override(config.EIA860_SOURCE_ENV)
    if override is not None:
        return str(override), f"local:{override.name}"
    url = os.environ.get(config.EIA860_URL_ENV, config.EIA860_URL).strip()
    if not url:
        return None
    dest = Path(ctx.work_dir) / "eia860_source.csv"
    sources.http_download(url, dest, logger=ctx.logger)
    return str(dest), url


@register
class EiaGeneratorsFetcher(Fetcher):
    name = "eia_generators"
    run_order = 60  # after county_boundary; optional, off the critical path

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        clip.county_bbox(con)  # validates GEO-3 ran
        resolved = _resolve_source(ctx)

        if resolved is None:
            con.execute(
                f"CREATE OR REPLACE TABLE {config.EIA_GENERATORS_TABLE} ({_EMPTY_COLS})"
            )
            log_event(
                log, "eia_generators.skipped", level=logging.WARNING,
                reason=f"no source configured ({config.EIA860_SOURCE_ENV}/{config.EIA860_URL_ENV})",
            )
            return self._emit(ctx, count=0, source="(not configured)")

        source, label = resolved
        read = _csv_read_expr(source)
        cols = spatial_io.source_columns(con, read)
        c_plant = spatial_io.pick_column(cols, config.EIA860_PLANT_ID_FIELDS, what="plant id", required=False)
        c_name = spatial_io.pick_column(cols, config.EIA860_NAME_FIELDS, what="plant name", required=False)
        c_cap = spatial_io.pick_column(cols, config.EIA860_CAPACITY_FIELDS, what="capacity", required=False)
        c_fuel = spatial_io.pick_column(cols, config.EIA860_FUEL_FIELDS, what="fuel/tech", required=False)
        c_status = spatial_io.pick_column(cols, config.EIA860_STATUS_FIELDS, what="status", required=False)
        c_county = spatial_io.pick_column(cols, config.EIA860_COUNTY_FIELDS, what="county", required=False)
        c_lon = spatial_io.pick_column(cols, config.EIA860_LON_FIELDS, what="longitude")
        c_lat = spatial_io.pick_column(cols, config.EIA860_LAT_FIELDS, what="latitude")
        _text = spatial_io.text_or_null

        con.execute(
            f"""
            CREATE OR REPLACE TABLE {config.EIA_GENERATORS_TABLE} AS
            WITH src AS (
                SELECT {_text(c_plant)} AS plant_id, {_text(c_name)} AS name,
                       {_num(c_cap)} AS capacity_mw, {_text(c_fuel)} AS fuel,
                       {_text(c_status)} AS status, {_text(c_county)} AS county,
                       {_num(c_lon)} AS lon, {_num(c_lat)} AS lat
                FROM {read}
            ),
            pts AS (
                SELECT *, ST_Point(lon, lat) AS geom FROM src
                WHERE lon IS NOT NULL AND lat IS NOT NULL
            )
            SELECT row_number() OVER (ORDER BY p.plant_id, p.lat, p.lon) AS id,
                   p.plant_id, p.name, p.capacity_mw, p.fuel, p.status, p.county,
                   p.lon, p.lat, p.geom
            FROM pts p, {clip.COUNTY_TABLE} b
            WHERE ST_Intersects(p.geom, b.geom)
            """
        )

        n = con.execute(f"SELECT count(*) FROM {config.EIA_GENERATORS_TABLE}").fetchone()[0]
        if n == 0:
            # A configured source that clips to nothing is suspicious but NON-fatal (optional
            # layer): warn and keep the empty table rather than aborting the build.
            log_event(log, "eia_generators.empty", level=logging.WARNING, source=label)
        return self._emit(ctx, count=n, source=label)

    def _emit(self, ctx: FetchContext, *, count: int, source: str) -> LayerResult:
        parquet = Path(ctx.work_dir) / config.EIA_GENERATORS_PARQUET
        geoparquet.write_intermediate(
            ctx.con,
            select_sql=f"SELECT {_SELECT_COLS} FROM {config.EIA_GENERATORS_TABLE}",
            out_path=parquet,
            geom_col="geom",
        )
        log_event(ctx.logger, "eia_generators.built", features=count, source=source)
        return LayerResult(
            name=self.name, table=config.EIA_GENERATORS_TABLE, feature_count=count,
            source=source, parquet_path=parquet,
        )
