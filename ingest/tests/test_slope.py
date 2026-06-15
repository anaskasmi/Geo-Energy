"""GEO-9 slope fetcher: DEM → percent-grade slope raster, computed in 26911, clipped to county.

These are hermetic: no network, no seamless-3dep (neutralized in conftest). The DEM source is
a tiny GeoTIFF generated in tmp_path via `write_dem_geotiff`. The whole module is skipped if
rasterio is not installed (it is a declared dependency, present in the ingest image)."""

import pytest

from pipeline import config, crs
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.slope import SlopeFetcher, _slope_from_dem
from pipeline.sources import SourceError
from tests.conftest import FIXTURES, write_dem_geotiff

rasterio = pytest.importorskip("rasterio")
np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_county(ctx, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    CountyBoundaryFetcher().fetch(ctx)
    return ctx


def _county_dem(tmp_path, **kw):
    """A DEM covering the kern_county.geojson extent (-120..-118, 34.9..35.6) + margin."""
    return write_dem_geotiff(
        tmp_path / "dem.tif",
        west=-120.1, south=34.8, east=-117.9, north=35.7,
        width=kw.pop("width", 150), height=kw.pop("height", 75), **kw,
    )


def test_slope_builds_screening_and_clips(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(_county_dem(tmp_path)))
    monkeypatch.setenv(config.DEM_RES_ENV, "2000")  # coarse grid keeps the test fast

    result = SlopeFetcher().fetch(ctx)

    assert result.table == config.SLOPE_TABLE and result.feature_count == 1
    role, res_m, path, crs_epsg, valid, nodata = ctx.con.execute(
        f"SELECT role, resolution_m, path, crs_epsg, valid_cells, nodata FROM {config.SLOPE_TABLE}"
    ).fetchone()
    assert role == "screening"
    assert res_m == 2000
    assert path == config.SLOPE_SCREENING_TIF
    assert crs_epsg == config.SLOPE_METRIC_CRS == 26911
    assert valid > 0
    assert nodata == config.SLOPE_NODATA

    tif = ctx.work_dir / config.SLOPE_SCREENING_TIF
    assert tif.exists()
    with rasterio.open(tif) as ds:
        assert ds.crs.to_epsg() == 26911
        assert ds.count == 1
        assert ds.nodata == config.SLOPE_NODATA
        band = ds.read(1)
        assert band.shape == (ds.height, ds.width)
        assert (band == config.SLOPE_NODATA).any(), "no off-county cells were masked"
        assert (band != config.SLOPE_NODATA).any(), "no valid slope cells inside the county"
        valid_px = band[band != config.SLOPE_NODATA]
        assert (valid_px >= 0).all()  # slope percent is non-negative


def test_slope_value_on_known_ramp(tmp_path):
    """White-box: a 10% linear ramp in EPSG:26911 must yield ~10% slope (validates the metric
    dz/dx math, not just that *some* slope comes out)."""
    x0, y0, n, px = 330_000.0, 3_900_000.0, 100, 100.0  # 10 km box inside Kern, 100 m pixels
    cols = np.arange(n, dtype="float32")
    z = np.tile(0.10 * cols * px, (n, 1)).astype("float32")  # z = 0.10 * x_metres → 10% grade
    dem = write_dem_geotiff(
        tmp_path / "ramp_26911.tif",
        west=x0, south=y0, east=x0 + n * px, north=y0 + n * px,
        crs_epsg=26911, elevation=z,
    )
    # County polygon = the DEM footprint (buffered) reprojected 26911 → 4326, so nothing is
    # masked away and we measure slope over the whole ramp.
    t = crs.transformer(config.SLOPE_METRIC_CRS, config.CRS_STORAGE)
    m = 2_000.0
    box = [
        (x0 - m, y0 - m), (x0 + n * px + m, y0 - m),
        (x0 + n * px + m, y0 + n * px + m), (x0 - m, y0 + n * px + m), (x0 - m, y0 - m),
    ]
    ring = [list(t.transform(x, y)) for x, y in box]
    cg = {"type": "Polygon", "coordinates": [ring]}

    stats = _slope_from_dem(
        dem, source_crs=26911, res_m=100, county_geojson=cg,
        out_path=tmp_path / "slope_ramp.tif", metric_crs=26911,
    )
    assert stats["valid_cells"] > 0
    assert 9.0 < stats["mean_slope_pct"] < 11.0
    assert 9.0 < stats["max_slope_pct"] < 11.5


def test_slope_final_aoi_emits_second_raster(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(_county_dem(tmp_path, width=180, height=90)))
    monkeypatch.setenv(config.DEM_RES_ENV, "2000")
    # A small AOI well inside the county → the 10 m final pass stays small (windowed crop).
    monkeypatch.setenv(config.SLOPE_FINAL_AOI_ENV, "-119.10,35.15,-119.05,35.20")

    result = SlopeFetcher().fetch(ctx)

    assert result.feature_count == 2
    rows = ctx.con.execute(
        f"SELECT role, resolution_m FROM {config.SLOPE_TABLE} ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["screening", "final"]
    assert dict(rows)["final"] == config.SLOPE_FINAL_RES_M == 10
    assert (ctx.work_dir / config.SLOPE_FINAL_TIF).exists()


def test_slope_final_aoi_outside_dem_raises(ctx_factory, monkeypatch, tmp_path):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(_county_dem(tmp_path)))
    monkeypatch.setenv(config.DEM_RES_ENV, "2000")
    monkeypatch.setenv(config.SLOPE_FINAL_AOI_ENV, "10.0,10.0,10.1,10.1")  # nowhere near the DEM
    with pytest.raises(SourceError):
        SlopeFetcher().fetch(ctx)


def test_slope_screening_all_nodata_raises(ctx_factory, monkeypatch, tmp_path):
    """A DEM that does not overlap the county → the screening raster is entirely nodata; the
    required slope layer must fail loud (DEM/county mismatch), not emit an all-nodata raster."""
    ctx = _with_county(ctx_factory(), monkeypatch)
    # Within UTM-11N longitudes but far south of the county (no overlap) → all cells masked.
    dem = write_dem_geotiff(
        tmp_path / "far.tif", west=-116.0, south=33.0, east=-115.5, north=33.5,
        width=40, height=40,
    )
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(dem))
    monkeypatch.setenv(config.DEM_RES_ENV, "2000")
    with pytest.raises(SourceError):
        SlopeFetcher().fetch(ctx)


def test_slope_without_county_boundary_raises(ctx_factory, monkeypatch, tmp_path):
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(_county_dem(tmp_path)))
    with pytest.raises(SourceError):  # clip.county_bbox raises: GEO-3 must run first
        SlopeFetcher().fetch(ctx_factory())


def test_slope_no_source_configured_raises(ctx_factory, monkeypatch):
    ctx = _with_county(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.DEM_SOURCE_ENV, raising=False)
    # seamless_3dep is neutralized in conftest → the live path raises SourceError, not a fetch.
    with pytest.raises(SourceError):
        SlopeFetcher().fetch(ctx)
