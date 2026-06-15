"""GEO-11 EIA-860 generators (optional, off the critical path): staged CSV → in-county
points, and graceful skip when unconfigured."""

import pytest

from pipeline import config
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.eia import EiaGeneratorsFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_county(ctx, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    CountyBoundaryFetcher().fetch(ctx)
    return ctx


def test_eia_builds_and_clips_to_county(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.EIA860_SOURCE_ENV, str(FIXTURES / "eia860_sample.csv"))

    result = EiaGeneratorsFetcher().fetch(ctx)

    # 3 of 4 rows are inside the county fixture (the Los Angeles plant is clipped away).
    assert result.table == config.EIA_GENERATORS_TABLE and result.feature_count == 3
    plants = {r[0] for r in ctx.con.execute(
        f"SELECT plant_id FROM {config.EIA_GENERATORS_TABLE}"
    ).fetchall()}
    assert plants == {"55001", "55002", "55003"} and "99999" not in plants
    cap = ctx.con.execute(
        f"SELECT capacity_mw FROM {config.EIA_GENERATORS_TABLE} WHERE plant_id = '55001'"
    ).fetchone()[0]
    assert cap == 120.5
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_eia_skips_when_unconfigured(ctx_factory, monkeypatch):
    """Optional + off critical path: no source → empty table, NO exception (build proceeds)."""
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.EIA860_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.EIA860_URL_ENV, "")

    result = EiaGeneratorsFetcher().fetch(ctx)

    assert result.feature_count == 0
    assert ctx.con.execute(
        f"SELECT count(*) FROM {config.EIA_GENERATORS_TABLE}"
    ).fetchone()[0] == 0
    # The table exists with the right shape so enrichment can LEFT JOIN it.
    cols = {r[0] for r in ctx.con.execute(
        f"DESCRIBE {config.EIA_GENERATORS_TABLE}"
    ).fetchall()}
    assert {"plant_id", "capacity_mw", "geom"} <= cols


def test_eia_without_county_raises(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.EIA860_SOURCE_ENV, str(FIXTURES / "eia860_sample.csv"))
    with pytest.raises(SourceError):  # county_boundary (GEO-3) must run first
        EiaGeneratorsFetcher().fetch(ctx_factory())
