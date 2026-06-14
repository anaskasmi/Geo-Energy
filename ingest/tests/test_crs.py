"""CRS helpers: always_xy is enforced and axis order is correct."""

import pytest

from pipeline import crs


def test_transform_sql_enforces_always_xy():
    sql = crs.transform_sql("geom", to_crs=26911)
    assert "always_xy := true" in sql
    assert "'EPSG:4326'" in sql and "'EPSG:26911'" in sql


def test_to_metric_and_storage_directions():
    assert crs.to_metric_sql("g", to_crs=3310) == (
        "ST_Transform(g, 'EPSG:4326', 'EPSG:3310', always_xy := true)"
    )
    assert crs.to_storage_sql("g", from_crs=26911) == (
        "ST_Transform(g, 'EPSG:26911', 'EPSG:4326', always_xy := true)"
    )


def test_pyproj_transformer_axis_order():
    """A Kern County lon/lat must land in a sane UTM 11N easting/northing.

    If axis order were swapped (lat/lon), the result would be wildly out of range —
    this is the regression guard for the always_xy rule.
    """
    pyproj = pytest.importorskip("pyproj")  # noqa: F841
    t = crs.transformer(4326, 26911)
    easting, northing = t.transform(-118.7, 35.3)  # (lon, lat) — Kern County
    assert 200_000 < easting < 500_000, easting
    assert 3_800_000 < northing < 4_000_000, northing


def test_transform_sql_executes_correctly_in_duckdb():
    """Run the emitted SQL in DuckDB so always_xy:=true syntax/behavior is guarded.

    String-equality tests above can't catch the spatial extension changing its named-arg
    syntax. This executes the helper output and checks the Kern UTM 11N bounds.
    """
    duckdb = pytest.importorskip("duckdb")
    from pipeline import db

    con = db.connect(":memory:", threads=2)
    metric = crs.to_metric_sql("ST_Point(-118.7, 35.3)", to_crs=26911)
    easting, northing = con.execute(f"SELECT ST_X(g), ST_Y(g) FROM (SELECT {metric} AS g)").fetchone()
    assert 200_000 < easting < 500_000, easting
    assert 3_800_000 < northing < 4_000_000, northing
