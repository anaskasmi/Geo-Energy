"""GEO-5 zoning fetchers: code normalization, zoning_rules.csv emission, GeoParquet, and the
general-plan / specific-plans companion layers."""

import csv

import pytest

from pipeline import config
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.zoning import GeneralPlanFetcher, SpecificPlansFetcher, ZoningFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_zoning_normalizes_codes_emits_rules_and_parquet(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.ZONING_SOURCE_ENV, str(FIXTURES / "zoning_sample.geojson"))
    ctx = ctx_factory()
    result = ZoningFetcher().fetch(ctx)

    assert result.table == "zoning" and result.feature_count == 6
    assert result.extra["code_field"] == "Zn_Cd1"

    # Normalization: E(20)* -> E (raw preserved); empty -> OTHER.
    raw_to_norm = dict(ctx.con.execute("SELECT zone_code_raw, zone_code FROM zoning").fetchall())
    assert raw_to_norm["E(20)*"] == "E"
    assert raw_to_norm["NR(40)"] == "NR"
    assert raw_to_norm[""] == "OTHER"
    distinct = {r[0] for r in ctx.con.execute("SELECT DISTINCT zone_code FROM zoning").fetchall()}
    assert distinct == {"A", "M-1", "E", "NR", "R-1", "OTHER"}

    # Every district present is covered by the curated rules (no gaps to default).
    assert result.extra["rule_gaps"] == 0
    assert result.extra["distinct_codes"] == 6

    # zoning_rules.csv written: 6 codes x 4 use cases, valid permissions, known facts.
    rules_csv = result.extra["rules_csv"]
    rows = list(csv.DictReader(open(rules_csv)))
    assert len(rows) == 6 * len(config.ZONING_USE_CASES)
    assert {r["permission"] for r in rows} <= set(config.ZONING_PERMISSIONS)
    perms = {(r["zone_code"], r["use_case"]): r["permission"] for r in rows}
    assert perms[("M-1", "data_center")] == "by_right"
    assert perms[("E", "solar")] == "prohibited"
    assert perms[("A", "solar")] == "conditional"

    # Geometry valid + GeoParquet intermediate present.
    assert ctx.con.execute("SELECT count(*) FROM zoning WHERE NOT ST_IsValid(geom)").fetchone()[0] == 0
    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_zone_code_normalization_edge_cases(ctx_factory, monkeypatch):
    # Exercise the rtrim('*') (bare trailing star), space-delimited suffix, and lowercase
    # paths of _zone_code_sql that the E(20) fixture (stops at '(') does not reach.
    monkeypatch.setenv(config.ZONING_SOURCE_ENV, str(FIXTURES / "zoning_normalize.geojson"))
    ctx = ctx_factory()
    ZoningFetcher().fetch(ctx)
    norm = dict(ctx.con.execute("SELECT zone_code_raw, zone_code FROM zoning").fetchall())
    assert norm["A*"] == "A"           # trailing '*' stripped by rtrim
    assert norm["MS 2 1/2"] == "MS"    # space-delimited suffix dropped
    assert norm["m-1"] == "M-1"        # uppercased


def test_zoning_flags_gap_for_unmapped_code(ctx_factory, monkeypatch, tmp_path):
    # A district code with no curated rule -> filled with default, gap reported (never silent).
    src = tmp_path / "z.geojson"
    src.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature",'
        '"properties":{"Zn_Cd1":"ZZ"},'
        '"geometry":{"type":"Polygon","coordinates":[[[-119.5,35.2],[-119.4,35.2],[-119.4,35.3],[-119.5,35.3],[-119.5,35.2]]]}}]}'
    )
    monkeypatch.setenv(config.ZONING_SOURCE_ENV, str(src))
    ctx = ctx_factory()
    result = ZoningFetcher().fetch(ctx)
    assert result.extra["rule_gaps"] == len(config.ZONING_USE_CASES)  # ZZ uncovered for all uses
    rows = {(r["zone_code"], r["use_case"]): r for r in csv.DictReader(open(result.extra["rules_csv"]))}
    assert rows[("ZZ", "solar")]["permission"] == config.ZONING_DEFAULT_PERMISSION
    assert "DEFAULT" in rows[("ZZ", "solar")]["basis"]


def test_general_plan_builds(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.GENERAL_PLAN_SOURCE_ENV, str(FIXTURES / "general_plan_sample.geojson"))
    ctx = ctx_factory()
    result = GeneralPlanFetcher().fetch(ctx)
    assert result.table == "general_plan" and result.feature_count == 2
    descs = {r[0] for r in ctx.con.execute("SELECT lu_desc FROM general_plan").fetchall()}
    assert descs == {"Intensive Agriculture", "Resource"}
    assert result.parquet_path.exists()


def test_specific_plans_builds(ctx_factory, monkeypatch):
    monkeypatch.setenv(config.SPECIFIC_PLANS_SOURCE_ENV, str(FIXTURES / "specific_plans_sample.geojson"))
    ctx = ctx_factory()
    result = SpecificPlansFetcher().fetch(ctx)
    assert result.table == "specific_plans" and result.feature_count == 2
    names = {r[0] for r in ctx.con.execute("SELECT sp_name FROM specific_plans").fetchall()}
    assert names == {"Tejon Ranch", "Stallion Springs"}
    assert result.parquet_path.exists()


def test_zoning_no_source_configured_raises(ctx_factory, monkeypatch):
    monkeypatch.delenv(config.ZONING_SOURCE_ENV, raising=False)
    monkeypatch.setenv(config.ZONING_URL_ENV, "")  # blank beats the live default
    with pytest.raises(SourceError):
        ZoningFetcher().fetch(ctx_factory())
