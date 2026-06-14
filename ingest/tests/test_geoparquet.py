"""GeoParquet intermediate convention: real GeoParquet metadata + bbox struct + round-trip."""

import json

import pytest

duckdb = pytest.importorskip("duckdb")
pq = pytest.importorskip("pyarrow.parquet")

from pipeline import db, geoparquet  # noqa: E402


def test_intermediate_is_real_geoparquet_with_bbox(tmp_path):
    con = db.connect(":memory:", threads=2)
    out = tmp_path / "layer.parquet"

    # A single polygon in EPSG:4326.
    select_sql = (
        "SELECT 1 AS id, "
        "ST_GeomFromText('POLYGON((-119 35, -118 35, -118 36, -119 36, -119 35))') AS geom"
    )
    geoparquet.write_intermediate(con, select_sql=select_sql, out_path=out, geom_col="geom")
    assert out.exists()

    # It is genuine GeoParquet: file-level `geo` metadata is present and well-formed.
    meta = pq.read_metadata(out).metadata
    assert meta is not None and b"geo" in meta, "missing GeoParquet `geo` metadata"
    geo = json.loads(meta[b"geo"])
    assert geo["version"].startswith("1.")
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["encoding"] == "WKB"

    # The explicit bbox struct covers the polygon.
    bbox = con.execute(
        f"SELECT bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax FROM read_parquet('{out}')"
    ).fetchone()
    assert bbox == (-119.0, 35.0, -118.0, 36.0)

    # Round-trips back into a usable DuckDB GEOMETRY column.
    read_sql = geoparquet.read_intermediate_sql(out, geom_alias="geom")
    n, gtype = con.execute(
        f"SELECT count(*), any_value(ST_GeometryType(geom)) FROM ({read_sql})"
    ).fetchone()
    assert n == 1
    assert "POLYGON" in gtype.upper()


def test_path_with_apostrophe_does_not_break_sql(tmp_path):
    """A data dir containing a single quote (legal on macOS/Linux) must not break the SQL."""
    con = db.connect(":memory:", threads=2)
    spicy = tmp_path / "O'Brien_data"
    spicy.mkdir()
    out = spicy / "layer.parquet"
    select_sql = "SELECT 1 AS id, ST_Point(-118.7, 35.3) AS geom"
    geoparquet.write_intermediate(con, select_sql=select_sql, out_path=out, geom_col="geom")
    read_sql = geoparquet.read_intermediate_sql(out)
    assert con.execute(f"SELECT count(*) FROM ({read_sql})").fetchone()[0] == 1
