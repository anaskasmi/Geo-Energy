"""GEO-4 — Kern County parcels fetcher (the base layer).

Produces the `parcels` table in storage CRS 4326 with a normalized APN join key and
acreage computed from geometry in a metric CRS (UTM 11N / 26911). Also emits a GeoParquet
intermediate (4326 + bbox struct) and a GeoJSON for tippecanoe (GEO-14). Spec §2.

Source: GEODAT "Assessor Parcels Land 2025" (ArcGIS FeatureServer, confirmed public /
token-free 2026-06-15), with an optional Shafter mirror fallback — both paginated to
GeoJSON (see arcgis.py). The endpoints stay env-configurable and the APN field is resolved
from a candidate list (the service uses "APN"). A pre-staged local file (GeoJSON, 4326) can
be supplied via `GEO_PARCELS_SOURCE` for offline/air-gapped runs and tests.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import arcgis, config, crs, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

_GEOM = spatial_io.GEOM_COLUMN


@register
class ParcelsFetcher(Fetcher):
    name = "parcels"
    run_order = 10

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        source, source_crs, label = self._resolve_source(ctx)
        read_expr = spatial_io.st_read_expr(source)

        cols = spatial_io.source_columns(con, read_expr)
        forced = os.environ.get(config.PARCELS_APN_FIELD_ENV, "").strip()
        candidates = [forced] if forced else list(config.PARCELS_APN_FIELDS)
        c_apn = spatial_io.pick_column(cols, candidates, what="APN field")

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        area_metric = crs.to_metric_sql("geom", to_crs=config.CRS_METRIC_UTM)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE parcels AS
            WITH src AS (
                SELECT {ident(c_apn)} AS apn_raw, {geom_4326} AS geom
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            ),
            valid AS (
                SELECT apn_raw,
                       CASE WHEN ST_IsValid(geom) THEN geom ELSE ST_MakeValid(geom) END AS geom
                FROM src
            ),
            measured AS (
                SELECT row_number() OVER () AS id,
                       apn_raw AS apn,
                       regexp_replace(upper(trim(apn_raw)), '[^A-Z0-9]', '', 'g') AS apn_norm,
                       ST_Area({area_metric}) AS area_sqm,
                       geom
                FROM valid
            )
            SELECT id, apn, apn_norm,
                   area_sqm,
                   area_sqm / {config.SQ_METERS_PER_ACRE} AS acres,
                   geom
            FROM measured
            """
        )

        n = con.execute("SELECT count(*) FROM parcels").fetchone()[0]
        if n == 0:
            raise sources.SourceError(f"parcels source {label!r} yielded 0 usable features")
        null_apn = con.execute("SELECT count(*) FROM parcels WHERE apn_norm IS NULL OR apn_norm = ''").fetchone()[0]
        if null_apn:
            log_event(log, "parcels.null_apn", count=null_apn, level=logging.WARNING)

        parquet = Path(ctx.work_dir) / "parcels.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT id, apn, apn_norm, area_sqm, acres, geom FROM parcels",
            out_path=parquet,
            geom_col="geom",
        )

        geojson = Path(ctx.work_dir) / "parcels.geojson"
        con.execute(
            f"""
            COPY (SELECT id, apn, apn_norm, acres, geom FROM parcels)
            TO {sql_str(geojson)} WITH (FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326')
            """
        )

        log_event(log, "parcels.built", features=n, apn_field=c_apn, source=label)
        return LayerResult(
            name=self.name,
            table="parcels",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"apn_field": c_apn, "geojson": str(geojson), "null_apn": null_apn},
        )

    def _resolve_source(self, ctx: FetchContext) -> tuple[str, int, str]:
        """Return (ST_Read source, source CRS, human label). Local override wins; else
        ArcGIS GEODAT → Shafter fallback, downloaded as GeoJSON (outSR=4326)."""
        override = sources.local_override(config.PARCELS_SOURCE_ENV)
        if override is not None:
            crs_code = int(os.environ.get(config.PARCELS_SOURCE_CRS_ENV, config.CRS_STORAGE))
            return str(override), crs_code, f"local:{override.name}"

        geodat = os.environ.get(config.PARCELS_GEODAT_URL_ENV, config.PARCELS_GEODAT_URL).strip()
        shafter = os.environ.get(config.PARCELS_SHAFTER_URL_ENV, config.PARCELS_SHAFTER_URL).strip()
        urls = [u for u in (geodat, shafter) if u]
        if not urls:
            raise sources.SourceError(
                "no parcels source configured: set GEO_PARCELS_SOURCE (local file) or "
                "GEO_PARCELS_GEODAT_URL / GEO_PARCELS_SHAFTER_URL"
            )
        dest = Path(ctx.work_dir) / "parcels_source.geojson"
        used, _count = arcgis.fetch_with_fallback(urls, dest, logger=ctx.logger)
        return str(dest), config.CRS_STORAGE, used  # ArcGIS f=geojson&outSR=4326
