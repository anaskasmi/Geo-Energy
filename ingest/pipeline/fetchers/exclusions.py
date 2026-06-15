"""GEO-11 — Stage-A optional exclusion overlays (protected / open-water / built-up).

`exclusions`: polygons clipped to Kern County, unioned across the configured exclusion kinds
with a `kind` column. Drives the §5 *optional* Stage-A exclusions (parcel × exclusion flags
computed in enrichment, GEO-13; scoring wires them behind a flag).

OPTIONAL and off the critical path (review C8): each kind ingests only when its
GEO_EXCLUSION_<KIND>_SOURCE (or _URL) is configured; with none configured the fetcher creates
an EMPTY table and logs a WARNING rather than failing the build. Real national sources
(PAD-US / NHD / NLCD) are large and their endpoints are deferred (default URLs empty), so a
clipped GeoJSON must be staged to enable a kind. Output is storage CRS 4326 + GeoParquet (§4).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import arcgis, clip, config, crs, sources, spatial_io, geoparquet
from ..logging_setup import log_event
from ..sqlutil import sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_GEOM = spatial_io.GEOM_COLUMN
_text = spatial_io.text_or_null


def _make_valid(geom_expr: str) -> str:
    return f"CASE WHEN ST_IsValid({geom_expr}) THEN {geom_expr} ELSE ST_MakeValid({geom_expr}) END"


def _resolve_kind(
    ctx: FetchContext, *, kind: str, source_env: str, crs_env: str, url_env: str,
    default_url: str, bbox: tuple[float, float, float, float],
) -> tuple[str, int, str] | None:
    """Return (ST_Read source, source CRS, label) for one exclusion kind, or None if not
    configured (the kind is then skipped — optional, off the critical path)."""
    override = sources.local_override(source_env)
    if override is not None:
        crs_code = int(os.environ.get(crs_env, config.CRS_STORAGE))
        return str(override), crs_code, f"local:{override.name}"
    url = os.environ.get(url_env, default_url).strip()
    if not url:
        return None
    dest = Path(ctx.work_dir) / f"exclusion_{kind}_source.geojson"
    if "/FeatureServer/" in url or "/MapServer/" in url:
        arcgis.fetch_featureserver_geojson(url, dest, bbox=bbox, logger=ctx.logger)
    else:
        sources.http_download(url, dest, logger=ctx.logger)
    return str(dest), config.CRS_STORAGE, url


@register
class ExclusionsFetcher(Fetcher):
    name = "exclusions"
    run_order = 61  # after county_boundary; optional, off the critical path

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the server-side prefilter

        con.execute(
            "CREATE OR REPLACE TEMP TABLE _excl_stage "
            "(kind VARCHAR, source_id VARCHAR, name VARCHAR, geom GEOMETRY)"
        )
        configured: list[str] = []
        for kind, source_env, crs_env, url_env, default_url in config.EXCLUSION_LAYERS:
            resolved = _resolve_kind(
                ctx, kind=kind, source_env=source_env, crs_env=crs_env, url_env=url_env,
                default_url=default_url, bbox=bbox,
            )
            if resolved is None:
                continue
            source, source_crs, label = resolved
            read = spatial_io.st_read_expr(source)
            cols = spatial_io.source_columns(con, read)
            c_id = spatial_io.pick_column(cols, config.EXCLUSION_ID_FIELDS, what="exclusion id", required=False)
            c_name = spatial_io.pick_column(cols, config.EXCLUSION_NAME_FIELDS, what="exclusion name", required=False)
            geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
            clip_geom = f"ST_CollectionExtract(ST_Intersection({_make_valid('s.g')}, c.geom), 3)"
            con.execute(
                f"""
                INSERT INTO _excl_stage (kind, source_id, name, geom)
                WITH src AS (
                    SELECT {sql_str(kind)} AS kind, {_text(c_id)} AS source_id,
                           {_text(c_name)} AS name, {geom_4326} AS g
                    FROM {read}
                    WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
                ),
                clipped AS (
                    SELECT s.kind, s.source_id, s.name, {clip_geom} AS geom
                    FROM src s, {clip.COUNTY_TABLE} c
                    WHERE ST_Intersects(s.g, c.geom)
                )
                SELECT kind, source_id, name, geom
                FROM clipped WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
                """
            )
            configured.append(kind)
            log_event(log, "exclusions.kind_ingested", kind=kind, source=label)

        con.execute(
            f"""
            CREATE OR REPLACE TABLE {config.EXCLUSIONS_TABLE} AS
            SELECT row_number() OVER (ORDER BY kind, source_id) AS id, kind, source_id, name, geom
            FROM _excl_stage
            """
        )
        con.execute("DROP TABLE _excl_stage")

        n = con.execute(f"SELECT count(*) FROM {config.EXCLUSIONS_TABLE}").fetchone()[0]
        if not configured:
            log_event(
                log, "exclusions.skipped", level=logging.WARNING,
                reason="no exclusion kinds configured (optional, off the critical path)",
            )
        elif n == 0:
            # Configured but everything clipped out — surface it (matches eia_generators.empty);
            # still non-fatal since exclusions are optional / off the critical path.
            log_event(log, "exclusions.empty", level=logging.WARNING, kinds=configured)

        parquet = Path(ctx.work_dir) / config.EXCLUSIONS_PARQUET
        geoparquet.write_intermediate(
            con,
            select_sql=f"SELECT id, kind, source_id, name, geom FROM {config.EXCLUSIONS_TABLE}",
            out_path=parquet,
            geom_col="geom",
        )
        label = "+".join(configured) if configured else "(none configured)"
        log_event(log, "exclusions.built", features=n, kinds=configured, source=label)
        return LayerResult(
            name=self.name, table=config.EXCLUSIONS_TABLE, feature_count=n,
            source=label, parquet_path=parquet, extra={"kinds": configured},
        )
