"""Shared test fixtures for the API.

We build a tiny REAL ``site.duckdb`` (spatial loaded, a couple of trivial tables)
under ``<tmp>/current/`` — the same path readers follow in production — and point
``DATA_DIR`` at the tmp root so the app's lifespan opens it for real.
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest


def build_artifact(data_dir: Path) -> Path:
    """Create ``<data_dir>/current/site.duckdb`` with spatial + a manifest table."""
    current = data_dir / "current"
    current.mkdir(parents=True, exist_ok=True)
    artifact = current / "site.duckdb"

    con = duckdb.connect(database=str(artifact), read_only=False)
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        # Mirror the harness: a build_manifest(key, value JSON) table, plus a trivial
        # table so a normal request path has something to read.
        con.execute("CREATE TABLE build_manifest (key VARCHAR, value JSON)")
        con.execute("INSERT INTO build_manifest VALUES ('schema_version', '1')")
        con.execute("CREATE TABLE sites (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO sites VALUES (1, 'alpha'), (2, 'beta')")
    finally:
        con.close()
    return artifact


@pytest.fixture
def healthy_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """A tmp DATA_DIR containing a real, openable artifact."""
    build_artifact(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def empty_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """A tmp DATA_DIR with NO artifact (tolerant-startup / unhealthy path)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


# --- Scored artifact (GEO-16/17) ---------------------------------------------
# A REAL parcels table with the full enriched schema (the 16 ENRICH_COLUMNS + base columns),
# a Hilbert-equivalent R-tree index, and a curated zoning_rules.csv — all hand-built with known
# values so scoring is deterministic and verifiable end to end. Geometry/centroids are tiny
# squares around (-119.0, 35.30) in Kern County (EPSG:4326).
#
# Columns mirror builder.py exactly. The query polygon SCORED_POLYGON covers parcels 1-7.
SCORED_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-119.05, 35.28], [-118.93, 35.28], [-118.93, 35.33], [-119.05, 35.33], [-119.05, 35.28]]],
}
# A polygon that intersects nothing (far away) — for the empty-result path.
EMPTY_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-120.5, 36.5], [-120.4, 36.5], [-120.4, 36.6], [-120.5, 36.6], [-120.5, 36.5]]],
}

# (id, apn, acres, slope, slope_final, ghi, dist_tx, dist_sub, kv, poi_mw, poi_n,
#  sfha, zoning, excl_prot, excl_water, excl_built, eia_m, lng)  — each parcel a square at (lng, 35.30).
_PARCELS = [
    (1, "001", 120.0, 3.0, None, 6.3, 1000.0, 600.0, 230.0, None, None, False, "A", False, False, False, 4000.0, -119.04),
    (2, "002", 50.0, 8.0, None, 5.8, 9000.0, 5000.0, None, None, None, False, "M-1", False, False, False, 8000.0, -119.03),
    (3, "003", 200.0, 20.0, None, 6.0, 500.0, 300.0, 500.0, 1200.0, 3, False, "M-2", False, False, False, 1500.0, -119.02),  # excl: slope
    (4, "004", 10.0, 2.0, None, 6.4, 1200.0, 700.0, 115.0, None, None, False, "A", False, False, False, 5000.0, -119.01),  # excl(solar): acres<20
    (5, "005", 300.0, 2.0, None, 6.5, 800.0, 500.0, 230.0, None, None, True, "A", False, False, False, 3000.0, -119.00),  # excl: sfha
    (6, "006", 80.0, 5.0, None, 6.1, 2000.0, 1500.0, 115.0, None, None, False, "E", False, False, False, 6000.0, -118.99),  # excl: zoning E
    (7, "007", 60.0, None, None, 6.0, 3000.0, 2000.0, None, None, None, False, None, False, False, False, 7000.0, -118.98),  # slope unknown + zoning NULL -> kept (regression: NULL zoning must not drop)
]
_ZONING_RULES = [
    # zone_code, zone_name, use_case, permission, basis
    ("A", "Exclusive Agriculture", "solar", "conditional", "x"),
    ("A", "Exclusive Agriculture", "data_center", "conditional", "x"),
    ("M-1", "Light Industrial", "solar", "conditional", "x"),
    ("M-1", "Light Industrial", "data_center", "by_right", "x"),
    ("M-2", "Medium Industrial", "solar", "conditional", "x"),
    ("M-2", "Medium Industrial", "data_center", "by_right", "x"),
    ("E", "Estate", "solar", "prohibited", "x"),
    ("E", "Estate", "data_center", "prohibited", "x"),
]


def _sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def build_scored_artifact(data_dir: Path, *, with_zoning: bool = True) -> Path:
    """Create ``<data_dir>/current/site.duckdb`` with a fully enriched ``parcels`` table.

    Plus an R-tree index ``parcels_geom_rtree``, a ``caiso_queue_summary`` table for
    /api/context, and (optionally) the curated ``zoning_rules.csv`` next to the artifact.
    """
    current = data_dir / "current"
    current.mkdir(parents=True, exist_ok=True)
    artifact = current / "site.duckdb"

    con = duckdb.connect(database=str(artifact), read_only=False)
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        con.execute("CREATE TABLE build_manifest (key VARCHAR, value JSON)")
        con.execute("INSERT INTO build_manifest VALUES ('schema_version', '1')")
        con.execute(
            "CREATE TABLE parcels ("
            "id BIGINT, apn VARCHAR, apn_norm VARCHAR, area_sqm DOUBLE, acres DOUBLE, geom GEOMETRY,"
            "centroid_26911 GEOMETRY, centroid_4326 GEOMETRY, mean_slope_pct DOUBLE,"
            "mean_slope_pct_final DOUBLE, ghi DOUBLE, dist_tx_m DOUBLE, dist_sub_m DOUBLE,"
            "nearest_sub_kv DOUBLE, poi_competition_mw DOUBLE, poi_competition_n BIGINT,"
            "sfha_flag BOOLEAN, zoning_class VARCHAR, excl_protected_area BOOLEAN,"
            "excl_open_water BOOLEAN, excl_built_up BOOLEAN, eia_nearest_m DOUBLE)"
        )
        for p in _PARCELS:
            (pid, apn, acres, slope, slope_f, ghi, dtx, dsub, kv, poi_mw, poi_n,
             sfha, zoning, ep, ew, eb, eia, lng) = p
            lat = 35.30
            # 0.005° square around (lng, lat); centroid at (lng+0.0025, lat+0.0025).
            x0, y0, x1, y1 = lng, lat, lng + 0.005, lat + 0.005
            cx, cy = lng + 0.0025, lat + 0.0025
            geom = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
            cells = [
                _sql_lit(pid), _sql_lit(apn), _sql_lit(apn), _sql_lit(acres * 4046.8564224),
                _sql_lit(acres), f"ST_GeomFromText({_sql_lit(geom)})",
                "NULL", f"ST_GeomFromText({_sql_lit(f'POINT({cx} {cy})')})",
                _sql_lit(slope), _sql_lit(slope_f), _sql_lit(ghi), _sql_lit(dtx), _sql_lit(dsub),
                _sql_lit(kv), _sql_lit(poi_mw), _sql_lit(poi_n), _sql_lit(sfha), _sql_lit(zoning),
                _sql_lit(ep), _sql_lit(ew), _sql_lit(eb), _sql_lit(eia),
            ]
            con.execute(f"INSERT INTO parcels VALUES ({','.join(cells)})")
        con.execute("CREATE INDEX parcels_geom_rtree ON parcels USING RTREE (geom)")

        # CAISO Kern queue summary (long-form) for /api/context.
        con.execute(
            "CREATE TABLE caiso_queue_summary (category VARCHAR, key VARCHAR, n_projects BIGINT,"
            " total_mw DOUBLE, active_n_projects BIGINT, active_total_mw DOUBLE)"
        )
        con.execute(
            "INSERT INTO caiso_queue_summary VALUES"
            " ('total','all',42,5000.0,30,3600.0),"
            " ('by_type','Solar',20,2500.0,15,1800.0),"
            " ('by_type','Battery',12,1500.0,9,1100.0),"
            " ('by_status','Active',30,3600.0,30,3600.0)"
        )
    finally:
        con.close()

    if with_zoning:
        with open(current / "zoning_rules.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["zone_code", "zone_name", "use_case", "permission", "basis"])
            w.writerows(_ZONING_RULES)
    return artifact


@pytest.fixture
def scored_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """A tmp DATA_DIR with a fully enriched parcels artifact + zoning_rules.csv."""
    build_scored_artifact(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path
