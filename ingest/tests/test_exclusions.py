"""GEO-11 exclusion overlays (optional, off the critical path): staged polygon kinds →
clipped `exclusions` table with a `kind` column, and graceful skip when none configured."""

import pytest

from pipeline import config
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.exclusions import ExclusionsFetcher
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")

# (kind, source_env) lookup from config, so tests follow the configured kinds.
_BY_KIND = {k: (src, crs, url) for (k, src, crs, url, _d) in config.EXCLUSION_LAYERS}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_county(ctx, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    CountyBoundaryFetcher().fetch(ctx)
    return ctx


def test_exclusions_builds_protected_kind(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    src_env = _BY_KIND["protected_area"][0]
    monkeypatch.setenv(src_env, str(FIXTURES / "exclusion_protected_sample.geojson"))

    result = ExclusionsFetcher().fetch(ctx)

    assert result.table == config.EXCLUSIONS_TABLE and result.feature_count == 2
    kinds = {r[0] for r in ctx.con.execute(
        f"SELECT DISTINCT kind FROM {config.EXCLUSIONS_TABLE}"
    ).fetchall()}
    assert kinds == {"protected_area"}
    # Every retained polygon is inside the county.
    outside = ctx.con.execute(
        f"SELECT count(*) FROM {config.EXCLUSIONS_TABLE} e, county_boundary c "
        f"WHERE NOT ST_Intersects(e.geom, c.geom)"
    ).fetchone()[0]
    assert outside == 0
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_exclusions_clips_outside_polygons(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    gj = tmp_path / "prot.geojson"
    gj.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"OBJECTID":1,"Unit_Nm":"In"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.5,35.1],[-119.3,35.1],[-119.3,35.3],[-119.5,35.3],[-119.5,35.1]]]}},'
        '{"type":"Feature","properties":{"OBJECTID":2,"Unit_Nm":"Out"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-110.0,40.0],[-109.8,40.0],[-109.8,40.2],[-110.0,40.2],[-110.0,40.0]]]}}'
        ']}'
    )
    monkeypatch.setenv(_BY_KIND["protected_area"][0], str(gj))
    result = ExclusionsFetcher().fetch(ctx)
    assert result.feature_count == 1
    assert ctx.con.execute(
        f"SELECT name FROM {config.EXCLUSIONS_TABLE}"
    ).fetchone()[0] == "In"


def test_exclusions_multiple_kinds(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    water = tmp_path / "water.geojson"
    water.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"GNIS_NAME":"Test Lake"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-118.9,35.2],[-118.7,35.2],[-118.7,35.4],[-118.9,35.4],[-118.9,35.2]]]}}'
        ']}'
    )
    monkeypatch.setenv(_BY_KIND["protected_area"][0], str(FIXTURES / "exclusion_protected_sample.geojson"))
    monkeypatch.setenv(_BY_KIND["open_water"][0], str(water))

    result = ExclusionsFetcher().fetch(ctx)

    assert result.feature_count == 3  # 2 protected + 1 water
    kinds = {r[0] for r in ctx.con.execute(
        f"SELECT DISTINCT kind FROM {config.EXCLUSIONS_TABLE}"
    ).fetchall()}
    assert kinds == {"protected_area", "open_water"}


def test_exclusions_skips_when_none_configured(ctx_factory, monkeypatch):
    """No kinds configured → empty table, NO exception (optional, off the critical path)."""
    ctx = _with_county(ctx_factory(), monkeypatch)
    for _src, _crs, _url in _BY_KIND.values():
        monkeypatch.delenv(_src, raising=False)

    result = ExclusionsFetcher().fetch(ctx)

    assert result.feature_count == 0
    cols = {r[0] for r in ctx.con.execute(
        f"DESCRIBE {config.EXCLUSIONS_TABLE}"
    ).fetchall()}
    assert {"kind", "geom"} <= cols
