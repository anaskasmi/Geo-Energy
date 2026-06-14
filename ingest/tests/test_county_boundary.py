"""GEO-3 county boundary fetcher: filter to Kern, 4326 + reprojections, bbox, GeoParquet."""

import json

import pytest

from pipeline import config
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_builds_kern_with_reprojections_and_bbox(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    ctx = ctx_factory()
    result = CountyBoundaryFetcher().fetch(ctx)

    row = ctx.con.execute(
        "SELECT geoid, name, statefp, countyfp, "
        "ST_GeometryType(geom), ST_GeometryType(geom_utm), ST_GeometryType(geom_albers), "
        "bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax FROM county_boundary"
    ).fetchone()
    geoid, name, statefp, countyfp, gt, gt_utm, gt_albers, xmin, ymin, xmax, ymax = row
    assert (geoid, name, statefp, countyfp) == ("06029", "Kern", "06", "029")
    assert "POLYGON" in gt and "POLYGON" in gt_utm and "POLYGON" in gt_albers
    assert (xmin, ymin, xmax, ymax) == (-120.0, 34.9, -118.0, 35.6)  # stored bbox = source bbox (4326)

    # Reprojected geometry lands in plausible metric ranges (Kern, UTM 11N meters).
    e_min, n_min = ctx.con.execute(
        "SELECT ST_XMin(geom_utm), ST_YMin(geom_utm) FROM county_boundary"
    ).fetchone()
    assert 0 < e_min < 1_000_000
    assert 3_800_000 < n_min < 4_100_000

    # LayerResult + GeoParquet intermediate.
    assert result.table == "county_boundary" and result.feature_count == 1
    assert result.extra["geoid"] == "06029"
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta
    geo = json.loads(meta[b"geo"])
    assert geo["columns"]["geometry"]["encoding"] == "WKB"
    bbox = ctx.con.execute(
        f"SELECT bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax FROM read_parquet('{result.parquet_path}')"
    ).fetchone()
    assert bbox == (-120.0, 34.9, -118.0, 35.6)


def test_filters_to_kern_from_a_multi_county_source(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "counties_multi.geojson"))
    ctx = ctx_factory()
    result = CountyBoundaryFetcher().fetch(ctx)
    assert result.feature_count == 1
    assert ctx.con.execute("SELECT geoid FROM county_boundary").fetchone()[0] == "06029"


def test_missing_kern_raises(ctx_factory, monkeypatch, tmp_path):
    only_la = tmp_path / "la.geojson"
    only_la.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"GEOID": "06037", "STATEFP": "06", "COUNTYFP": "037", "NAME": "Los Angeles"},
            "geometry": {"type": "Polygon", "coordinates": [[[-118.6, 33.7], [-117.6, 33.7], [-117.6, 34.3], [-118.6, 34.3], [-118.6, 33.7]]]},
        }],
    }))
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(only_la))
    with pytest.raises(SourceError):
        CountyBoundaryFetcher().fetch(ctx_factory())


def test_source_crs_override_reprojects_into_storage(ctx_factory, monkeypatch):
    """Declaring the source as NAD83 (4269) exercises the reproject-to-4326 path; the
    Kern boundary stays put (NAD83→WGS84 is sub-meter at this scale)."""
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    monkeypatch.setenv(config.COUNTY_SOURCE_CRS_ENV, "4269")
    ctx = ctx_factory()
    CountyBoundaryFetcher().fetch(ctx)
    xmin, ymin, xmax, ymax = ctx.con.execute(
        "SELECT bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax FROM county_boundary"
    ).fetchone()
    assert xmin == pytest.approx(-120.0, abs=0.01)
    assert ymax == pytest.approx(35.6, abs=0.01)


def test_unset_source_with_no_url_attempts_download(ctx_factory, monkeypatch):
    """With no override and a bogus URL, the live path is taken and fails as a SourceError
    (proves we don't silently no-op when offline)."""
    monkeypatch.delenv(config.COUNTY_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.COUNTY_URL_ENV, "http://127.0.0.1:9/none.zip")
    with pytest.raises(SourceError):
        CountyBoundaryFetcher().fetch(ctx_factory())
