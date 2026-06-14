"""End-to-end: the real harness discovers GEO-3 + GEO-4 and builds a valid artifact.

This is the capstone for GEO-3/GEO-4 — it runs `harness.run` with real fetcher discovery
(no stubbing), fed offline via pre-staged source fixtures, and asserts the committed
artifact, manifest, intermediates, and tippecanoe input.
"""

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


def test_full_build_with_county_and_parcels(tmp_path, monkeypatch):
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    monkeypatch.setenv(config.PARCELS_SOURCE_ENV, str(FIXTURES / "parcels_sample.geojson"))

    cfg = make_settings(tmp_path)
    out = harness.run(cfg, build_id="20260101T000000_000100Z")
    assert out == cfg.current_artifact_path and out.exists()

    con = duckdb.connect(str(out), read_only=True)
    try:
        con.execute("LOAD spatial;")
        assert con.execute("SELECT count(*) FROM county_boundary").fetchone()[0] == 1
        assert con.execute("SELECT geoid FROM county_boundary").fetchone()[0] == "06029"
        assert con.execute("SELECT count(*) FROM parcels").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM parcels WHERE NOT ST_IsValid(geom)").fetchone()[0] == 0
    finally:
        con.close()

    release = cfg.current_link.resolve()
    manifest = json.loads((release / config.MANIFEST_NAME).read_text())
    layers = {layer["name"]: layer for layer in manifest["layers"]}
    assert set(layers) == {"county_boundary", "parcels"}
    assert [layer["name"] for layer in manifest["layers"]] == ["county_boundary", "parcels"]  # run order
    assert layers["parcels"]["features"] == 4

    for artifact in ("county_boundary.parquet", "parcels.parquet", "parcels.geojson", config.SUCCESS_MARKER):
        assert (release / artifact).exists(), artifact
