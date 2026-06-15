"""GEO-7 — CAISO interconnection queue + POI geolocation + poi_competition.

Three cohesive fetchers built from the published CAISO interconnection queue
(`gridstatus.CAISO().get_interconnection_queue()`), scoped to Kern County:

* `caiso_queue`        — one row per queued project (name, status, generation type, MW,
                         county, PTO, POI name). Each project's POI is **geolocated by
                         name-matching to the HIFLD substations (GEO-6)**, which also
                         supplies the POI voltage (the queue carries no voltage column).
* `poi_competition`    — per geolocated POI: the count + MW of active queued projects at
                         that POI and within a radius (the §5 POI competition factor).
* `caiso_queue_summary`— Kern county-level queue totals (by type / by status), retained as
                         **context only** for /api/context (§6 grid_context), never scored.

The queue is materialized to CSV first so the live path (gridstatus → DataFrame → CSV) and
the offline path (a pre-staged CSV via `GEO_CAISO_QUEUE_SOURCE`) share one DuckDB read path;
`gridstatus` is imported lazily only on the live path, so the test suite needs neither the
library nor the network. Geolocation depends on the `substations` table (GEO-6, run_order=21)
existing in the same build connection, so these run after it (run_order 40–42).
"""

from __future__ import annotations

from pathlib import Path

from .. import config, crs, geoparquet, sources, spatial_io
from ..logging_setup import log_event
from ..sqlutil import ident, sql_str
from .base import FetchContext, Fetcher, LayerResult, register

# Generic tokens stripped when normalizing a station/POI name for matching (so e.g.
# "Whirlwind 230 kV Substation" and HIFLD "WHIRLWIND" collapse to the same key).
_NOISE_RE = r"\b(SUBSTATION|SUBSTATIONS|SUB|STATION|SWITCHYARD|SWITCHING|SWYD|TAP|JCT|JUNCTION|KV)\b"


def _csv_read_expr(path: str | Path) -> str:
    """A `read_csv_auto(...)` table expression for the queue CSV (all columns as VARCHAR so
    the messy 36-column schema never trips type sniffing; numerics are TRY_CAST downstream)."""
    return f"read_csv_auto({sql_str(str(path))}, header=true, all_varchar=true, sample_size=-1)"


def _num_sql(col: str | None) -> str:
    """A numeric attribute as DOUBLE, tolerating stray characters (commas, units)."""
    if not col:
        return "CAST(NULL AS DOUBLE)"
    return (
        f"TRY_CAST(nullif(regexp_replace(CAST({ident(col)} AS VARCHAR), '[^0-9.-]', '', 'g'), '') "
        "AS DOUBLE)"
    )


def _voltage_sql(col: str | None) -> str:
    """Optional source voltage as DOUBLE with HIFLD sentinels / non-positive nulled (the
    queue normally has no voltage column; this only fires if a source provides one)."""
    if not col:
        return "CAST(NULL AS DOUBLE)"
    v = _num_sql(col)
    sentinels = ", ".join(str(s) for s in config.VOLTAGE_NULL_SENTINELS)
    return f"CASE WHEN {v} IS NULL OR {v} IN ({sentinels}) OR {v} <= 0 THEN NULL ELSE {v} END"


def _active_sql(status_col: str | None) -> str:
    """TRUE when a status is not a terminal/withdrawn state (counts toward competition)."""
    if not status_col:
        return "FALSE"
    s = f"lower(coalesce(CAST({ident(status_col)} AS VARCHAR), ''))"
    conds = " OR ".join(f"{s} LIKE '%{p}%'" for p in config.CAISO_INACTIVE_STATUS_PATTERNS)
    return f"(CAST({ident(status_col)} AS VARCHAR) IS NOT NULL AND NOT ({conds}))"


def _norm_name_sql(expr: str) -> str:
    """Normalize a station/POI name to a match key: uppercase, drop punctuation, digits
    (voltages/sizes) and generic station tokens, collapse whitespace."""
    s = f"upper({expr})"
    s = f"regexp_replace({s}, '[^A-Z0-9]+', ' ', 'g')"
    s = f"regexp_replace({s}, '[0-9]+', ' ', 'g')"
    s = f"regexp_replace({s}, {sql_str(_NOISE_RE)}, ' ', 'g')"
    return f"trim(regexp_replace({s}, '\\s+', ' ', 'g'))"


def _resolve_queue_source(ctx: FetchContext) -> tuple[str, str]:
    """Return (CSV path, human label). Local override wins; else fetch the live CAISO queue
    via gridstatus (imported lazily) and materialize it to CSV in the work dir."""
    override = sources.local_override(config.CAISO_QUEUE_SOURCE_ENV)
    if override is not None:
        return str(override), f"local:{override.name}"

    dest = Path(ctx.work_dir) / "caiso_queue_source.csv"
    try:
        import gridstatus  # noqa: PLC0415 — lazy: only the live path needs it / the network
    except ImportError as err:
        raise sources.SourceError(
            f"gridstatus not installed; set {config.CAISO_QUEUE_SOURCE_ENV} (pre-staged CSV) "
            "or install gridstatus"
        ) from err
    df = gridstatus.CAISO().get_interconnection_queue()
    df.to_csv(dest, index=False)
    log_event(ctx.logger, "caiso_queue.fetched", rows=int(len(df)), dest=str(dest))
    return str(dest), "gridstatus.CAISO().get_interconnection_queue()"


def _require_substations(con) -> None:
    """The geolocation depends on GEO-6 having built `substations` first."""
    try:
        con.execute("SELECT 1 FROM substations LIMIT 1").fetchone()
    except Exception as err:  # noqa: BLE001 — table missing → not yet built
        raise sources.SourceError(
            "substations table missing; the substations fetcher (GEO-6) must run before "
            "caiso_queue (POI geolocation needs it)"
        ) from err


@register
class CaisoQueueFetcher(Fetcher):
    name = "caiso_queue"
    run_order = 40  # after substations (21): geolocation name-matches against them

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        _require_substations(con)
        source, label = _resolve_queue_source(ctx)
        read_expr = _csv_read_expr(source)
        cols = spatial_io.source_columns(con, read_expr)

        c_qid = spatial_io.pick_column(cols, config.CAISO_QUEUE_ID_FIELDS, what="queue id", required=False)
        c_name = spatial_io.pick_column(cols, config.CAISO_NAME_FIELDS, what="project name", required=False)
        c_type = spatial_io.pick_column(cols, config.CAISO_TYPE_FIELDS, what="generation type", required=False)
        c_fuel = spatial_io.pick_column(cols, config.CAISO_FUEL_FIELDS, what="fuel", required=False)
        c_status = spatial_io.pick_column(cols, config.CAISO_STATUS_FIELDS, what="status", required=False)
        c_mw = spatial_io.pick_column(cols, config.CAISO_MW_FIELDS, what="capacity MW", required=False)
        c_county = spatial_io.pick_column(cols, config.CAISO_COUNTY_FIELDS, what="county")
        c_state = spatial_io.pick_column(cols, config.CAISO_STATE_FIELDS, what="state", required=False)
        c_pto = spatial_io.pick_column(cols, config.CAISO_PTO_FIELDS, what="transmission owner", required=False)
        c_poi = spatial_io.pick_column(cols, config.CAISO_POI_FIELDS, what="POI name")
        c_volt = spatial_io.pick_column(cols, config.CAISO_POI_VOLTAGE_FIELDS, what="POI voltage", required=False)
        c_qdate = spatial_io.pick_column(cols, config.CAISO_QUEUE_DATE_FIELDS, what="queue date", required=False)
        c_done = spatial_io.pick_column(cols, config.CAISO_COMPLETION_FIELDS, what="completion date", required=False)

        _text = spatial_io.text_or_null
        # Match Kern as a whole word so "Kern", "Kern County", and multi-county strings like
        # "Los Angeles, Kern" or "Kern/Inyo" all scope in (exact equality silently dropped them).
        kern_pat = sql_str(r"\b" + config.KERN_COUNTY_NAME.upper() + r"\b")
        minlen = int(config.POI_MATCH_MIN_TOKEN_LEN)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE caiso_queue AS
            WITH src AS (
                SELECT {_text(c_qid)}    AS queue_id,
                       {_text(c_name)}   AS project_name,
                       {_text(c_type)}   AS generation_type,
                       {_text(c_fuel)}   AS fuel,
                       {_text(c_status)} AS status,
                       {_num_sql(c_mw)}  AS capacity_mw,
                       {_text(c_county)} AS county,
                       {_text(c_state)}  AS state,
                       {_text(c_pto)}    AS transmission_owner,
                       {_text(c_poi)}    AS poi_name,
                       {_voltage_sql(c_volt)} AS source_voltage_kv,
                       {_text(c_qdate)}  AS queue_date,
                       {_text(c_done)}   AS proposed_completion_date
                FROM {read_expr}
            ),
            kern AS (
                SELECT row_number() OVER () AS rid, src.*,
                       {_norm_name_sql("poi_name")} AS poi_name_norm,
                       {_active_sql("status")}      AS is_active
                FROM src
                WHERE regexp_matches(upper(trim(county)), {kern_pat})
            ),
            subs AS (
                SELECT id AS substation_id, max_voltage_kv, geom,
                       {_norm_name_sql("name")} AS sub_name_norm
                FROM substations
            ),
            cand AS (
                SELECT k.rid AS rid, s.substation_id, s.max_voltage_kv, s.geom AS geom,
                       length(s.sub_name_norm) AS sub_len,
                       CASE
                         WHEN k.poi_name_norm = s.sub_name_norm THEN 3
                         -- containment tiers: require the *contained* name to be specific
                         -- enough (>= minlen chars) so a short generic station token can't
                         -- whole-word-match many unrelated POIs. Exact (rank 3) is exempt.
                         WHEN length(s.sub_name_norm) >= {minlen} AND
                              (' ' || k.poi_name_norm || ' ') LIKE ('% ' || s.sub_name_norm || ' %') THEN 2
                         WHEN length(k.poi_name_norm) >= {minlen} AND
                              (' ' || s.sub_name_norm || ' ') LIKE ('% ' || k.poi_name_norm || ' %') THEN 1
                         ELSE 0
                       END AS rank
                FROM kern k, subs s
                WHERE k.poi_name_norm <> '' AND s.sub_name_norm <> ''
            ),
            ranked AS (
                SELECT *, max(rank) OVER (PARTITION BY rid) AS best_rank
                FROM cand WHERE rank > 0
            ),
            best AS (
                -- among the candidates tied at a project's best rank, flag ambiguity and pick
                -- one deterministically (most specific name, then highest voltage, then id).
                SELECT rid, substation_id, max_voltage_kv, geom,
                       count(*) OVER (PARTITION BY rid) > 1 AS poi_match_ambiguous
                FROM ranked
                WHERE rank = best_rank
                QUALIFY row_number() OVER (
                    PARTITION BY rid
                    ORDER BY sub_len DESC, max_voltage_kv DESC NULLS LAST, substation_id
                ) = 1
            )
            SELECT k.rid AS id,
                   k.queue_id, k.project_name, k.generation_type, k.fuel, k.status, k.is_active,
                   k.capacity_mw, k.county, k.state, k.transmission_owner,
                   k.poi_name, k.poi_name_norm,
                   b.substation_id AS matched_substation_id,
                   coalesce(b.poi_match_ambiguous, FALSE) AS poi_match_ambiguous,
                   coalesce(k.source_voltage_kv, b.max_voltage_kv) AS poi_voltage_kv,
                   k.queue_date, k.proposed_completion_date,
                   b.geom AS geom
            FROM kern k
            LEFT JOIN best b ON b.rid = k.rid
            """
        )

        n = con.execute("SELECT count(*) FROM caiso_queue").fetchone()[0]
        if n == 0:
            # Kern is one of CAISO's largest renewable queues; 0 after the county filter means
            # a county-name/schema mismatch or empty source, not a real empty queue.
            raise sources.SourceError(
                f"caiso_queue from {label!r} yielded 0 projects in {config.KERN_COUNTY_NAME} County"
            )
        matched, active = con.execute(
            "SELECT count(*) FILTER (WHERE matched_substation_id IS NOT NULL), "
            "count(*) FILTER (WHERE is_active) FROM caiso_queue"
        ).fetchone()
        if matched == 0:
            # Geolocation is best-effort (fuzzy name match); 0 matches is degraded but not
            # fatal (the queue context still loads). Surface it loudly for the operator.
            log_event(log, "caiso_queue.no_poi_matches", level=30, projects=n, source=label)

        parquet = Path(ctx.work_dir) / "caiso_queue.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql=(
                "SELECT id, queue_id, project_name, generation_type, fuel, status, is_active, "
                "capacity_mw, county, state, transmission_owner, poi_name, poi_name_norm, "
                "matched_substation_id, poi_match_ambiguous, poi_voltage_kv, queue_date, "
                "proposed_completion_date, geom FROM caiso_queue"
            ),
            out_path=parquet,
            geom_col="geom",
        )

        log_event(log, "caiso_queue.built", projects=n, geolocated=matched, active=active,
                  poi_field=c_poi, source=label)
        return LayerResult(
            name=self.name,
            table="caiso_queue",
            feature_count=n,
            source=label,
            parquet_path=parquet,
            extra={"poi_field": c_poi, "geolocated": matched, "active": active},
        )


@register
class PoiCompetitionFetcher(Fetcher):
    name = "poi_competition"
    run_order = 41  # after caiso_queue (40)

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        radius = config.POI_COMPETITION_RADIUS_M
        p_m = crs.to_metric_sql("p.geom", to_crs=config.CRS_METRIC_UTM)
        a_m = crs.to_metric_sql("a.geom", to_crs=config.CRS_METRIC_UTM)
        con.execute(
            f"""
            CREATE OR REPLACE TABLE poi_competition AS
            WITH active AS (
                SELECT id, capacity_mw, matched_substation_id, geom
                FROM caiso_queue
                WHERE is_active AND matched_substation_id IS NOT NULL AND geom IS NOT NULL
            ),
            poi AS (
                SELECT s.id AS substation_id, s.name AS poi_name,
                       s.max_voltage_kv AS poi_voltage_kv, s.geom
                FROM substations s
                WHERE s.id IN (SELECT DISTINCT matched_substation_id FROM active)
            ),
            at_poi AS (
                SELECT matched_substation_id AS substation_id,
                       count(*) AS n_at_poi,
                       coalesce(sum(capacity_mw), 0) AS mw_at_poi
                FROM active GROUP BY matched_substation_id
            ),
            radius AS (
                SELECT p.substation_id,
                       count(a.id) AS n_within_radius,
                       coalesce(sum(a.capacity_mw), 0) AS mw_within_radius
                FROM poi p
                LEFT JOIN active a ON ST_Distance({p_m}, {a_m}) <= {radius}
                GROUP BY p.substation_id
            )
            SELECT row_number() OVER () AS id,
                   p.substation_id, p.poi_name, p.poi_voltage_kv,
                   coalesce(ap.n_at_poi, 0)        AS n_at_poi,
                   coalesce(ap.mw_at_poi, 0)       AS mw_at_poi,
                   coalesce(r.n_within_radius, 0)  AS n_within_radius,
                   coalesce(r.mw_within_radius, 0) AS mw_within_radius,
                   CAST({radius} AS DOUBLE)        AS radius_m,
                   p.geom
            FROM poi p
            LEFT JOIN at_poi ap ON ap.substation_id = p.substation_id
            LEFT JOIN radius r ON r.substation_id = p.substation_id
            """
        )
        n = con.execute("SELECT count(*) FROM poi_competition").fetchone()[0]
        if n == 0:
            log_event(log, "poi_competition.empty", level=30,
                      note="no active geolocated queue projects")

        parquet = Path(ctx.work_dir) / "poi_competition.parquet"
        geoparquet.write_intermediate(
            con,
            select_sql=(
                "SELECT id, substation_id, poi_name, poi_voltage_kv, n_at_poi, mw_at_poi, "
                "n_within_radius, mw_within_radius, radius_m, geom FROM poi_competition"
            ),
            out_path=parquet,
            geom_col="geom",
        )
        log_event(log, "poi_competition.built", pois=n, radius_m=radius)
        return LayerResult(
            name=self.name, table="poi_competition", feature_count=n,
            source="derived: caiso_queue + substations", parquet_path=parquet,
            extra={"radius_m": radius},
        )


@register
class CaisoQueueSummaryFetcher(Fetcher):
    name = "caiso_queue_summary"
    run_order = 42  # after caiso_queue (40)

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        # Long-form Kern county-level context (total + by generation type + by status).
        # Context only (§6 grid_context) — never scored.
        agg = (
            "count(*) AS n_projects, coalesce(sum(capacity_mw), 0) AS total_mw, "
            "count(*) FILTER (WHERE is_active) AS active_n_projects, "
            "coalesce(sum(capacity_mw) FILTER (WHERE is_active), 0) AS active_total_mw"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE caiso_queue_summary AS
            SELECT * FROM (
                SELECT 'total' AS category, 'all' AS key, {agg} FROM caiso_queue
                UNION ALL
                SELECT 'by_type', coalesce(nullif(generation_type, ''), 'Unknown'), {agg}
                FROM caiso_queue GROUP BY 2
                UNION ALL
                SELECT 'by_status', coalesce(nullif(status, ''), 'Unknown'), {agg}
                FROM caiso_queue GROUP BY 2
            )
            ORDER BY category, key
            """
        )
        n = con.execute("SELECT count(*) FROM caiso_queue_summary").fetchone()[0]
        total_mw = con.execute(
            "SELECT total_mw FROM caiso_queue_summary WHERE category = 'total'"
        ).fetchone()[0]

        parquet = Path(ctx.work_dir) / "caiso_queue_summary.parquet"
        con.execute(
            f"COPY (SELECT * FROM caiso_queue_summary) TO {sql_str(parquet)} (FORMAT PARQUET)"
        )
        log_event(log, "caiso_queue_summary.built", rows=n, total_mw=total_mw)
        return LayerResult(
            name=self.name, table="caiso_queue_summary", feature_count=n,
            source="derived: caiso_queue", parquet_path=parquet,
            extra={"total_mw": total_mw},
        )
