"""GEO-6 HIFLD fetchers: county clip, voltage sentinel/zero nulling, GeoParquet output."""

import pytest

from pipeline import config
from pipeline.clip import county_bbox
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.hifld import SubstationsFetcher, TransmissionLinesFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_county(ctx, monkeypatch):
    """Build the county_boundary table (the clip geometry) into ctx.con first."""
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    CountyBoundaryFetcher().fetch(ctx)
    return ctx


def test_transmission_clips_to_county_and_nulls_bad_voltage(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.TRANSMISSION_SOURCE_ENV, str(FIXTURES / "transmission_sample.geojson"))
    result = TransmissionLinesFetcher().fetch(ctx)

    # TL-C is fully outside the county and dropped; A, B, D, E are kept.
    assert result.table == "transmission_lines" and result.feature_count == 4
    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM transmission_lines").fetchall()}
    assert kept == {"TL-A", "TL-B", "TL-D", "TL-E"}

    # Voltage: real values survive; the -999999 sentinel (TL-D) and 0 (TL-E) become NULL.
    volts = dict(ctx.con.execute("SELECT source_id, voltage_kv FROM transmission_lines").fetchall())
    assert volts["TL-A"] == 230 and volts["TL-B"] == 115
    assert volts["TL-D"] is None and volts["TL-E"] is None
    assert result.extra["with_voltage"] == 2 and result.extra["voltage_field"] == "VOLTAGE"

    # TL-B crosses the eastern boundary (x=-118.0); the clip truncates it there.
    xmax = ctx.con.execute(
        "SELECT ST_XMax(geom) FROM transmission_lines WHERE source_id = 'TL-B'"
    ).fetchone()[0]
    assert xmax == pytest.approx(-118.0, abs=1e-9)

    # Every stored geometry is line-typed, valid, and inside the county bbox.
    bad = ctx.con.execute(
        "SELECT count(*) FROM transmission_lines "
        "WHERE NOT ST_IsValid(geom) OR ST_GeometryType(geom) NOT IN ('LINESTRING','MULTILINESTRING')"
    ).fetchone()[0]
    assert bad == 0
    xmin, ymin, xmax2, ymax = county_bbox(ctx.con)
    outside = ctx.con.execute(
        f"SELECT count(*) FROM transmission_lines "
        f"WHERE ST_XMin(geom) < {xmin}-1e-9 OR ST_XMax(geom) > {xmax2}+1e-9 "
        f"OR ST_YMin(geom) < {ymin}-1e-9 OR ST_YMax(geom) > {ymax}+1e-9"
    ).fetchone()[0]
    assert outside == 0

    # GeoParquet intermediate with geo metadata.
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_substations_filter_to_county_and_null_voltage(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(FIXTURES / "substations_sample.geojson"))
    result = SubstationsFetcher().fetch(ctx)

    # SS-4 is outside the county and dropped; S1, S2, S3 kept.
    assert result.table == "substations" and result.feature_count == 3
    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM substations").fetchall()}
    assert kept == {"SS-1", "SS-2", "SS-3"}

    volts = dict(ctx.con.execute("SELECT source_id, max_voltage_kv FROM substations").fetchall())
    assert volts["SS-1"] == 230        # real value kept
    assert volts["SS-2"] is None       # 0 (unknown) nulled
    assert volts["SS-3"] is None       # -999999 sentinel nulled
    assert result.extra["with_voltage"] == 1 and result.extra["voltage_field"] == "MAX_VOLT"

    # min_voltage_kv resolves its own field (MIN_VOLT) and applies the same nulling.
    minv = dict(ctx.con.execute("SELECT source_id, min_voltage_kv FROM substations").fetchall())
    assert minv["SS-1"] == 66          # real min kept
    assert minv["SS-2"] is None        # 0 nulled
    assert minv["SS-3"] is None        # -999999 nulled

    # All points, inside the county.
    types = ctx.con.execute("SELECT DISTINCT ST_GeometryType(geom) FROM substations").fetchall()
    assert types == [("POINT",)]
    inside = ctx.con.execute(
        "SELECT bool_and(ST_Intersects(s.geom, c.geom)) FROM substations s, county_boundary c"
    ).fetchone()[0]
    assert inside is True

    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_substations_resolves_alternate_voltage_field(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(FIXTURES / "substations_altfield.geojson"))
    result = SubstationsFetcher().fetch(ctx)
    assert result.feature_count == 1
    assert result.extra["voltage_field"] == "MAX_VOLTAG"  # alternate max-voltage field resolved
    name, mx, mn = ctx.con.execute(
        "SELECT name, max_voltage_kv, min_voltage_kv FROM substations"
    ).fetchone()
    assert name == "WHEELER RIDGE" and mx == 500 and mn == 230  # MIN_VOLTAG resolved too


def test_clip_uses_county_polygon_not_just_bbox(ctx_factory, monkeypatch, tmp_path):
    """An L-shaped county whose bbox != polygon. Features in the bbox "notch" (inside the
    bounding box but outside the polygon) must be dropped / truncated at the polygon edge —
    proving a true polygon clip rather than a cheaper bbox-only filter."""
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "county_lshape.geojson"))
    ctx = ctx_factory()
    CountyBoundaryFetcher().fetch(ctx)  # county polygon has a notch at x in (-119,-118), y in (35.25,35.6)

    # One substation inside the polygon, one in the notch (inside bbox, outside polygon).
    subs = tmp_path / "subs.geojson"
    subs.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"ID":"IN","NAME":"INSIDE","MAX_VOLT":230},'
        '"geometry":{"type":"Point","coordinates":[-119.5,35.0]}},'
        '{"type":"Feature","properties":{"ID":"NOTCH","NAME":"NOTCH","MAX_VOLT":230},'
        '"geometry":{"type":"Point","coordinates":[-118.5,35.4]}}]}'
    )
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(subs))
    SubstationsFetcher().fetch(ctx)
    kept = {r[0] for r in ctx.con.execute("SELECT source_id FROM substations").fetchall()}
    assert kept == {"IN"}  # the notch point is dropped (a bbox-only clip would have kept it)

    # A line from inside the polygon into the notch is truncated at the polygon edge x=-119.0,
    # not carried to its endpoint x=-118.5 (which lies inside the bbox).
    tl = tmp_path / "tl.geojson"
    tl.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"ID":"CROSS","VOLTAGE":230},'
        '"geometry":{"type":"LineString","coordinates":[[-119.5,35.4],[-118.5,35.4]]}}]}'
    )
    monkeypatch.setenv(config.TRANSMISSION_SOURCE_ENV, str(tl))
    TransmissionLinesFetcher().fetch(ctx)
    xmax = ctx.con.execute("SELECT ST_XMax(geom) FROM transmission_lines").fetchone()[0]
    assert xmax == pytest.approx(-119.0, abs=1e-9)


def test_transmission_without_county_boundary_raises(ctx_factory, monkeypatch):
    # No county_boundary table built first -> the clip dependency is unmet -> SourceError,
    # never a silent empty layer.
    monkeypatch.setenv(config.TRANSMISSION_SOURCE_ENV, str(FIXTURES / "transmission_sample.geojson"))
    with pytest.raises(SourceError):
        TransmissionLinesFetcher().fetch(ctx_factory())


def test_substations_no_source_configured_raises(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.SUBSTATIONS_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.SUBSTATIONS_URL_ENV, "")  # blank beats the live default
    with pytest.raises(SourceError):
        SubstationsFetcher().fetch(ctx)
