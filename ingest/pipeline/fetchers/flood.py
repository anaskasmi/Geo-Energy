"""GEO-8 — FEMA NFHL Special Flood Hazard Areas (clipped to Kern County).

One fetcher over the National Flood Hazard Layer "Flood Hazard Zones" polygon layer
(S_FLD_HAZ_AR, MapServer layer 28):

* `flood_sfha` — Special Flood Hazard Area polygons (FLD_ZONE A%/V%), truncated to the
  county polygon. Drives the parcel `sfha_flag` (a Stage-A exclusion) in the parcels×flood
  enrichment join (GEO-13); the join itself is done there, not here.

NFHL is a national layer, so the source is prefiltered server-side to the county bounding
box (the ArcGIS envelope filter) *and* the SFHA where-clause, then clipped precisely to the
county polygon in DuckDB (via the shared county_boundary table from GEO-3, run_order=0). The
SFHA filter is re-applied in DuckDB so the local-override path (which reads the whole file)
selects identically. Output is storage CRS 4326 + GeoParquet (§4).

A pre-staged local file (GeoJSON, 4326) can be supplied via `GEO_FLOOD_SOURCE` for
offline/air-gapped runs and tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import arcgis, clip, config, crs, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_GEOM = spatial_io.GEOM_COLUMN
_text = spatial_io.text_or_null


def _make_valid(geom_expr: str) -> str:
    return f"CASE WHEN ST_IsValid({geom_expr}) THEN {geom_expr} ELSE ST_MakeValid({geom_expr}) END"


def _is_sfha_sql(zone_col: str, tf_col: str | None) -> str:
    """TRUE when a row is a Special Flood Hazard Area: FLD_ZONE begins A or V, EXCLUDING the
    'AREA NOT INCLUDED' sentinel (A%-prefixed but not an SFHA). When the authoritative
    SFHA_TF flag is present, a row explicitly marked 'F' (not-SFHA) is also excluded."""
    z = f"upper(trim({ident(zone_col)}))"
    base = (
        f"(({z} LIKE 'A%' OR {z} LIKE 'V%') AND {z} <> upper({sql_str(config.FLOOD_ANI_VALUE)}))"
    )
    if tf_col:
        tf = f"upper(trim({ident(tf_col)}))"
        return f"({base} AND ({tf} IS NULL OR {tf} <> 'F'))"
    return base


def _resolve_source(
    ctx: FetchContext, *, bbox: tuple[float, float, float, float]
) -> tuple[str, int, str]:
    """Return (ST_Read source, source CRS, human label). Local override wins; else fetch the
    NFHL FeatureServer prefiltered to the county `bbox` and the SFHA where-clause (outSR=4326)."""
    override = sources.local_override(config.FLOOD_SOURCE_ENV)
    if override is not None:
        crs_code = int(os.environ.get(config.FLOOD_SOURCE_CRS_ENV, config.CRS_STORAGE))
        return str(override), crs_code, f"local:{override.name}"

    url = os.environ.get(config.FLOOD_URL_ENV, config.FLOOD_NFHL_URL).strip()
    if not url:
        raise sources.SourceError(
            f"no source for flood: set {config.FLOOD_SOURCE_ENV} (local file) or {config.FLOOD_URL_ENV}"
        )
    dest = Path(ctx.work_dir) / "flood_source.geojson"
    arcgis.fetch_featureserver_geojson(
        url, dest, where=config.FLOOD_SFHA_WHERE, bbox=bbox, logger=ctx.logger
    )
    return str(dest), config.CRS_STORAGE, url


@register
class FloodSfhaFetcher(Fetcher):
    name = "flood_sfha"
    run_order = 22  # after county_boundary (0) and the other clip layers (20/21)

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the server-side prefilter
        source, source_crs, label = _resolve_source(ctx, bbox=bbox)
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_zone = spatial_io.pick_column(cols, config.FLOOD_ZONE_FIELDS, what="flood zone")
        c_subty = spatial_io.pick_column(cols, config.FLOOD_SUBTYPE_FIELDS, what="zone subtype", required=False)
        c_tf = spatial_io.pick_column(cols, config.FLOOD_SFHA_TF_FIELDS, what="SFHA flag", required=False)
        c_id = spatial_io.pick_column(cols, config.FLOOD_ID_FIELDS, what="source id", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        # Clip the polygon to the county, keeping only polygonal components (a tangent touch
        # can yield a stray line/point — ST_CollectionExtract(..., 3) drops those).
        clip_geom = (
            "ST_CollectionExtract(ST_Intersection("
            f"{_make_valid('s.g')}, c.geom), 3)"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE flood_sfha AS
            WITH src AS (
                SELECT {_text(c_id)}     AS source_id,
                       {_text(c_zone)}   AS fld_zone,
                       {_text(c_subty)}  AS zone_subtype,
                       {geom_4326}       AS g
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
                  AND {_is_sfha_sql(c_zone, c_tf)}
            ),
            clipped AS (
                SELECT s.source_id, s.fld_zone, s.zone_subtype,
                       {clip_geom} AS geom
                FROM src s, {clip.COUNTY_TABLE} c
                WHERE ST_Intersects(s.g, c.geom)
            )
            SELECT row_number() OVER () AS id,
                   source_id, fld_zone, zone_subtype, TRUE AS sfha_flag, geom
            FROM clipped
            WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
            """
        )

        n = con.execute("SELECT count(*) FROM flood_sfha").fetchone()[0]
        if n == 0:
            # Kern County indisputably contains mapped SFHA (the Kern River corridor, etc.);
            # 0 after the clip means a CRS/clip/schema/where drift, not a legitimately empty
            # layer. Fail loud like every other fetcher rather than swap in an empty
            # exclusion layer that would silently mark every parcel flood-free.
            raise sources.SourceError(
                f"flood_sfha clip of {label!r} yielded 0 SFHA features inside the county"
            )
        zones = con.execute(
            "SELECT count(DISTINCT upper(trim(fld_zone))) FROM flood_sfha"
        ).fetchone()[0]

        parquet = Path(ctx.work_dir) / "flood_sfha.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT id, source_id, fld_zone, zone_subtype, sfha_flag, geom FROM flood_sfha",
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "flood_sfha.built", features=n, distinct_zones=zones,
                  zone_field=c_zone, sfha_tf_field=c_tf, source=label)
        return LayerResult(
            name=self.name,
            table="flood_sfha",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"zone_field": c_zone, "sfha_tf_field": c_tf, "distinct_zones": zones},
        )
