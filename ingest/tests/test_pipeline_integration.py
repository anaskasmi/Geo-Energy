"""End-to-end: the real harness discovers every fetcher (GEO-3/4/5/6) and builds a valid
artifact.

Runs `harness.run` with real fetcher discovery (no stubbing), fed offline via pre-staged
source fixtures, and asserts the committed artifact, manifest, intermediates, and tippecanoe
input. Exercises the full fetch order and the county-clip dependency (transmission/
substations clip to the county_boundary built first).
"""

import csv
import json

import pytest

from pipeline import config, harness
from pipeline.fetchers import base
from tests.conftest import FIXTURES, make_settings, write_dem_geotiff

duckdb = pytest.importorskip("duckdb")
# The slope fetcher (GEO-9) is auto-discovered and runs in the full build, so the e2e test
# needs the raster stack (rasterio/numpy). It is a declared ingest dependency; skip cleanly
# where it is absent rather than hard-error on the whole build.
pytest.importorskip("rasterio")


@pytest.fixture(autouse=True)
def _clean_registry():
    base.clear_registry()
    yield
    base.clear_registry()


def _stage_all_sources(monkeypatch, tmp_path):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_sample.geojson"))
    monkeypatch.setenv(config.TRANSMISSION_SOURCE_ENV, str(FIXTURES / "transmission_sample.geojson"))
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(FIXTURES / "substations_sample.geojson"))
    monkeypatch.setenv(config.FLOOD_SOURCE_ENV, str(FIXTURES / "flood_sample.geojson"))
    monkeypatch.setenv(config.ZONING_SOURCE_ENV, str(FIXTURES / "zoning_sample.geojson"))
    monkeypatch.setenv(config.GENERAL_PLAN_SOURCE_ENV, str(FIXTURES / "general_plan_sample.geojson"))
    monkeypatch.setenv(config.SPECIFIC_PLANS_SOURCE_ENV, str(FIXTURES / "specific_plans_sample.geojson"))
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_sample.csv"))
    monkeypatch.setenv(config.NREL_GHI_SOURCE_ENV, str(FIXTURES / "ghi_grid_sample.csv"))
    # Supplemental optional layers (GEO-11): EIA generators + a protected-area exclusion.
    monkeypatch.setenv(config.EIA860_SOURCE_ENV, str(FIXTURES / "eia860_sample.csv"))
    monkeypatch.setenv(
        config.EXCLUSION_LAYERS[0][1], str(FIXTURES / "exclusion_protected_sample.geojson")
    )
    # Slope (GEO-9): a tiny DEM covering the county fixture, computed on a coarse grid so the
    # e2e build stays fast (no checked-in binary; *.tif is gitignored).
    dem = write_dem_geotiff(
        tmp_path / "dem_source.tif", west=-120.1, south=34.8, east=-117.9, north=35.7,
        width=150, height=75,
    )
    monkeypatch.setenv(config.DEM_SOURCE_ENV, str(dem))
    monkeypatch.setenv(config.DEM_RES_ENV, "2000")


def test_full_build_with_all_layers(tmp_path, monkeypatch):
    _stage_all_sources(monkeypatch, tmp_path)

    cfg = make_settings(tmp_path)
    out = harness.run(cfg, build_id="20260101T000000_000100Z")
    assert out == cfg.current_artifact_path and out.exists()

    con = duckdb.connect(str(out), read_only=True)
    try:
        con.execute("LOAD spatial;")
        counts = {
            "county_boundary": 1, "parcels": 4, "transmission_lines": 4,
            "substations": 3, "flood_sfha": 4, "zoning": 6, "general_plan": 2,
            "specific_plans": 2, "caiso_queue": 5, "poi_competition": 1,
            "caiso_queue_summary": 8, "slope_raster": 1, "ghi_grid": 4,
            "eia_generators": 3, "exclusions": 2,
        }
        for table, expected in counts.items():
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == expected, table
        assert con.execute("SELECT geoid FROM county_boundary").fetchone()[0] == "06029"
        assert con.execute("SELECT count(*) FROM parcels WHERE NOT ST_IsValid(geom)").fetchone()[0] == 0
        # Clipped layers landed inside the county; voltage sentinels nulled.
        assert con.execute("SELECT count(*) FROM substations WHERE max_voltage_kv = 0").fetchone()[0] == 0
        assert con.execute(
            "SELECT count(*) FROM transmission_lines t, county_boundary c "
            "WHERE NOT ST_Intersects(t.geom, c.geom)"
        ).fetchone()[0] == 0
        # Flood: every retained polygon is SFHA and inside the county.
        assert con.execute("SELECT count(*) FROM flood_sfha WHERE NOT sfha_flag").fetchone()[0] == 0
        assert con.execute(
            "SELECT count(*) FROM flood_sfha f, county_boundary c WHERE NOT ST_Intersects(f.geom, c.geom)"
        ).fetchone()[0] == 0
        # CAISO: geolocated POIs inherit substation voltage; county summary totals present.
        assert con.execute(
            "SELECT poi_voltage_kv FROM caiso_queue WHERE queue_id = 'Q1'"
        ).fetchone()[0] == 230
        assert con.execute(
            "SELECT total_mw FROM caiso_queue_summary WHERE category = 'total'"
        ).fetchone()[0] == 725.0
    finally:
        con.close()

    release = cfg.current_link.resolve()
    manifest = json.loads((release / config.MANIFEST_NAME).read_text())
    layer_order = [layer["name"] for layer in manifest["layers"]]
    assert layer_order == [
        "county_boundary", "parcels", "transmission_lines", "substations", "flood_sfha",
        "zoning", "general_plan", "specific_plans",
        "caiso_queue", "poi_competition", "caiso_queue_summary",
        "slope", "ghi_grid", "eia_generators", "exclusions",
    ]  # run_order

    # Intermediates + tippecanoe input + zoning_rules.csv + success marker all present.
    for artifact in (
        "county_boundary.parquet", "parcels.parquet", "parcels.geojson",
        "transmission_lines.parquet", "substations.parquet", "flood_sfha.parquet",
        "zoning.parquet", "general_plan.parquet", "specific_plans.parquet",
        "caiso_queue.parquet", "poi_competition.parquet", "caiso_queue_summary.parquet",
        config.SLOPE_SCREENING_TIF,  # slope.tif raster sidecar (GEO-9)
        config.GHI_GRID_PARQUET,     # ghi_grid.parquet (GEO-10)
        config.EIA_GENERATORS_PARQUET, config.EXCLUSIONS_PARQUET,  # GEO-11 optional layers
        config.ZONING_RULES_CSV, config.SUCCESS_MARKER,
    ):
        assert (release / artifact).exists(), artifact

    # zoning_rules.csv is well-formed and covers the districts in the data.
    rules_rows = list(csv.DictReader(open(release / config.ZONING_RULES_CSV)))
    assert len(rules_rows) == 6 * len(config.ZONING_USE_CASES)
    assert {r["permission"] for r in rules_rows} <= set(config.ZONING_PERMISSIONS)
