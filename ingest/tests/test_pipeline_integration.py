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
from tests.conftest import FIXTURES, make_settings

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(autouse=True)
def _clean_registry():
    base.clear_registry()
    yield
    base.clear_registry()


def _stage_all_sources(monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_sample.geojson"))
    monkeypatch.setenv(config.TRANSMISSION_SOURCE_ENV, str(FIXTURES / "transmission_sample.geojson"))
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(FIXTURES / "substations_sample.geojson"))
    monkeypatch.setenv(config.ZONING_SOURCE_ENV, str(FIXTURES / "zoning_sample.geojson"))
    monkeypatch.setenv(config.GENERAL_PLAN_SOURCE_ENV, str(FIXTURES / "general_plan_sample.geojson"))
    monkeypatch.setenv(config.SPECIFIC_PLANS_SOURCE_ENV, str(FIXTURES / "specific_plans_sample.geojson"))


def test_full_build_with_all_layers(tmp_path, monkeypatch):
    _stage_all_sources(monkeypatch)

    cfg = make_settings(tmp_path)
    out = harness.run(cfg, build_id="20260101T000000_000100Z")
    assert out == cfg.current_artifact_path and out.exists()

    con = duckdb.connect(str(out), read_only=True)
    try:
        con.execute("LOAD spatial;")
        counts = {
            "county_boundary": 1, "parcels": 4, "transmission_lines": 4,
            "substations": 3, "zoning": 6, "general_plan": 2, "specific_plans": 2,
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
    finally:
        con.close()

    release = cfg.current_link.resolve()
    manifest = json.loads((release / config.MANIFEST_NAME).read_text())
    layer_order = [layer["name"] for layer in manifest["layers"]]
    assert layer_order == [
        "county_boundary", "parcels", "transmission_lines", "substations",
        "zoning", "general_plan", "specific_plans",
    ]  # run_order

    # Intermediates + tippecanoe input + zoning_rules.csv + success marker all present.
    for artifact in (
        "county_boundary.parquet", "parcels.parquet", "parcels.geojson",
        "transmission_lines.parquet", "substations.parquet",
        "zoning.parquet", "general_plan.parquet", "specific_plans.parquet",
        config.ZONING_RULES_CSV, config.SUCCESS_MARKER,
    ):
        assert (release / artifact).exists(), artifact

    # zoning_rules.csv is well-formed and covers the districts in the data.
    rules_rows = list(csv.DictReader(open(release / config.ZONING_RULES_CSV)))
    assert len(rules_rows) == 6 * len(config.ZONING_USE_CASES)
    assert {r["permission"] for r in rules_rows} <= set(config.ZONING_PERMISSIONS)
