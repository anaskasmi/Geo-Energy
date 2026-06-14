"""spatial_io: ST_Read expression building, column introspection, candidate resolution."""

from pathlib import Path

import pytest

from pipeline import spatial_io

FIXTURES = Path(__file__).parent / "fixtures"


def test_st_read_expr_escapes_path_and_layer():
    assert spatial_io.st_read_expr("/data/O'Brien/x.geojson") == (
        "ST_Read('/data/O''Brien/x.geojson')"
    )
    assert spatial_io.st_read_expr("/d/x.gpkg", layer="parcels") == (
        "ST_Read('/d/x.gpkg', layer='parcels')"
    )


def test_vsizip_builds_gdal_member_path(tmp_path):
    z = tmp_path / "cb.zip"
    z.write_text("")  # resolve() needs it to exist on some platforms
    assert spatial_io.vsizip(z, "cb_2023_us_county_500k.shp") == (
        f"/vsizip/{z.resolve()}/cb_2023_us_county_500k.shp"
    )


def test_source_columns_lists_attrs_and_geom():
    duckdb = pytest.importorskip("duckdb")  # noqa: F841
    from pipeline import db

    con = db.connect(":memory:", threads=2)
    read = spatial_io.st_read_expr(FIXTURES / "parcels_sample.geojson")
    cols = spatial_io.source_columns(con, read)
    assert "APN" in cols
    assert spatial_io.GEOM_COLUMN in cols  # ST_Read names geometry `geom`


def test_pick_column_is_case_insensitive_and_first_wins():
    present = ["OBJECTID", "ParcelID", "geom"]
    assert spatial_io.pick_column(present, ["APN", "parcelid"], what="APN") == "ParcelID"
    assert spatial_io.pick_column(present, ["nope"], what="x", required=False) is None
    with pytest.raises(ValueError):
        spatial_io.pick_column(present, ["nope"], what="APN")
