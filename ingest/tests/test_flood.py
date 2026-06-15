"""GEO-8 FEMA NFHL flood fetcher: SFHA filter (FLD_ZONE A%/V%), county polygon clip,
GeoParquet output."""

import pytest

from pipeline import config
from pipeline.clip import county_bbox
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.flood import FloodSfhaFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_county(ctx, monkeypatch, fixture="kern_county.geojson"):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / fixture))
    CountyBoundaryFetcher().fetch(ctx)
    return ctx


def test_flood_filters_sfha_clips_to_county_and_emits_parquet(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(FIXTURES / "flood_sample.geojson"))
    result = FloodSfhaFetcher().fetch(ctx)

    # Kept: F1(AE), F2(A), F3(VE), F6(AO truncated). Dropped: F4(X), F5(D) non-SFHA;
    # F7(AE) entirely outside the county.
    assert result.table == "flood_sfha" and result.feature_count == 4
    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM flood_sfha").fetchall()}
    assert kept == {"F1", "F2", "F3", "F6"}

    # Only SFHA zones survive the filter; every retained row is flagged SFHA.
    zones = {r[0] for r in ctx.con.execute("SELECT upper(fld_zone) FROM flood_sfha").fetchall()}
    assert zones == {"AE", "A", "VE", "AO"}
    assert ctx.con.execute("SELECT count(*) FROM flood_sfha WHERE NOT sfha_flag").fetchone()[0] == 0
    assert result.extra["zone_field"] == "FLD_ZONE" and result.extra["distinct_zones"] == 4

    # F6 crosses the eastern boundary (x=-118.0); the clip truncates it there.
    xmax = ctx.con.execute(
        "SELECT ST_XMax(geom) FROM flood_sfha WHERE source_id = 'F6'"
    ).fetchone()[0]
    assert xmax == pytest.approx(-118.0, abs=1e-9)

    # zone_subtype carried through where present.
    subty = dict(ctx.con.execute("SELECT source_id, zone_subtype FROM flood_sfha").fetchall())
    assert subty["F2"] == "FLOODWAY"

    # Every stored geometry is polygonal, valid, and inside the county bbox.
    bad = ctx.con.execute(
        "SELECT count(*) FROM flood_sfha "
        "WHERE NOT ST_IsValid(geom) OR ST_GeometryType(geom) NOT IN ('POLYGON','MULTIPOLYGON')"
    ).fetchone()[0]
    assert bad == 0
    xmin, ymin, xmax2, ymax = county_bbox(ctx.con)
    outside = ctx.con.execute(
        f"SELECT count(*) FROM flood_sfha "
        f"WHERE ST_XMin(geom) < {xmin}-1e-9 OR ST_XMax(geom) > {xmax2}+1e-9 "
        f"OR ST_YMin(geom) < {ymin}-1e-9 OR ST_YMax(geom) > {ymax}+1e-9"
    ).fetchone()[0]
    assert outside == 0

    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_flood_clip_uses_county_polygon_not_just_bbox(ctx_factory, monkeypatch, tmp_path):
    """An L-shaped county whose bbox != polygon. An SFHA polygon in the bbox "notch" (inside
    the bounding box, outside the polygon) is dropped; one crossing into the notch is
    truncated at the polygon edge — proving a true polygon clip, not a bbox-only filter."""
    ctx = _with_county(ctx_factory(), monkeypatch, fixture="county_lshape.geojson")
    # notch = x in (-119,-118), y in (35.25,35.6), excluded from the L-shaped polygon.
    flood = tmp_path / "flood.geojson"
    flood.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"FLD_AR_ID":"NOTCH","FLD_ZONE":"AE"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-118.5,35.4],[-118.2,35.4],[-118.2,35.5],[-118.5,35.5],[-118.5,35.4]]]}},'
        '{"type":"Feature","properties":{"FLD_AR_ID":"CROSS","FLD_ZONE":"AE"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.2,35.4],[-118.5,35.4],[-118.5,35.5],[-119.2,35.5],[-119.2,35.4]]]}}]}'
    )
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(flood))
    FloodSfhaFetcher().fetch(ctx)

    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM flood_sfha").fetchall()}
    assert kept == {"CROSS"}  # NOTCH is dropped (a bbox-only clip would have kept it)
    # CROSS is truncated at the polygon edge x=-119.0, not carried to x=-118.5 (inside bbox).
    xmax = ctx.con.execute("SELECT ST_XMax(geom) FROM flood_sfha").fetchone()[0]
    assert xmax == pytest.approx(-119.0, abs=1e-9)


def test_flood_repairs_invalid_geometry(ctx_factory, monkeypatch, tmp_path):
    """A self-intersecting (bowtie) SFHA polygon must be repaired by ST_MakeValid and survive
    the ST_CollectionExtract(...,3) polygon extraction as a valid, non-empty polygon — proving
    the invalid-geometry path (untested by the clean rectangular fixtures)."""
    ctx = _with_county(ctx_factory(), monkeypatch)
    src = tmp_path / "bowtie.geojson"
    src.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"FLD_AR_ID":"BOW","FLD_ZONE":"AE"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.5,35.0],[-119.3,35.2],[-119.3,35.0],[-119.5,35.2],[-119.5,35.0]]]}}]}'
    )
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(src))
    FloodSfhaFetcher().fetch(ctx)
    n, valid, gtype, area = ctx.con.execute(
        "SELECT count(*), bool_and(ST_IsValid(geom)), "
        "       max(ST_GeometryType(geom)), max(ST_Area(geom)) FROM flood_sfha"
    ).fetchone()
    assert n == 1 and valid is True
    assert gtype in ("POLYGON", "MULTIPOLYGON") and area > 0


def test_flood_excludes_ani_and_explicit_non_sfha(ctx_factory, monkeypatch, tmp_path):
    """FEMA's 'AREA NOT INCLUDED' is A%-prefixed but NOT an SFHA, and a row explicitly flagged
    SFHA_TF='F' is non-SFHA — both must be dropped despite the A%/V% prefix filter."""
    ctx = _with_county(ctx_factory(), monkeypatch)
    src = tmp_path / "ani.geojson"
    src.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"FLD_AR_ID":"KEEP","FLD_ZONE":"AE","SFHA_TF":"T"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.5,35.0],[-119.4,35.0],[-119.4,35.1],[-119.5,35.1],[-119.5,35.0]]]}},'
        '{"type":"Feature","properties":{"FLD_AR_ID":"ANI","FLD_ZONE":"AREA NOT INCLUDED","SFHA_TF":"F"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.3,35.0],[-119.2,35.0],[-119.2,35.1],[-119.3,35.1],[-119.3,35.0]]]}},'
        '{"type":"Feature","properties":{"FLD_AR_ID":"FALSEA","FLD_ZONE":"A","SFHA_TF":"F"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.1,35.0],[-119.0,35.0],[-119.0,35.1],[-119.1,35.1],[-119.1,35.0]]]}}]}'
    )
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(src))
    result = FloodSfhaFetcher().fetch(ctx)
    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM flood_sfha").fetchall()}
    assert kept == {"KEEP"}  # ANI and the SFHA_TF='F' 'A' zone are both excluded
    assert result.extra["sfha_tf_field"] == "SFHA_TF"


def test_flood_without_county_boundary_raises(ctx_factory, monkeypatch):
    # No county_boundary built first -> the clip dependency is unmet -> SourceError.
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(FIXTURES / "flood_sample.geojson"))
    with pytest.raises(SourceError):
        FloodSfhaFetcher().fetch(ctx_factory())


def test_flood_all_non_sfha_raises(ctx_factory, monkeypatch, tmp_path):
    # A source with only X/D zones -> 0 SFHA after the filter -> fail loud (never an empty
    # exclusion layer that would silently mark every parcel flood-free).
    ctx = _with_county(ctx_factory(), monkeypatch)
    src = tmp_path / "nonsfha.geojson"
    src.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"FLD_AR_ID":"X1","FLD_ZONE":"X"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.5,35.0],[-119.3,35.0],[-119.3,35.2],[-119.5,35.2],[-119.5,35.0]]]}}]}'
    )
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(src))
    with pytest.raises(SourceError):
        FloodSfhaFetcher().fetch(ctx)


def test_flood_no_source_configured_raises(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.FLOOD_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.FLOOD_URL_ENV, "")  # blank beats the live default
    with pytest.raises(SourceError):
        FloodSfhaFetcher().fetch(ctx)
