"""GEO-5 — Kern County zoning / land-use ingestion + zoning_rules.csv.

Three fetchers over Kern County's own GEODAT layers (already county-scoped, so no clip):

* `zoning`        — zoning districts. Normalizes the primary district code (`Zn_Cd1`) to a
                    base code (lot-size parentheticals and `*` stripped), emits the polygons
                    for the parcels×zoning spatial join (GEO-13), and writes zoning_rules.csv
                    (FR-A2) validated for coverage against the codes actually present.
* `general_plan`  — general plan land-use designations (`GP_DESIG` / `LU_DESC`).
* `specific_plans`— specific plan areas (`SP_NAME_1`).

All output is storage CRS 4326 + GeoParquet (§4). A pre-staged local file (GeoJSON, 4326)
can be supplied via `GEO_ZONING_SOURCE` / `GEO_GENERAL_PLAN_SOURCE` /
`GEO_SPECIFIC_PLANS_SOURCE` for offline/air-gapped runs and tests.

Zoning-district layer + code field confirmed 2026-06-15: `Kern_County_Zoning` FeatureServer,
primary code field `Zn_Cd1`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import arcgis, config, crs, geoparquet, sources, spatial_io, zoning_rules
from ..logging_setup import log_event
from ..sqlutil import ident
from .base import FetchContext, Fetcher, LayerResult, register

_GEOM = spatial_io.GEOM_COLUMN
_text = spatial_io.text_or_null


def _make_valid(geom_expr: str) -> str:
    return f"CASE WHEN ST_IsValid({geom_expr}) THEN {geom_expr} ELSE ST_MakeValid({geom_expr}) END"


def _zone_code_sql(col: str) -> str:
    """Normalize a raw zoning code to its base district: take the leading token before any
    '(' or space (drops lot-size suffixes like E(20), NR(40), MS 2 1/2), uppercase, strip a
    trailing '*'. Empty/NULL → 'OTHER' (which carries a curated default rule)."""
    raw = f"trim({ident(col)})"
    return f"coalesce(nullif(rtrim(upper(regexp_extract({raw}, '^[^( ]+', 0)), '*'), ''), 'OTHER')"


def _resolve(
    ctx: FetchContext, *, layer: str, source_env: str, source_crs_env: str, url_env: str, url_default: str
) -> tuple[str, int, str]:
    """Return (ST_Read source, source CRS, human label). Local override wins; else fetch the
    GEODAT FeatureServer (already Kern-scoped, so no bbox prefilter), outSR=4326."""
    override = sources.local_override(source_env)
    if override is not None:
        crs_code = int(os.environ.get(source_crs_env, config.CRS_STORAGE))
        return str(override), crs_code, f"local:{override.name}"
    url = os.environ.get(url_env, url_default).strip()
    if not url:
        raise sources.SourceError(f"no source for {layer}: set {source_env} (local file) or {url_env}")
    dest = Path(ctx.work_dir) / f"{layer}_source.geojson"
    arcgis.fetch_featureserver_geojson(url, dest, logger=ctx.logger)
    return str(dest), config.CRS_STORAGE, url


@register
class ZoningFetcher(Fetcher):
    name = "zoning"
    run_order = 30

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        source, source_crs, label = _resolve(
            ctx,
            layer="zoning",
            source_env=config.ZONING_SOURCE_ENV,
            source_crs_env=config.ZONING_SOURCE_CRS_ENV,
            url_env=config.ZONING_URL_ENV,
            url_default=config.ZONING_URL,
        )
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_code = spatial_io.pick_column(cols, config.ZONING_CODE_FIELDS, what="zoning code")
        c_desc = spatial_io.pick_column(cols, config.ZONING_DESC_FIELDS, what="zoning description", required=False)
        c_comb = spatial_io.pick_column(cols, config.ZONING_COMBINED_FIELDS, what="combined zoning", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE zoning AS
            WITH src AS (
                SELECT {_zone_code_sql(c_code)} AS zone_code,
                       {_text(c_code)}          AS zone_code_raw,
                       {_text(c_comb)}          AS zone_combined,
                       {_text(c_desc)}          AS description,
                       {_make_valid(geom_4326)} AS geom
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            )
            SELECT row_number() OVER () AS id, zone_code, zone_code_raw, zone_combined, description, geom
            FROM src
            """
        )

        n = con.execute("SELECT count(*) FROM zoning").fetchone()[0]
        if n == 0:
            raise sources.SourceError(f"zoning source {label!r} yielded 0 usable features")

        distinct_codes = [r[0] for r in con.execute("SELECT DISTINCT zone_code FROM zoning").fetchall()]
        rules, names = zoning_rules.load_rules()
        rows, missing = zoning_rules.effective_rows(distinct_codes, rules, names)
        rules_csv = Path(ctx.work_dir) / config.ZONING_RULES_CSV
        zoning_rules.write_csv(rows, rules_csv)
        if missing:
            log_event(log, "zoning.rules_gap", level=logging.WARNING,
                      count=len(missing), pairs=[f"{c}/{u}" for c, u in missing])

        parquet = Path(ctx.work_dir) / "zoning.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT id, zone_code, zone_code_raw, zone_combined, description, geom FROM zoning",
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "zoning.built", features=n, distinct_codes=len(distinct_codes),
                  rule_gaps=len(missing), code_field=c_code, source=label)
        return LayerResult(
            name=self.name,
            table="zoning",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={
                "code_field": c_code,
                "distinct_codes": len(distinct_codes),
                "rules_csv": str(rules_csv),
                "rule_gaps": len(missing),
            },
        )


@register
class GeneralPlanFetcher(Fetcher):
    name = "general_plan"
    run_order = 31

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        source, source_crs, label = _resolve(
            ctx,
            layer="general_plan",
            source_env=config.GENERAL_PLAN_SOURCE_ENV,
            source_crs_env=config.GENERAL_PLAN_SOURCE_CRS_ENV,
            url_env=config.GENERAL_PLAN_URL_ENV,
            url_default=config.GENERAL_PLAN_URL,
        )
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_desig = spatial_io.pick_column(cols, config.GENERAL_PLAN_DESIG_FIELDS, what="GP designation", required=False)
        c_lu = spatial_io.pick_column(cols, config.GENERAL_PLAN_LU_FIELDS, what="land-use description", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE general_plan AS
            WITH src AS (
                SELECT {_text(c_desig)} AS gp_desig,
                       {_text(c_lu)}    AS lu_desc,
                       {_make_valid(geom_4326)} AS geom
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            )
            SELECT row_number() OVER () AS id, gp_desig, lu_desc, geom FROM src
            """
        )
        n = con.execute("SELECT count(*) FROM general_plan").fetchone()[0]
        if n == 0:
            raise sources.SourceError(f"general_plan source {label!r} yielded 0 usable features")

        parquet = Path(ctx.work_dir) / "general_plan.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT id, gp_desig, lu_desc, geom FROM general_plan",
            out_path=parquet,
            geom_col="geom",
        )
        log_event(log, "general_plan.built", features=n, desig_field=c_desig, source=label)
        return LayerResult(
            name=self.name, table="general_plan", feature_count=n, source=label,
            parquet_path=parquet, extra={"desig_field": c_desig},
        )


@register
class SpecificPlansFetcher(Fetcher):
    name = "specific_plans"
    run_order = 32

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        source, source_crs, label = _resolve(
            ctx,
            layer="specific_plans",
            source_env=config.SPECIFIC_PLANS_SOURCE_ENV,
            source_crs_env=config.SPECIFIC_PLANS_SOURCE_CRS_ENV,
            url_env=config.SPECIFIC_PLANS_URL_ENV,
            url_default=config.SPECIFIC_PLANS_URL,
        )
        read_expr = spatial_io.st_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)
        c_name = spatial_io.pick_column(cols, config.SPECIFIC_PLANS_NAME_FIELDS, what="plan name", required=False)

        geom_4326 = crs.ensure_storage_sql(_GEOM, from_crs=source_crs)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE specific_plans AS
            WITH src AS (
                SELECT {_text(c_name)} AS sp_name,
                       {_make_valid(geom_4326)} AS geom
                FROM {read_expr}
                WHERE {_GEOM} IS NOT NULL AND NOT ST_IsEmpty({_GEOM})
            )
            SELECT row_number() OVER () AS id, sp_name, geom FROM src
            """
        )
        n = con.execute("SELECT count(*) FROM specific_plans").fetchone()[0]
        if n == 0:
            raise sources.SourceError(f"specific_plans source {label!r} yielded 0 usable features")

        parquet = Path(ctx.work_dir) / "specific_plans.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql="SELECT id, sp_name, geom FROM specific_plans",
            out_path=parquet,
            geom_col="geom",
        )
        log_event(log, "specific_plans.built", features=n, name_field=c_name, source=label)
        return LayerResult(
            name=self.name, table="specific_plans", feature_count=n, source=label,
            parquet_path=parquet, extra={"name_field": c_name},
        )
