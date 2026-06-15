"""Shared test helpers: fixture paths, Settings factory, and a FetchContext factory."""

import sys
from pathlib import Path

import pytest

from pipeline import config
from pipeline.config import Settings
from pipeline.fetchers.base import FetchContext
from pipeline.logging_setup import get_logger

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _hermetic_sources(monkeypatch):
    """Keep the suite offline. The live fetcher endpoints now have real defaults, so blank
    them for every test: a test that forgets to stage a *_SOURCE fixture then fails with a
    SourceError instead of making a network call. Tests set their own source explicitly.
    """
    monkeypatch.setenv(config.PARCELS_GEODAT_URL_ENV, "")
    monkeypatch.setenv(config.PARCELS_SHAFTER_URL_ENV, "")
    monkeypatch.setenv(config.COUNTY_URL_ENV, "")
    monkeypatch.setenv(config.TRANSMISSION_URL_ENV, "")
    monkeypatch.setenv(config.SUBSTATIONS_URL_ENV, "")
    monkeypatch.setenv(config.ZONING_URL_ENV, "")
    monkeypatch.setenv(config.GENERAL_PLAN_URL_ENV, "")
    monkeypatch.setenv(config.SPECIFIC_PLANS_URL_ENV, "")
    monkeypatch.setenv(config.FLOOD_URL_ENV, "")
    monkeypatch.setenv(config.NREL_GHI_URL_ENV, "")  # GHI (GEO-10) live NREL endpoint
    # Supplemental optional layers (GEO-11): EIA-860 + exclusion overlay URLs.
    monkeypatch.setenv(config.EIA860_URL_ENV, "")
    for _kind, _src_env, _crs_env, _url_env, _default in config.EXCLUSION_LAYERS:
        monkeypatch.setenv(_url_env, "")
    # CAISO (GEO-7) has no URL env — its live path lazy-imports gridstatus and calls the
    # network. requirements.txt bundles gridstatus into the same image as pytest, so `make
    # test` would otherwise let an un-staged CAISO fetch hit the live queue. Neutralize the
    # import for every test (an un-staged source then raises SourceError, never a network
    # call); a test that wants the live path stubs sys.modules["gridstatus"] itself.
    monkeypatch.setitem(sys.modules, "gridstatus", None)
    # Slope (GEO-9) has no URL env either — its live path lazy-imports `seamless_3dep` and
    # fetches USGS 3DEP DEM tiles over the network. Neutralize it so an un-staged slope test
    # raises SourceError instead of hitting 3DEP; tests use a pre-staged DEM via GEO_DEM_SOURCE.
    monkeypatch.setitem(sys.modules, "seamless_3dep", None)


def write_dem_geotiff(
    path,
    *,
    west,
    south,
    east,
    north,
    width=120,
    height=60,
    crs_epsg=4326,
    nodata=-9999.0,
    elevation=None,
):
    """Write a tiny single-band float32 DEM GeoTIFF for slope tests (no network).

    rasterio/numpy are imported INSIDE so importing conftest never requires them; callers
    that exercise the raster path guard themselves with `pytest.importorskip("rasterio")`.
    `elevation(rows, cols)` may return a custom HxW array; the default is a smooth tilted
    plane plus a bump so slope is non-zero and varies across the grid.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    if elevation is not None:
        data = np.asarray(elevation, dtype="float32")
        height, width = data.shape
    else:
        ys, xs = np.mgrid[0:height, 0:width].astype("float32")
        bump = 80.0 * np.exp(-(((xs - width * 0.6) / (width * 0.15)) ** 2)
                             - (((ys - height * 0.4) / (height * 0.2)) ** 2))
        data = (100.0 + 1.5 * xs + 0.8 * ys + bump).astype("float32")

    transform = from_bounds(west, south, east, north, width, height)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(int(crs_epsg)), transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


def make_settings(tmp_path, keep=3):
    return Settings(
        data_dir=tmp_path,
        keep_releases=keep,
        log_level="INFO",
        nrel_api_key="",
        duckdb_threads=2,
    )


@pytest.fixture
def mem_con():
    """An in-memory DuckDB connection with spatial loaded (closed after the test)."""
    pytest.importorskip("duckdb")
    from pipeline import db

    con = db.connect(":memory:", threads=2)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture
def ctx_factory(tmp_path, mem_con):
    """Build a FetchContext sharing one in-memory con and `tmp_path` as the work dir."""

    def make(**settings_kw):
        cfg = make_settings(tmp_path, **settings_kw)
        return FetchContext(work_dir=tmp_path, con=mem_con, settings=cfg, logger=get_logger("test"))

    return make
