"""GEO-3 — Kern County boundary fetcher (the shared clip/sample geometry).

Produces the `county_boundary` table: the Kern County polygon (GEOID 06029) in storage CRS
4326, plus reprojected UTM 11N (26911) and CA Albers (3310) geometries, plus a 4326 bbox
envelope. Downstream "clip to county" fetchers (transmission, flood, slope, GHI) read this
table, so it runs first (run_order=0). Spec §2.

Source: the Census cartographic boundary county file (the data pygris wraps). We read it
directly with DuckDB `ST_Read` (GDAL) rather than pulling in geopandas/pygris, to keep the
ingest image slim. Census ships NAD83 (4269); we reproject to 4326 for storage. A
pre-staged local file (GeoJSON, assumed 4326) can be supplied via `GEO_COUNTY_SOURCE`.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import config, crs, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_SOURCE_LABEL = "Census cartographic boundary (counties, 1:500k)"


@register
class CountyBoundaryFetcher(Fetcher):
    name = "county_boundary"
    run_order = 0

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, cfg, log = ctx.con, ctx.settings, ctx.logger
        source, source_crs = self._resolve_source(ctx)
        read_expr = spatial_io.st_read_expr(source)

        cols = spatial_io.source_columns(con, read_expr)
        c_geoid = spatial_io.pick_column(cols, ["GEOID", "GEOID20", "AFFGEOID"], what="GEOID")
        c_state = spatial_io.pick_column(cols, ["STATEFP", "STATEFP20", "STATE_FIPS"], what="state FIPS")
        c_county = spatial_io.pick_column(cols, ["COUNTYFP", "COUNTYFP20", "COUNTY_FIPS"], what="county FIPS")
        c_name = spatial_io.pick_column(cols, ["NAME", "NAMELSAD"], what="county name", required=False)
        name_expr = ident(c_name) if c_name else "NULL"

        geom_4326 = crs.ensure_storage_sql(spatial_io.GEOM_COLUMN, from_crs=source_crs)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE county_boundary AS
            WITH src AS (
                SELECT {ident(c_geoid)}  AS geoid,
                       {name_expr}       AS name,
                       {ident(c_state)}  AS statefp,
                       {ident(c_county)} AS countyfp,
                       {geom_4326}       AS geom
                FROM {read_expr}
                WHERE {ident(c_state)} = {sql_str(config.KERN_STATE_FIPS)}
                  AND {ident(c_county)} = {sql_str(config.KERN_COUNTY_FIPS)}
            )
            SELECT geoid, name, statefp, countyfp, geom,
                   {crs.to_metric_sql("geom", to_crs=config.CRS_METRIC_UTM)}    AS geom_utm,
                   {crs.to_metric_sql("geom", to_crs=config.CRS_METRIC_ALBERS)} AS geom_albers,
                   ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin,
                   ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax
            FROM src
            """
        )

        n = con.execute("SELECT count(*) FROM county_boundary").fetchone()[0]
        if n != 1:
            raise sources.SourceError(
                f"expected exactly one county (STATEFP={config.KERN_STATE_FIPS}, "
                f"COUNTYFP={config.KERN_COUNTY_FIPS}); source yielded {n}"
            )
        geoid, bbox = con.execute(
            "SELECT geoid, [bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax] FROM county_boundary"
        ).fetchone()
        if geoid != config.KERN_GEOID:
            raise sources.SourceError(f"resolved county GEOID {geoid!r}, expected {config.KERN_GEOID!r}")

        parquet = Path(ctx.work_dir) / "county_boundary.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT geoid, name, statefp, countyfp, geom FROM county_boundary",
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "county_boundary.built", geoid=geoid, bbox=bbox, source=source_crs)
        return LayerResult(
            name=self.name,
            table="county_boundary",
            feature_count=n,
            source=_SOURCE_LABEL,
            parquet_path=parquet,
            extra={"geoid": geoid, "bbox": bbox},
        )

    def _resolve_source(self, ctx: FetchContext) -> tuple[str, int]:
        """Return (ST_Read source, source CRS). Local override wins; else download Census."""
        override = sources.local_override(config.COUNTY_SOURCE_ENV)
        if override is not None:
            crs_code = int(os.environ.get(config.COUNTY_SOURCE_CRS_ENV, config.CRS_STORAGE))
            return str(override), crs_code

        url = os.environ.get(config.COUNTY_URL_ENV, config.COUNTY_CB_URL)
        zip_path = Path(ctx.work_dir) / "county_cb.zip"
        sources.http_download(url, zip_path, logger=ctx.logger)
        return spatial_io.vsizip(zip_path, config.COUNTY_CB_SHP), config.COUNTY_CB_CRS
