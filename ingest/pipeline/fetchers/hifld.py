"""GEO-6 — HIFLD transmission lines + electric substations (clipped to Kern County).

Two fetchers from the Homeland Infrastructure Foundation-Level Data (HIFLD) program:

* `transmission_lines` — HIFLD Electric Power Transmission Lines (polylines), truncated to
  the county polygon (a true clip, line geometry preserved).
* `substations` — HIFLD Electric Substations (points), filtered to those inside the county.
  Reused downstream to geolocate CAISO interconnection POIs (GEO-7).

Both are national layers, so the source is prefiltered server-side to the county bounding
box (the ArcGIS envelope filter) and then clipped precisely to the county polygon in DuckDB
(via the shared county_boundary table from GEO-3, run_order=0). Voltage carries HIFLD's
"not available" sentinel (-999999); we also treat non-positive voltage as unknown (the
substations re-host encodes unknown as 0). Output is storage CRS 4326 + GeoParquet (§4).

A pre-staged local file (GeoJSON, 4326) can be supplied via `GEO_TRANSMISSION_SOURCE` /
`GEO_SUBSTATIONS_SOURCE` for offline/air-gapped runs and tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import arcgis, clip, config, crs, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident
from .base import FetchContext, Fetcher, LayerResult, register

_GEOM = spatial_io.GEOM_COLUMN
_text = spatial_io.text_or_null


def _voltage_sql(col: str | None) -> str:
    """kV as DOUBLE with unknowns nulled: HIFLD sentinels (-999999/-999998) and any
    non-positive value (0 kV is "not recorded", not a real voltage)."""
    if not col:
        return "CAST(NULL AS DOUBLE)"
    v = f"TRY_CAST({ident(col)} AS DOUBLE)"
    sentinels = ", ".join(str(s) for s in config.VOLTAGE_NULL_SENTINELS)
    return f"CASE WHEN {v} IS NULL OR {v} IN ({sentinels}) OR {v} <= 0 THEN NULL ELSE {v} END"


def _resolve_source(
    ctx: FetchContext,
    *,
    layer: str,
    source_env: str,
    source_crs_env: str,
    url_env: str,
    url_default: str,
    bbox: tuple[float, float, float, float],
) -> tuple[str, int, str]:
    """Return (ST_Read source, source CRS, human label). Local override wins; else fetch the
    national FeatureServer prefiltered to the county `bbox` (outSR=4326)."""
    override = sources.local_override(source_env)
    if override is not None:
        crs_code = int(os.environ.get(source_crs_env, config.CRS_STORAGE))
        return str(override), crs_code, f"local:{override.name}"

    url = os.environ.get(url_env, url_default).strip()
    if not url:
        raise sources.SourceError(f"no source for {layer}: set {source_env} (local file) or {url_env}")
    dest = Path(ctx.work_dir) / f"{layer}_source.geojson"
    arcgis.fetch_featureserver_geojson(url, dest, bbox=bbox, logger=ctx.logger)
    return str(dest), config.CRS_STORAGE, url


@register
class TransmissionLinesFetcher(Fetcher):
    name = "transmission_lines"
    run_order = 20

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the server-side prefilter
        source, source_crs, label = _resolve_source(
            ctx,
            layer="transmission",
            source_env=config.TRANSMISSION_SOURCE_ENV,
            source_crs_env=config.TRANSMISSION_SOURCE_CRS_ENV,
            url_env=config.TRANSMISSION_URL_ENV,
            url_default=config.TRANSMISSION_URL,
            bbox=bbox,
        )
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_volt = spatial_io.pick_column(cols, config.TRANSMISSION_VOLTAGE_FIELDS, what="voltage", required=False)
        c_class = spatial_io.pick_column(cols, ["VOLT_CLASS", "VOLTAGE_CLASS"], what="voltage class", required=False)
        c_owner = spatial_io.pick_column(cols, ["OWNER", "OPERATOR"], what="owner", required=False)
        c_type = spatial_io.pick_column(cols, ["TYPE", "LINE_TYPE"], what="line type", required=False)
        c_status = spatial_io.pick_column(cols, ["STATUS"], what="status", required=False)
        c_id = spatial_io.pick_column(cols, ["ID", "OBJECTID", "OBJECTID_1"], what="source id", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        # Clip the line to the county polygon, then keep only line components (a tangent
        # touch yields a stray POINT — ST_CollectionExtract(..., 2) drops it).
        clip_geom = (
            "ST_CollectionExtract(ST_Intersection("
            "CASE WHEN ST_IsValid(s.g) THEN s.g ELSE ST_MakeValid(s.g) END, c.geom), 2)"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE transmission_lines AS
            WITH src AS (
                SELECT {_text(c_id)}      AS source_id,
                       {_voltage_sql(c_volt)} AS voltage_kv,
                       {_text(c_class)}   AS volt_class,
                       {_text(c_owner)}   AS owner,
                       {_text(c_type)}    AS line_type,
                       {_text(c_status)}  AS status,
                       {geom_4326}        AS g
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            ),
            clipped AS (
                SELECT s.source_id, s.voltage_kv, s.volt_class, s.owner, s.line_type, s.status,
                       {clip_geom} AS geom
                FROM src s, {clip.COUNTY_TABLE} c
                WHERE ST_Intersects(s.g, c.geom)
            )
            SELECT row_number() OVER () AS id,
                   source_id, voltage_kv, volt_class, owner, line_type, status, geom
            FROM clipped
            WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
            """
        )

        n = con.execute("SELECT count(*) FROM transmission_lines").fetchone()[0]
        if n == 0:
            # Kern County indisputably contains transmission lines; 0 after the clip means a
            # CRS/clip/schema drift, not a legitimate empty layer. Fail loud like every other
            # fetcher so a broken build never swaps in an empty critical scoring layer.
            raise sources.SourceError(
                f"transmission_lines clip of {label!r} yielded 0 features inside the county"
            )
        with_volt = con.execute(
            "SELECT count(*) FROM transmission_lines WHERE voltage_kv IS NOT NULL"
        ).fetchone()[0]

        parquet = Path(ctx.work_dir) / "transmission_lines.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql=(
                "SELECT id, source_id, voltage_kv, volt_class, owner, line_type, status, geom "
                "FROM transmission_lines"
            ),
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "transmission_lines.built", features=n, with_voltage=with_volt,
                  voltage_field=c_volt, source=label)
        return LayerResult(
            name=self.name,
            table="transmission_lines",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"voltage_field": c_volt, "with_voltage": with_volt},
        )


@register
class SubstationsFetcher(Fetcher):
    name = "substations"
    run_order = 21

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the server-side prefilter
        source, source_crs, label = _resolve_source(
            ctx,
            layer="substations",
            source_env=config.SUBSTATIONS_SOURCE_ENV,
            source_crs_env=config.SUBSTATIONS_SOURCE_CRS_ENV,
            url_env=config.SUBSTATIONS_URL_ENV,
            url_default=config.SUBSTATIONS_URL,
            bbox=bbox,
        )
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_volt = spatial_io.pick_column(cols, config.SUBSTATIONS_VOLTAGE_FIELDS, what="max voltage", required=False)
        c_min_volt = spatial_io.pick_column(cols, config.SUBSTATIONS_MIN_VOLTAGE_FIELDS, what="min voltage", required=False)
        c_name = spatial_io.pick_column(cols, ["NAME", "SUB_NAME", "STATION"], what="name", required=False)
        c_type = spatial_io.pick_column(cols, ["TYPE", "SUB_TYPE"], what="type", required=False)
        c_status = spatial_io.pick_column(cols, ["STATUS"], what="status", required=False)
        c_id = spatial_io.pick_column(cols, ["ID", "OBJECTID", "OBJECTID_1"], what="source id", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        # Points: keep those inside the county (a point clip is just in/out).
        con.execute(
            f"""
            CREATE OR REPLACE TABLE substations AS
            WITH src AS (
                SELECT {_text(c_id)}   AS source_id,
                       {_text(c_name)} AS name,
                       {_voltage_sql(c_volt)}     AS max_voltage_kv,
                       {_voltage_sql(c_min_volt)} AS min_voltage_kv,
                       {_text(c_type)}   AS sub_type,
                       {_text(c_status)} AS status,
                       {geom_4326}       AS g
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            )
            SELECT row_number() OVER () AS id,
                   s.source_id, s.name, s.max_voltage_kv, s.min_voltage_kv, s.sub_type, s.status,
                   s.g AS geom
            FROM src s, {clip.COUNTY_TABLE} c
            WHERE ST_Intersects(s.g, c.geom)
            """
        )

        n = con.execute("SELECT count(*) FROM substations").fetchone()[0]
        if n == 0:
            # See TransmissionLinesFetcher: 0 inside Kern means a bug, not an empty layer.
            raise sources.SourceError(
                f"substations clip of {label!r} yielded 0 features inside the county"
            )
        with_volt = con.execute(
            "SELECT count(*) FROM substations WHERE max_voltage_kv IS NOT NULL"
        ).fetchone()[0]

        parquet = Path(ctx.work_dir) / "substations.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql=(
                "SELECT id, source_id, name, max_voltage_kv, min_voltage_kv, sub_type, status, geom "
                "FROM substations"
            ),
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "substations.built", features=n, with_voltage=with_volt,
                  voltage_field=c_volt, source=label)
        return LayerResult(
            name=self.name,
            table="substations",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"voltage_field": c_volt, "with_voltage": with_volt},
        )
