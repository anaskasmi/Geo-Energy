"""GEO-12 / GEO-13 — builder assembly + enrichment unit tests.

Builds a small artifact on disk (so the R-tree index path matches production), with hand-placed
geometries whose nearest neighbors / containment are known, then asserts assemble() produced
the index + Hilbert order and enrich() computed every FR-A4 derived column correctly.
"""

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("rasterio")

from pipeline import builder, config, db
from pipeline.logging_setup import get_logger

_LOG = get_logger("test.builder")


def _g(wkt: str) -> str:
    return f"ST_GeomFromText('{wkt}')"


def _seed(con):
    """Two parcels with known nearest substation / line / GHI point / overlaps."""
    # county_boundary (covers everything) + bbox columns the real schema carries.
    con.execute(
        f"""CREATE TABLE county_boundary AS SELECT '06029' AS geoid,
            {_g('POLYGON((-119.5 34.8, -118.5 34.8, -118.5 35.6, -119.5 35.6, -119.5 34.8))')} AS geom,
            -119.5 AS bbox_xmin, 34.8 AS bbox_ymin, -118.5 AS bbox_xmax, 35.6 AS bbox_ymax"""
    )
    # parcels: p1 ~(-118.95,35.05), p2 ~(-118.75,35.25). Real-schema columns.
    con.execute(
        f"""CREATE TABLE parcels AS SELECT * FROM (VALUES
            (1, 'APN-1', 'APN1', 1.0e6, 247.1,
             {_g('POLYGON((-119.0 35.0, -118.9 35.0, -118.9 35.1, -119.0 35.1, -119.0 35.0))')}),
            (2, 'APN-2', 'APN2', 1.0e6, 247.1,
             {_g('POLYGON((-118.8 35.2, -118.7 35.2, -118.7 35.3, -118.8 35.3, -118.8 35.2))')})
        ) t(id, apn, apn_norm, area_sqm, acres, geom)"""
    )
    # substations: A near p1 (230 kV), B near p2 (500 kV).
    con.execute(
        f"""CREATE TABLE substations AS SELECT * FROM (VALUES
            (10, 230.0, {_g('POINT(-118.96 35.06)')}),
            (11, 500.0, {_g('POINT(-118.74 35.26)')})
        ) t(id, max_voltage_kv, geom)"""
    )
    con.execute(
        f"""CREATE TABLE transmission_lines AS SELECT * FROM (VALUES
            (100, {_g('LINESTRING(-118.99 35.0, -118.99 35.2)')})
        ) t(id, geom)"""
    )
    # GHI grid: closer-to-p1 point = 5.5, closer-to-p2 point = 6.0.
    con.execute(
        f"""CREATE TABLE ghi_grid AS SELECT * FROM (VALUES
            (1, 5.5, {_g('POINT(-118.95 35.05)')}),
            (2, 6.0, {_g('POINT(-118.75 35.25)')})
        ) t(id, avg_ghi, geom)"""
    )
    # flood SFHA overlaps p1 only.
    con.execute(
        f"""CREATE TABLE flood_sfha AS SELECT * FROM (VALUES
            (1, true, {_g('POLYGON((-119.05 35.02, -118.93 35.02, -118.93 35.08, -119.05 35.08, -119.05 35.02))')})
        ) t(id, sfha_flag, geom)"""
    )
    # zoning: 'SOLAR' covers p1 centroid, 'IND' covers p2 centroid. `id` mirrors the real
    # zoning schema (row_number) so the deterministic tie-break (ORDER BY area, id) applies.
    con.execute(
        f"""CREATE TABLE zoning AS SELECT * FROM (VALUES
            (1, 'SOLAR', {_g('POLYGON((-119.1 34.95, -118.85 34.95, -118.85 35.15, -119.1 35.15, -119.1 34.95))')}),
            (2, 'IND',   {_g('POLYGON((-118.85 35.15, -118.6 35.15, -118.6 35.35, -118.85 35.35, -118.85 35.15))')})
        ) t(id, zone_code, geom)"""
    )
    # POI competition attached to substation A (the one p1 is nearest to).
    con.execute(
        "CREATE TABLE poi_competition AS SELECT 10 AS substation_id, 3 AS n_within_radius, "
        "150.0 AS mw_within_radius"
    )
    # exclusions: a protected area overlapping p2 only.
    con.execute(
        f"""CREATE TABLE exclusions AS SELECT * FROM (VALUES
            (1, 'protected_area', {_g('POLYGON((-118.82 35.18, -118.68 35.18, -118.68 35.32, -118.82 35.32, -118.82 35.18))')})
        ) t(id, kind, geom)"""
    )
    # EIA generator near p1.
    con.execute(
        f"CREATE TABLE eia_generators AS SELECT 1 AS id, {_g('POINT(-118.97 35.04)')} AS geom"
    )
    # caiso_queue must EXIST for the convergence check (empty is allowed).
    con.execute("CREATE TABLE caiso_queue (queue_id VARCHAR, geom GEOMETRY)")


def _write_slope_raster(path, value=10.0):
    """A constant-slope GeoTIFF in EPSG:26911 covering the parcels' metric extent."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    w, s, e, n = 300_000.0, 3_860_000.0, 360_000.0, 3_920_000.0  # UTM 11N box over the parcels
    width = height = 60
    data = np.full((height, width), value, dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(26911), transform=from_bounds(w, s, e, n, width, height),
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def built(tmp_path):
    con = db.connect(tmp_path / "t.duckdb", threads=2)
    _seed(con)
    slope = _write_slope_raster(tmp_path / "slope.tif", value=10.0)
    con.execute(
        f"CREATE TABLE {config.SLOPE_TABLE} AS SELECT 'screening' AS role, '{slope.name}' AS path"
    )
    asm = builder.assemble(con, tmp_path, settings=None, logger=_LOG)
    enr = builder.enrich(con, tmp_path, settings=None, logger=_LOG)
    try:
        yield con, asm, enr
    finally:
        con.close()


def test_assembly_indexes_and_orders(built):
    con, asm, _ = built
    assert asm["hilbert_ordered"] is True
    assert asm["parcels"] == 2
    # R-tree index exists on parcels.
    idxs = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    assert config.PARCELS_GEOM_INDEX in idxs


def test_assembly_requires_core_tables(tmp_path):
    con = db.connect(tmp_path / "bare.duckdb", threads=2)
    con.execute(f"CREATE TABLE parcels AS SELECT 1 AS id, {_g('POINT(-119 35)')} AS geom")
    try:
        with pytest.raises(Exception) as exc:
            builder.assemble(con, tmp_path, settings=None, logger=_LOG)
        assert "required core tables missing" in str(exc.value)
    finally:
        con.close()


def test_enrichment_columns_present(built):
    con, _, _ = built
    cols = {r[0] for r in con.execute("DESCRIBE parcels").fetchall()}
    for name, _typ in builder.ENRICH_COLUMNS:
        assert name in cols, name


def test_enrichment_nearest_and_flags(built):
    con, _, enr = built
    row = lambda pid: con.execute(  # noqa: E731
        "SELECT nearest_sub_kv, dist_sub_m, dist_tx_m, ghi, sfha_flag, zoning_class, "
        "poi_competition_mw, poi_competition_n, excl_protected_area, mean_slope_pct, "
        "ST_AsText(centroid_4326), ST_AsText(centroid_26911), eia_nearest_m "
        "FROM parcels WHERE id = ?", [pid]
    ).fetchone()

    p1 = row(1)
    p2 = row(2)
    # Nearest substation: p1 → A (230 kV), p2 → B (500 kV).
    assert p1[0] == 230.0 and p2[0] == 500.0
    # Distances are metric (meters) and positive and small (parcels sit next to their POIs).
    assert 0 < p1[1] < 5000 and 0 < p2[1] < 5000
    assert p1[2] is not None and p1[2] > 0  # nearest transmission line distance
    # GHI sampled from nearest grid point.
    assert p1[3] == 5.5 and p2[3] == 6.0
    # SFHA overlaps p1 only.
    assert p1[4] is True and p2[4] is False
    # Zoning by centroid containment.
    assert p1[5] == "SOLAR" and p2[5] == "IND"
    # POI competition attached to p1's nearest substation (A); p2's nearest (B) has none.
    assert p1[6] == 150.0 and p1[7] == 3
    assert p2[6] is None and p2[7] is None
    # Optional exclusion overlaps p2 only.
    assert p2[8] is True and p1[8] is False
    # Zonal mean slope sampled from the constant 10%% raster.
    assert p1[9] == pytest.approx(10.0) and p2[9] == pytest.approx(10.0)
    # Centroids present in both CRS.
    assert p1[10].startswith("POINT") and p1[11].startswith("POINT")
    # EIA cross-check distance present (a generator was staged near p1).
    assert p1[12] is not None and p1[12] > 0
    # Enrichment summary reports both parcels slope-sampled.
    assert enr["with_slope"] == 2 and enr["sfha_parcels"] == 1


def test_zoning_class_consistent_with_stored_centroid(built):
    """zoning_class must be derived from the SAME point stored as centroid_4326 — re-querying
    containment with the stored centroid must reproduce the stored class (no projection drift)."""
    con, _, _ = built
    mismatches = con.execute(
        """
        SELECT count(*) FROM parcels p
        WHERE p.zoning_class IS DISTINCT FROM (
            SELECT z.zone_code FROM zoning z
            WHERE ST_Contains(z.geom, p.centroid_4326)
            ORDER BY ST_Area(z.geom), z.id LIMIT 1
        )
        """
    ).fetchone()[0]
    assert mismatches == 0


def test_enrichment_survives_index(built):
    """The R-tree index and Hilbert order from assemble() must survive enrich()'s in-place
    ALTER/UPDATE (a CREATE OR REPLACE would have dropped them)."""
    con, _, _ = built
    idxs = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    assert config.PARCELS_GEOM_INDEX in idxs
    # Index is usable for a spatial predicate.
    n = con.execute(
        "SELECT count(*) FROM parcels WHERE ST_Intersects(geom, "
        f"{_g('POLYGON((-119.05 34.98, -118.88 34.98, -118.88 35.12, -119.05 35.12, -119.05 34.98))')})"
    ).fetchone()[0]
    assert n == 1  # only p1 lies in that box
