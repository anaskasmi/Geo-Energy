"""GEO-4 parcels fetcher: APN normalization, acreage, make-valid, GeoParquet + GeoJSON."""

import json

import pytest

from pipeline import config, spatial_io
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.parcels import ParcelsFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_builds_parcels_with_apn_norm_acres_and_validity(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_sample.geojson"))
    ctx = ctx_factory()
    result = ParcelsFetcher().fetch(ctx)

    assert result.table == "parcels" and result.feature_count == 4
    assert result.extra["apn_field"] == "APN"

    # APN normalization: trim, upper, strip non-alphanumeric (dashes/spaces removed).
    norm = dict(ctx.con.execute("SELECT apn, apn_norm FROM parcels WHERE apn_norm <> ''").fetchall())
    assert norm["  123-456-78 "] == "12345678"
    assert norm["987 654 32"] == "98765432"
    assert norm["BOW-TIE-01"] == "BOWTIE01"

    # Empty APN is kept but flagged.
    assert result.extra["null_apn"] == 1
    assert ctx.con.execute("SELECT count(*) FROM parcels WHERE apn_norm = ''").fetchone()[0] == 1

    # Every stored geometry is valid (the self-intersecting bowtie was repaired).
    assert ctx.con.execute("SELECT count(*) FROM parcels WHERE NOT ST_IsValid(geom)").fetchone()[0] == 0

    # Acreage is positive, plausible (~0.01°² parcels near lat 35), and consistent with m².
    amin, amax, ratio_ok = ctx.con.execute(
        f"SELECT min(acres), max(acres), "
        f"bool_and(abs(area_sqm - acres * {config.SQ_METERS_PER_ACRE}) < 1e-6) FROM parcels"
    ).fetchone()
    assert amin > 0 and amax < 1000
    assert ratio_ok

    # GeoParquet intermediate (real geo metadata + bbox struct).
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta

    # GeoJSON for tippecanoe (GEO-14): present, valid, and re-readable as 4 features.
    gj_path = result.extra["geojson"]
    fc = json.loads(open(gj_path).read())
    assert fc["type"] == "FeatureCollection"
    read = spatial_io.st_read_expr(gj_path)
    assert ctx.con.execute(f"SELECT count(*) FROM {read}").fetchone()[0] == 4


def test_resolves_alternate_apn_field_from_candidates(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_altfield.geojson"))
    ctx = ctx_factory()
    result = ParcelsFetcher().fetch(ctx)
    assert result.extra["apn_field"] == "ParcelID"
    apn, apn_norm = ctx.con.execute("SELECT apn, apn_norm FROM parcels").fetchone()
    assert apn == "AAA-111" and apn_norm == "AAA111"


def test_forced_apn_field_env(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_altfield.geojson"))
    monkeypatch.setenv(config.PARCELS_APN_FIELD_ENV, "ParcelID")
    assert ParcelsFetcher().fetch(ctx_factory()).extra["apn_field"] == "ParcelID"


def test_forced_apn_field_absent_raises(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_altfield.geojson"))
    monkeypatch.setenv(config.PARCELS_APN_FIELD_ENV, "NOPE")
    with pytest.raises(ValueError):
        ParcelsFetcher().fetch(ctx_factory())


def test_no_source_configured_raises(ctx_factory, monkeypatch):
    for var in (
        config.PARCELS_SOURCE_ENV,
        config.PARCELS_GEODAT_URL_ENV,
        config.PARCELS_SHAFTER_URL_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SourceError):
        ParcelsFetcher().fetch(ctx_factory())
