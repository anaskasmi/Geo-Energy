"""GEO-12 / GEO-13 — DuckDB artifact assembly + parcel enrichment.

These two stages run once, after every fetcher has loaded its table into the build
connection — the convergence point where all core layers (parcels, zoning, tx/sub, CAISO,
flood, slope, GHI) are present. They are invoked by the harness between the fetcher loop and
the manifest write, on the same connection and release/staging dir.

* ``assemble`` (GEO-12, "assembly half"): validates the core tables exist, **Hilbert-orders**
  the parcels table on write (so spatially-near parcels are physically adjacent — better
  row-group pruning / range-scan locality), builds an **R-tree index** on ``parcels.geom``,
  and verifies the GeoParquet intermediates carry their ``bbox`` struct. Produces the
  ``site.duckdb`` shell ready for enrichment; touches no optional layer.

* ``enrich`` (GEO-13, "enrichment half", FR-A4): computes the derived per-parcel columns —
  centroids (4326/26911), zonal mean slope (sampled from the slope raster), GHI sample,
  nearest transmission-line / substation distances + nearest substation kV, POI competition,
  ``sfha_flag``, ``zoning_class`` and optional exclusion / EIA cross-check columns. Columns are
  added **in place** (``ALTER TABLE ADD COLUMN`` + ``UPDATE ... FROM``) so the R-tree index and
  Hilbert order created by ``assemble`` survive (a ``CREATE OR REPLACE`` would drop them).

This is the CRS/units correctness hotspot: every metric quantity (distance, centroid for
distance, zonal raster sampling) is computed in EPSG:26911 via ``crs`` helpers
(``always_xy := true``); only the stored geometry stays EPSG:4326. rasterio/numpy are imported
lazily inside the zonal-slope helper so importing this module never requires the raster stack.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import config, crs, sources
from .logging_setup import log_event
from .sqlutil import ident, sql_str

_GEOM = "geom"

# Derived columns added to the parcels table by enrich() (GEO-13, FR-A4). `acres` already
# exists (parcels fetcher computes it from geometry); it is not re-added.
ENRICH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("centroid_26911", "GEOMETRY"),       # metric centroid (distances computed against this)
    ("centroid_4326", "GEOMETRY"),        # same point in storage CRS (display/serve)
    ("mean_slope_pct", "DOUBLE"),         # zonal mean of the 30 m screening slope raster
    ("mean_slope_pct_final", "DOUBLE"),   # zonal mean of the 10 m final raster (when emitted)
    ("ghi", "DOUBLE"),                    # nearest GHI grid point (kWh/m²/day)
    ("dist_tx_m", "DOUBLE"),              # distance to nearest transmission line (m, 26911)
    ("dist_sub_m", "DOUBLE"),             # distance to nearest substation (m, 26911)
    ("nearest_sub_kv", "DOUBLE"),         # max voltage of that nearest substation
    ("poi_competition_mw", "DOUBLE"),     # queued MW competing at the nearest substation's POI
    ("poi_competition_n", "BIGINT"),      # queued project count at that POI
    ("sfha_flag", "BOOLEAN"),             # intersects a FEMA SFHA polygon (Stage-A exclusion)
    ("zoning_class", "VARCHAR"),          # zone code of the polygon containing the centroid
    ("excl_protected_area", "BOOLEAN"),   # optional overlay flags (NULL/FALSE when unconfigured)
    ("excl_open_water", "BOOLEAN"),
    ("excl_built_up", "BOOLEAN"),
    ("eia_nearest_m", "DOUBLE"),          # distance to nearest EIA-860 generator (cross-check)
)


# ── shared helpers ─────────────────────────────────────────────────────────────
def _count(con: Any, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {ident(table)}").fetchone()[0]


def _base_tables(con: Any) -> set[str]:
    """Names of the regular (non-temp) tables in the build connection."""
    return {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }


def _columns(con: Any, table: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {ident(table)}").fetchall()}


# ── GEO-12: assembly ─────────────────────────────────────────────────────────────
def assemble(con: Any, work_dir: str | Path, settings: Any, logger: Any) -> dict:
    """Finalize the artifact shell: validate core tables, Hilbert-order + R-tree index the
    parcels table, verify intermediate bbox structs. Returns a summary for the manifest.

    A build with no parcels table (an empty/smoke build with no data fetchers) has nothing to
    assemble, so the stage is a no-op; once parcels exists the full convergence contract is
    enforced (every core fetcher must have run)."""
    if config.PARCELS_TABLE not in _base_tables(con):
        log_event(logger, "assembly.skipped", reason="no parcels table (empty build)")
        return {"ran": False}
    _require_core_tables(con, logger)
    hilbert = _hilbert_order_parcels(con, logger)
    _create_rtree_index(con, logger)
    bbox_ok = _verify_bbox_struct(con, work_dir, logger)
    summary = {
        "ran": True,
        "parcels": _count(con, config.PARCELS_TABLE),
        "hilbert_ordered": hilbert,
        "rtree_index": config.PARCELS_GEOM_INDEX,
        "bbox_struct_verified": bbox_ok,
    }
    log_event(logger, "assembly.done", **summary)
    return summary


def _require_core_tables(con: Any, logger: Any) -> None:
    present = _base_tables(con)
    missing = [t for t in config.BUILDER_REQUIRED_TABLES if t not in present]
    if missing:
        raise sources.SourceError(
            f"artifact assembly: required core tables missing {missing}; every core fetcher "
            f"must run before the builder (the convergence contract)"
        )
    if _count(con, config.PARCELS_TABLE) == 0:
        raise sources.SourceError(
            "artifact assembly: parcels table is empty; cannot build a usable artifact"
        )
    # A core table being empty (e.g. a county with no SFHA polygons) is legal but worth a
    # warning — the enrichment columns it feeds will be NULL/false for every parcel.
    for t in config.BUILDER_REQUIRED_TABLES:
        if t != config.PARCELS_TABLE and _count(con, t) == 0:
            log_event(logger, "assembly.empty_core_table", table=t, level=logging.WARNING)


def _hilbert_order_parcels(con: Any, logger: Any) -> bool:
    """Rewrite parcels physically ordered by the Hilbert index of each geometry over the
    parcels' own extent. ST_Hilbert needs a BOX_2D bounds (not a geometry), so the extent is
    read out and inlined as a BOX_2D literal. Returns False (and leaves natural order) when the
    extent is degenerate."""
    row = con.execute(
        f"SELECT min(ST_XMin({_GEOM})), min(ST_YMin({_GEOM})), "
        f"max(ST_XMax({_GEOM})), max(ST_YMax({_GEOM})) FROM {config.PARCELS_TABLE}"
    ).fetchone()
    if row is None or any(v is None for v in row) or row[2] <= row[0] or row[3] <= row[1]:
        log_event(logger, "assembly.hilbert_skipped", reason="degenerate parcel extent",
                  level=logging.WARNING)
        return False
    x0, y0, x1, y1 = (float(v) for v in row)
    box = f"{{'min_x': {x0!r}, 'min_y': {y0!r}, 'max_x': {x1!r}, 'max_y': {y1!r}}}::BOX_2D"
    con.execute(
        f"CREATE OR REPLACE TABLE {config.PARCELS_TABLE} AS "
        f"SELECT * FROM {config.PARCELS_TABLE} ORDER BY ST_Hilbert({_GEOM}, {box})"
    )
    log_event(logger, "assembly.hilbert_ordered", table=config.PARCELS_TABLE)
    return True


def _create_rtree_index(con: Any, logger: Any) -> None:
    idx = config.PARCELS_GEOM_INDEX
    con.execute(f"DROP INDEX IF EXISTS {ident(idx)}")
    con.execute(f"CREATE INDEX {ident(idx)} ON {config.PARCELS_TABLE} USING RTREE ({_GEOM})")
    log_event(logger, "assembly.rtree_created", index=idx, table=config.PARCELS_TABLE)


def _verify_bbox_struct(con: Any, work_dir: str | Path, logger: Any) -> bool:
    """Confirm the parcels GeoParquet intermediate carries the explicit `bbox` STRUCT column
    (the §4 row-group-pruning convention the fetchers write via geoparquet.write_intermediate)."""
    pq = Path(work_dir) / "parcels.parquet"
    if not pq.exists():
        log_event(logger, "assembly.bbox_check_skipped", reason="parcels.parquet absent",
                  level=logging.WARNING)
        return False
    cols = {
        r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({sql_str(pq)})"
        ).fetchall()
    }
    ok = "bbox" in cols and "geometry" in cols
    if not ok:
        log_event(logger, "assembly.bbox_missing", file="parcels.parquet",
                  columns=sorted(cols), level=logging.WARNING)
    return ok


# ── GEO-13: enrichment ─────────────────────────────────────────────────────────
def enrich(con: Any, work_dir: str | Path, settings: Any, logger: Any) -> dict:
    """Compute and store the derived per-parcel columns (FR-A4). Adds columns in place so the
    R-tree index + Hilbert order from assemble() survive. Returns a summary for the manifest.
    No-op when there is no parcels table (mirrors assemble's empty-build guard)."""
    present = _base_tables(con)
    if config.PARCELS_TABLE not in present:
        log_event(logger, "enrichment.skipped", reason="no parcels table (empty build)")
        return {"ran": False}
    _add_columns(con, config.PARCELS_TABLE, ENRICH_COLUMNS)
    _build_centroids(con)
    _build_nearest(con, present)
    _apply_sql_enrichment(con, present)
    zonal = _apply_zonal_slope(con, work_dir, logger)
    summary = {"ran": True, **_enrichment_summary(con, zonal)}
    _drop_temps(con)
    log_event(logger, "enrichment.done", **summary)
    return summary


def _add_columns(con: Any, table: str, coldefs: tuple[tuple[str, str], ...]) -> None:
    existing = _columns(con, table)
    for name, typ in coldefs:
        if name not in existing:
            con.execute(f"ALTER TABLE {ident(table)} ADD COLUMN {ident(name)} {typ}")


def _build_centroids(con: Any) -> None:
    """Metric centroid per parcel (reused by every nearest-neighbor and the enrich select)."""
    metric = crs.to_metric_sql(_GEOM)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _cent AS
        SELECT id, {_GEOM} AS geom, ST_Centroid({metric}) AS c26911
        FROM {config.PARCELS_TABLE}
        """
    )


def _build_nearest(con: Any, present: set[str]) -> None:
    """Per-parcel nearest substation / transmission line / GHI point (+ optional EIA generator),
    each computed in EPSG:26911. Empty source tables yield no rows (NULL after the LEFT JOIN).

    Streams a GROUP BY aggregate instead of ``row_number()`` over the full parcel×feature
    cross-join. The window form materialized + sorted hundreds of millions of rows (421k parcels ×
    every feature), spilling tens of GB to the temp dir (OOM, then "No space left on device").
    ``min`` / ``arg_min(value, {'d': dist, 'i': id})`` keep the original deterministic (distance,
    then id) tie-break while holding only one running min per parcel — verified identical to the
    window form, ties included. ``min(dist)`` covers tx/eia (only the distance is kept).
    """
    m = crs.to_metric_sql

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _nearest_sub AS
        WITH s AS (
            SELECT id AS sub_id, max_voltage_kv, {m('geom')} AS g
            FROM substations WHERE {_GEOM} IS NOT NULL
        )
        SELECT id, best.sub_id AS sub_id, best.kv AS nearest_sub_kv, best.dist AS dist_sub_m
        FROM (
            SELECT c.id AS id,
                   arg_min(
                       {{'sub_id': s.sub_id, 'kv': s.max_voltage_kv, 'dist': ST_Distance(c.c26911, s.g)}},
                       {{'d': ST_Distance(c.c26911, s.g), 'i': s.sub_id}}
                   ) AS best
            FROM _cent c, s
            GROUP BY c.id
        ) n
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _nearest_tx AS
        WITH l AS (
            SELECT id AS line_id, {m('geom')} AS g
            FROM transmission_lines WHERE {_GEOM} IS NOT NULL
        )
        SELECT c.id AS id, min(ST_Distance(c.c26911, l.g)) AS dist_tx_m
        FROM _cent c, l
        GROUP BY c.id
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _nearest_ghi AS
        WITH g AS (
            SELECT id AS ghi_id, avg_ghi, {m('geom')} AS gm
            FROM ghi_grid WHERE {_GEOM} IS NOT NULL
        )
        SELECT c.id AS id,
               arg_min(g.avg_ghi, {{'d': ST_Distance(c.c26911, g.gm), 'i': g.ghi_id}}) AS ghi
        FROM _cent c, g
        GROUP BY c.id
        """
    )
    if "eia_generators" in present:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _nearest_eia AS
            WITH e AS (
                SELECT id AS eia_id, {m('geom')} AS g
                FROM eia_generators WHERE {_GEOM} IS NOT NULL
            )
            SELECT c.id AS id, min(ST_Distance(c.c26911, e.g)) AS eia_nearest_m
            FROM _cent c, e
            GROUP BY c.id
            """
        )


def _apply_sql_enrichment(con: Any, present: set[str]) -> None:
    """Assemble every SQL-derived column into a temp table keyed by parcel id, then UPDATE the
    parcels table in place. Optional layers (poi_competition, exclusions, EIA) degrade to
    NULL/FALSE when their table is absent."""
    to_storage = crs.to_storage_sql("c.c26911")

    # zoning_class is the zone of the polygon containing the parcel centroid. Use the SAME
    # point that is stored as centroid_4326 (the metric centroid reprojected to storage CRS),
    # not a fresh ST_Centroid(geom_4326) — projection is non-linear, so the two centroids
    # differ and would make zoning_class inconsistent with the stored centroid. Tie-break on
    # smallest containing polygon then z.id so the result is deterministic across builds.
    zoning_pt = crs.to_storage_sql("c.c26911")
    zoning_expr = (
        f"(SELECT z.zone_code FROM zoning z "
        f" WHERE ST_Contains(z.geom, {zoning_pt}) "
        f" ORDER BY ST_Area(z.geom), z.id LIMIT 1)"
        if "zoning" in present else "NULL"
    )
    sfha_expr = (
        "EXISTS (SELECT 1 FROM flood_sfha f WHERE ST_Intersects(c.geom, f.geom))"
        if "flood_sfha" in present else "FALSE"
    )

    def excl(kind: str) -> str:
        if "exclusions" not in present:
            return "FALSE"
        return (
            f"EXISTS (SELECT 1 FROM exclusions e WHERE e.kind = {sql_str(kind)} "
            f"AND ST_Intersects(c.geom, e.geom))"
        )

    has_poi = "poi_competition" in present
    has_eia = "eia_generators" in present
    # Aggregate poi_competition to exactly one row per substation_id before joining, so the
    # UPDATE ... FROM can never fan out to multiple rows per parcel even if the CAISO fetcher's
    # one-row-per-POI contract ever changes (a defensive collapse, not relying on the source).
    poi_join = (
        "LEFT JOIN (SELECT substation_id, max(mw_within_radius) AS mw_within_radius, "
        "max(n_within_radius) AS n_within_radius FROM poi_competition "
        "GROUP BY substation_id) pc ON pc.substation_id = ns.sub_id"
        if has_poi else ""
    )
    eia_join = "LEFT JOIN _nearest_eia ne ON ne.id = c.id" if has_eia else ""

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _enrich AS
        SELECT
            c.id,
            c.c26911 AS centroid_26911,
            {to_storage} AS centroid_4326,
            ns.nearest_sub_kv,
            ns.dist_sub_m,
            nt.dist_tx_m,
            ng.ghi,
            {'pc.mw_within_radius' if has_poi else 'NULL'} AS poi_competition_mw,
            {'pc.n_within_radius' if has_poi else 'NULL'} AS poi_competition_n,
            {sfha_expr} AS sfha_flag,
            {zoning_expr} AS zoning_class,
            {excl('protected_area')} AS excl_protected_area,
            {excl('open_water')} AS excl_open_water,
            {excl('built_up')} AS excl_built_up,
            {'ne.eia_nearest_m' if has_eia else 'NULL'} AS eia_nearest_m
        FROM _cent c
        LEFT JOIN _nearest_sub ns ON ns.id = c.id
        LEFT JOIN _nearest_tx nt ON nt.id = c.id
        LEFT JOIN _nearest_ghi ng ON ng.id = c.id
        {poi_join}
        {eia_join}
        """
    )
    con.execute(
        f"""
        UPDATE {config.PARCELS_TABLE} SET
            centroid_26911 = e.centroid_26911,
            centroid_4326 = e.centroid_4326,
            nearest_sub_kv = e.nearest_sub_kv,
            dist_sub_m = e.dist_sub_m,
            dist_tx_m = e.dist_tx_m,
            ghi = e.ghi,
            poi_competition_mw = e.poi_competition_mw,
            poi_competition_n = e.poi_competition_n,
            sfha_flag = e.sfha_flag,
            zoning_class = e.zoning_class,
            excl_protected_area = e.excl_protected_area,
            excl_open_water = e.excl_open_water,
            excl_built_up = e.excl_built_up,
            eia_nearest_m = e.eia_nearest_m
        FROM _enrich e
        WHERE {config.PARCELS_TABLE}.id = e.id
        """
    )


def _apply_zonal_slope(con: Any, work_dir: str | Path, logger: Any) -> dict:
    """Sample the mean slope under each parcel from the slope raster(s) and store it. Fails
    soft per parcel (parcels over nodata get NULL); never aborts the build."""
    out = {"screening": 0, "final": 0}
    if "slope_raster" not in _base_tables(con):
        log_event(logger, "enrichment.slope_skipped", reason="no slope_raster table",
                  level=logging.WARNING)
        return out
    by_role = {
        r[0]: r[1] for r in con.execute("SELECT role, path FROM slope_raster").fetchall()
    }
    metric = crs.to_metric_sql(_GEOM)
    geo_rows = con.execute(
        f"SELECT id, ST_AsGeoJSON({metric}) FROM {config.PARCELS_TABLE}"
    ).fetchall()
    shapes = [(int(i), json.loads(g)) for i, g in geo_rows if g]
    if not shapes:
        return out

    for role, col in (("screening", "mean_slope_pct"), ("final", "mean_slope_pct_final")):
        path = by_role.get(role)
        if not path:
            continue
        raster = Path(work_dir) / path
        if not raster.exists():
            log_event(logger, "enrichment.slope_raster_missing", role=role, path=path,
                      level=logging.WARNING)
            continue
        try:
            means = _zonal_mean_slope(raster, shapes)
        except Exception as err:  # noqa: BLE001 — zonal sampling must fail soft, never the build
            log_event(logger, "enrichment.zonal_failed", role=role, path=path,
                      error=str(err), level=logging.WARNING)
            continue
        out[role] = _update_scalar_col(con, col, means)
    return out


def _zonal_mean_slope(raster_path: Path, shapes: list[tuple[int, dict]]) -> dict[int, float]:
    """Mean slope-percent under each parcel. Burns parcel ids onto the slope grid once
    (all_touched, so parcels smaller than a cell still register) and reduces by id with a
    bincount — O(raster cells), not per-polygon. Parcel ids are positive (row_number); 0 is
    reserved as the no-parcel background. rasterio/numpy imported lazily."""
    import numpy as np
    import rasterio
    from rasterio.features import rasterize

    with rasterio.open(raster_path) as ds:
        # The parcel geometries are reprojected to EPSG:26911 (SLOPE_METRIC_CRS) for sampling,
        # so the raster grid MUST be the same CRS or the burn-in is silently misaligned. Refuse
        # a mismatched raster (the caller turns this into a logged skip, not wrong slope data).
        epsg = ds.crs.to_epsg() if ds.crs is not None else None
        if epsg is not None and epsg != config.SLOPE_METRIC_CRS:
            raise ValueError(
                f"slope raster {raster_path.name} is EPSG:{epsg}, expected "
                f"EPSG:{config.SLOPE_METRIC_CRS}; refusing to mis-sample"
            )
        band = ds.read(1)
        nodata = ds.nodata
        transform = ds.transform
        shape = band.shape

    # row_number() ids fit int32 (county has ~4e5 parcels « 2.1e9); GDAL rasterize needs int32.
    burn = [(geom, int(pid)) for pid, geom in shapes]
    id_raster = rasterize(burn, out_shape=shape, transform=transform, fill=0,
                          all_touched=True, dtype="int32")
    valid = id_raster != 0
    band = band.astype("float64")
    if nodata is not None:
        valid &= band != float(nodata)
    valid &= np.isfinite(band)
    ids = id_raster[valid]
    if ids.size == 0:
        return {}
    vals = band[valid]
    length = int(ids.max()) + 1
    sums = np.bincount(ids, weights=vals, minlength=length)
    counts = np.bincount(ids, minlength=length)
    nz = np.nonzero(counts)[0]
    return {int(i): float(sums[i] / counts[i]) for i in nz}


def _update_scalar_col(con: Any, col: str, values: dict[int, float]) -> int:
    con.execute("CREATE OR REPLACE TEMP TABLE _scalar (id BIGINT, v DOUBLE)")
    if values:
        con.executemany("INSERT INTO _scalar VALUES (?, ?)", list(values.items()))
    con.execute(
        f"UPDATE {config.PARCELS_TABLE} SET {ident(col)} = _scalar.v "
        f"FROM _scalar WHERE {config.PARCELS_TABLE}.id = _scalar.id"
    )
    return len(values)


def _enrichment_summary(con: Any, zonal: dict) -> dict:
    n = _count(con, config.PARCELS_TABLE)
    classified = con.execute(
        f"SELECT count(*) FROM {config.PARCELS_TABLE} WHERE zoning_class IS NOT NULL"
    ).fetchone()[0]
    sfha = con.execute(
        f"SELECT count(*) FROM {config.PARCELS_TABLE} WHERE sfha_flag"
    ).fetchone()[0]
    with_slope = con.execute(
        f"SELECT count(*) FROM {config.PARCELS_TABLE} WHERE mean_slope_pct IS NOT NULL"
    ).fetchone()[0]
    return {
        "parcels": n,
        "zonal_screening": zonal.get("screening", 0),
        "zonal_final": zonal.get("final", 0),
        "with_slope": with_slope,
        "zoning_classified": classified,
        "sfha_parcels": sfha,
    }


def _drop_temps(con: Any) -> None:
    for t in ("_cent", "_nearest_sub", "_nearest_tx", "_nearest_ghi", "_nearest_eia",
              "_enrich", "_scalar"):
        con.execute(f"DROP TABLE IF EXISTS {ident(t)}")
