"""GEO-10 NREL solar GHI grid: the HTTP client (transport-injected, cached) + the fetcher
(staged-CSV path, county clip). Fully hermetic — no network."""

import json

import httpx
import pytest

from pipeline import config, nrel
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.ghi import GhiGridFetcher, _build_grid
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


def _sr_response(ghi=5.8, dni=7.2, tilt=6.5):
    return {
        "version": "1.0.0",
        "outputs": {
            "avg_dni": {"annual": dni, "monthly": {}},
            "avg_ghi": {"annual": ghi, "monthly": {}},
            "avg_lat_tilt": {"annual": tilt, "monthly": {}},
        },
    }


# ── nrel.py HTTP client (transport-injected) ───────────────────────────────────

def test_fetch_solar_resource_parses_and_caches(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        assert request.url.params["api_key"] == "KEY"
        return httpx.Response(200, json=_sr_response(5.81, 7.22, 6.53))

    row = nrel.fetch_solar_resource(
        -119.2, 35.2, api_key="KEY", url=config.NREL_SOLAR_RESOURCE_URL,
        transport=httpx.MockTransport(handler), cache_dir=tmp_path,
    )
    assert row == {"lon": -119.2, "lat": 35.2, "avg_ghi": 5.81, "avg_dni": 7.22, "avg_lat_tilt": 6.53}
    assert calls["n"] == 1

    # A second call is served from the on-disk cache — the transport (which would 500) is
    # never hit again.
    def boom(request):
        raise AssertionError("cache miss: NREL was queried again")

    row2 = nrel.fetch_solar_resource(
        -119.2, 35.2, api_key="KEY", url=config.NREL_SOLAR_RESOURCE_URL,
        transport=httpx.MockTransport(boom), cache_dir=tmp_path,
    )
    assert row2 == row
    assert (tmp_path / "sr_35.2000_-119.2000.json").exists()


def test_fetch_grid_drops_no_data_and_throttles(tmp_path):
    slept: list[float] = []

    def handler(request):
        lat = float(request.url.params["lat"])
        if lat == 35.9:  # a "no data" point (e.g. outside coverage)
            return httpx.Response(200, json={"outputs": {"avg_ghi": {"annual": None}}})
        return httpx.Response(200, json=_sr_response())

    points = [(-119.2, 35.1), (-119.0, 35.9), (-118.8, 35.3)]
    rows = nrel.fetch_grid(
        points, api_key="KEY", url=config.NREL_SOLAR_RESOURCE_URL,
        transport=httpx.MockTransport(handler), rate_per_hour=1000,
        cache_dir=tmp_path, sleep=slept.append,
    )
    assert [r["lat"] for r in rows] == [35.1, 35.3]  # the no-data point is dropped
    # Throttled between live calls: 3 calls → 2 inter-call sleeps of 3600/1000 = 3.6 s.
    assert slept == [pytest.approx(3.6), pytest.approx(3.6)]


def test_fetch_grid_requires_api_key():
    with pytest.raises(SourceError):
        nrel.fetch_grid([(-119.0, 35.0)], api_key="", url=config.NREL_SOLAR_RESOURCE_URL)


def test_api_key_is_redacted_on_http_error(tmp_path, caplog):
    """A non-2xx response must NOT leak the api_key (it rides in the request URL) into the
    raised SourceError or the logs — the key is the project's one secret."""
    secret = "SECRET_KEY_abc123"

    def handler(request):
        assert request.url.params["api_key"] == secret  # it really is on the wire
        return httpx.Response(403, json={"error": "forbidden"})

    with caplog.at_level("INFO"):
        with pytest.raises(SourceError) as ei:
            nrel.fetch_solar_resource(
                -119.0, 35.0, api_key=secret, url=config.NREL_SOLAR_RESOURCE_URL,
                transport=httpx.MockTransport(handler), retries=2, backoff=0,
                sleep=lambda *_: None, cache_dir=tmp_path,
            )
    assert secret not in str(ei.value)
    assert "api_key=***" in str(ei.value)
    assert secret not in caplog.text  # not in the retry logs either


def test_corrupt_cache_self_heals(tmp_path):
    """A truncated/corrupt cache file must not wedge the run — it is discarded and re-fetched."""
    cp = tmp_path / "sr_35.0000_-119.0000.json"
    cp.write_text("{ this is not valid json")

    def handler(request):
        return httpx.Response(200, json=_sr_response(5.55, 7.0, 6.2))

    row = nrel.fetch_solar_resource(
        -119.0, 35.0, api_key="K", url=config.NREL_SOLAR_RESOURCE_URL,
        transport=httpx.MockTransport(handler), cache_dir=tmp_path,
    )
    assert row["avg_ghi"] == 5.55
    assert json.loads(cp.read_text())["outputs"]["avg_ghi"]["annual"] == 5.55  # cache repaired


# ── the fetcher (offline CSV path) ─────────────────────────────────────────────

def test_ghi_builds_from_staged_csv(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.NREL_GHI_SOURCE_ENV, str(FIXTURES / "ghi_grid_sample.csv"))

    result = GhiGridFetcher().fetch(ctx)

    assert result.table == config.GHI_GRID_TABLE and result.feature_count == 4
    ghi = ctx.con.execute(
        f"SELECT round(avg_ghi,2) FROM {config.GHI_GRID_TABLE} ORDER BY lat, lon LIMIT 1"
    ).fetchone()[0]
    assert ghi == 5.70
    # geometry is a real point in 4326
    gtype = ctx.con.execute(
        f"SELECT DISTINCT ST_GeometryType(geom) FROM {config.GHI_GRID_TABLE}"
    ).fetchone()[0]
    assert "POINT" in gtype.upper()
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_ghi_clips_to_county(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    csv = tmp_path / "ghi.csv"
    csv.write_text(
        "lon,lat,avg_ghi,avg_dni,avg_lat_tilt\n"
        "-119.0,35.2,5.9,7.2,6.6\n"        # inside the county fixture
        "-130.0,40.0,6.5,8.0,7.0\n"        # far outside → must be clipped away
    )
    monkeypatch.setenv(config.NREL_GHI_SOURCE_ENV, str(csv))
    result = GhiGridFetcher().fetch(ctx)
    assert result.feature_count == 1
    lon = ctx.con.execute(f"SELECT lon FROM {config.GHI_GRID_TABLE}").fetchone()[0]
    assert lon == -119.0


def test_build_grid_clips_to_county(ctx_factory, monkeypatch):
    from pipeline import clip

    ctx = _with_county(ctx_factory(), monkeypatch)
    bbox = clip.county_bbox(ctx.con)
    pts = _build_grid(ctx.con, bbox, spacing=0.25)
    assert len(pts) > 0
    # every returned point is inside the county polygon
    for lon, lat in pts:
        hit = ctx.con.execute(
            "SELECT ST_Intersects(ST_Point(?, ?), (SELECT geom FROM county_boundary))",
            [lon, lat],
        ).fetchone()[0]
        assert hit


def test_ghi_without_county_boundary_raises(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.NREL_GHI_SOURCE_ENV, str(FIXTURES / "ghi_grid_sample.csv"))
    with pytest.raises(SourceError):
        GhiGridFetcher().fetch(ctx_factory())


def test_ghi_no_source_configured_raises(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.NREL_GHI_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.NREL_GHI_URL_ENV, "")  # blank beats the live default
    with pytest.raises(SourceError):
        GhiGridFetcher().fetch(ctx)
