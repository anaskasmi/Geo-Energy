"""GEO-7 CAISO interconnection queue: Kern county scoping, POI geolocation by name-match to
HIFLD substations (with voltage inheritance), poi_competition, and the county-level summary."""

import sys
import types

import pytest

from pipeline import config
from pipeline.fetchers.base import clear_registry
from pipeline.fetchers.caiso import (
    CaisoQueueFetcher,
    CaisoQueueSummaryFetcher,
    PoiCompetitionFetcher,
)
from pipeline.fetchers.county_boundary import CountyBoundaryFetcher
from pipeline.fetchers.hifld import SubstationsFetcher
from pipeline.sources import SourceError
from tests.conftest import FIXTURES

pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _with_substations(ctx, monkeypatch, fixture="substations_sample.geojson"):
    """Build county_boundary then substations (the geolocation target) into ctx.con."""
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    CountyBoundaryFetcher().fetch(ctx)
    monkeypatch.setenv(config.SUBSTATIONS_SOURCE_ENV, str(FIXTURES / fixture))
    SubstationsFetcher().fetch(ctx)
    return ctx


def test_caiso_queue_scopes_to_kern_geolocates_and_inherits_voltage(ctx_factory, monkeypatch):
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_sample.csv"))
    result = CaisoQueueFetcher().fetch(ctx)

    # Q6 (Los Angeles) is filtered out; Q1-Q5 (Kern) remain.
    assert result.table == "caiso_queue" and result.feature_count == 5
    ids = {r[0] for r in ctx.con.execute("SELECT queue_id FROM caiso_queue").fetchall()}
    assert ids == {"Q1", "Q2", "Q3", "Q4", "Q5"}
    assert result.extra["poi_field"] == "Interconnection Location"

    rows = {
        r[0]: r for r in ctx.con.execute(
            "SELECT queue_id, matched_substation_id, poi_voltage_kv, is_active, capacity_mw, "
            "generation_type FROM caiso_queue"
        ).fetchall()
    }
    # POI name-matching: "Pinetree Substation" and "PINETREE 230 kV Sub" both match PINETREE
    # (digits/KV/SUB stripped); "Sentinel Sub" -> SENTINEL; "Monolith" -> MONOLITH;
    # "Nowhere Junction" -> no substation -> unmatched.
    pinetree = ctx.con.execute("SELECT id FROM substations WHERE name = 'PINETREE'").fetchone()[0]
    assert rows["Q1"][1] == pinetree and rows["Q2"][1] == pinetree
    assert rows["Q5"][1] is None
    # Voltage inherited from the matched substation (the queue has none): PINETREE=230 kV.
    assert rows["Q1"][2] == 230 and rows["Q2"][2] == 230
    # MONOLITH/SENTINEL have null voltage -> inherited null; unmatched -> null.
    assert rows["Q3"][2] is None and rows["Q4"][2] is None and rows["Q5"][2] is None
    # Active flag: Active -> true; Withdrawn/Completed -> false.
    assert rows["Q1"][3] is True and rows["Q5"][3] is True
    assert rows["Q3"][3] is False and rows["Q4"][3] is False

    assert result.extra["geolocated"] == 4 and result.extra["active"] == 3

    # Matched rows carry the substation point geometry; unmatched are null.
    assert ctx.con.execute(
        "SELECT count(*) FROM caiso_queue WHERE matched_substation_id IS NOT NULL AND geom IS NULL"
    ).fetchone()[0] == 0
    assert ctx.con.execute(
        "SELECT ST_GeometryType(geom) FROM caiso_queue WHERE queue_id = 'Q1'"
    ).fetchone()[0] == "POINT"

    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_poi_competition_aggregates_active_projects(ctx_factory, monkeypatch):
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_sample.csv"))
    CaisoQueueFetcher().fetch(ctx)
    result = PoiCompetitionFetcher().fetch(ctx)

    # Only PINETREE has *active* geolocated projects (Q1+Q2); MONOLITH/SENTINEL were matched
    # only by withdrawn/completed projects, so they are not POIs in the competition table.
    assert result.table == "poi_competition" and result.feature_count == 1
    row = ctx.con.execute(
        "SELECT poi_name, n_at_poi, mw_at_poi, n_within_radius, mw_within_radius, radius_m "
        "FROM poi_competition"
    ).fetchone()
    assert row[0] == "PINETREE"
    assert row[1] == 2 and row[2] == 150.0          # 2 projects, 100+50 MW at the POI
    assert row[3] == 2 and row[4] == 150.0          # nearest other POIs are >10 km away
    assert row[5] == config.POI_COMPETITION_RADIUS_M

    assert result.parquet_path.exists()
    meta = pq.read_metadata(result.parquet_path).metadata
    assert meta is not None and b"geo" in meta


def test_caiso_summary_is_county_context_only(ctx_factory, monkeypatch):
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_sample.csv"))
    CaisoQueueFetcher().fetch(ctx)
    result = CaisoQueueSummaryFetcher().fetch(ctx)
    assert result.table == "caiso_queue_summary"

    total = ctx.con.execute(
        "SELECT n_projects, total_mw, active_n_projects, active_total_mw "
        "FROM caiso_queue_summary WHERE category = 'total'"
    ).fetchone()
    assert total == (5, 725.0, 3, 225.0)  # 5 Kern projects, 725 MW; 3 active, 225 MW

    by_type = {
        r[0]: (r[1], r[2]) for r in ctx.con.execute(
            "SELECT key, n_projects, total_mw FROM caiso_queue_summary WHERE category = 'by_type'"
        ).fetchall()
    }
    assert by_type["Solar"] == (2, 175.0)   # Q1 + Q5
    assert by_type["Storage"] == (1, 200.0)  # Q3
    by_status = {
        r[0]: r[1] for r in ctx.con.execute(
            "SELECT key, n_projects FROM caiso_queue_summary WHERE category = 'by_status'"
        ).fetchall()
    }
    assert by_status["Active"] == 3 and by_status["Withdrawn"] == 1 and by_status["Completed"] == 1


def test_poi_competition_radius_distinguishes_from_at_poi(ctx_factory, monkeypatch):
    # Three substations at metric-separable points: ALPHA, BRAVO ~4.6 km from ALPHA (within the
    # 10 km radius), CHARLIE ~18 km away (outside). One active project on each. This proves the
    # ST_Distance radius join + EPSG:26911 transform: at-POI counts stay 1 while within-radius
    # picks up the nearby POI's project (and excludes the far one).
    ctx = _with_substations(ctx_factory(), monkeypatch, fixture="caiso_substations_geo.geojson")
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_competition.csv"))
    CaisoQueueFetcher().fetch(ctx)
    PoiCompetitionFetcher().fetch(ctx)

    rows = {
        r[0]: r[1:] for r in ctx.con.execute(
            "SELECT poi_name, n_at_poi, mw_at_poi, n_within_radius, mw_within_radius "
            "FROM poi_competition"
        ).fetchall()
    }
    # ALPHA: alone at its point (1/100), but BRAVO's project is within 10 km (2/150).
    assert rows["ALPHA"] == (1, 100.0, 2, 150.0)
    # BRAVO: symmetric — ALPHA within range, CHARLIE not.
    assert rows["BRAVO"] == (1, 50.0, 2, 150.0)
    # CHARLIE: isolated (>10 km from both) — radius equals at-POI.
    assert rows["CHARLIE"] == (1, 80.0, 1, 80.0)


def test_caiso_name_match_requires_digit_stripping(ctx_factory, monkeypatch):
    # POI "Whirlwind230" (voltage fused into the name, no separator) only resolves to the
    # WHIRLWIND substation because normalization strips the digits; without digit-stripping it
    # is a single token "WHIRLWIND230" that neither equals nor whole-word-contains "WHIRLWIND".
    ctx = _with_substations(ctx_factory(), monkeypatch, fixture="caiso_substations_geo.geojson")
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_digitname.csv"))
    CaisoQueueFetcher().fetch(ctx)
    matched = ctx.con.execute(
        "SELECT s.name FROM caiso_queue q JOIN substations s ON s.id = q.matched_substation_id"
    ).fetchone()
    assert matched == ("WHIRLWIND",)


def test_caiso_live_path_materializes_gridstatus_to_csv(ctx_factory, monkeypatch, tmp_path):
    # The live path (no override) lazy-imports gridstatus, calls get_interconnection_queue(),
    # and materializes the DataFrame to CSV for the shared DuckDB read path. Stub gridstatus
    # (the autouse fixture sets it to None) so this stays offline yet exercises the real branch.
    pd = pytest.importorskip("pandas")
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.CAISO_QUEUE_SOURCE_ENV, raising=False)

    fake = types.ModuleType("gridstatus")

    class _CAISO:
        def get_interconnection_queue(self):
            return pd.DataFrame([{
                "Queue ID": "G1", "Project Name": "Grid Solar", "Generation Type": "Solar",
                "Capacity (MW)": 42.0, "County": "Kern", "State": "CA", "Status": "Active",
                "Transmission Owner": "PG&E", "Interconnection Location": "Pinetree Substation",
            }])

    fake.CAISO = _CAISO
    monkeypatch.setitem(sys.modules, "gridstatus", fake)

    result = CaisoQueueFetcher().fetch(ctx)
    assert result.source == "gridstatus.CAISO().get_interconnection_queue()"
    assert (tmp_path / "caiso_queue_source.csv").exists()  # df materialized to CSV
    row = ctx.con.execute(
        "SELECT project_name, capacity_mw, poi_voltage_kv FROM caiso_queue"
    ).fetchone()
    pinetree = ctx.con.execute("SELECT id FROM substations WHERE name = 'PINETREE'").fetchone()[0]
    assert row[0] == "Grid Solar" and row[1] == 42.0 and row[2] == 230
    assert ctx.con.execute(
        "SELECT matched_substation_id FROM caiso_queue"
    ).fetchone()[0] == pinetree


def test_caiso_resolves_raw_gridstatus_columns(ctx_factory, monkeypatch):
    # A CSV using the *raw* (un-standardized) gridstatus column names still resolves via the
    # candidate lists ("Station or Transmission Line" -> POI, "Net MWs to Grid" -> MW, etc.).
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_rawcols.csv"))
    result = CaisoQueueFetcher().fetch(ctx)
    assert result.feature_count == 1
    assert result.extra["poi_field"] == "Station or Transmission Line"
    row = ctx.con.execute(
        "SELECT capacity_mw, matched_substation_id, poi_voltage_kv FROM caiso_queue"
    ).fetchone()
    pinetree = ctx.con.execute("SELECT id FROM substations WHERE name = 'PINETREE'").fetchone()[0]
    assert row[0] == 120.0 and row[1] == pinetree and row[2] == 230


def test_caiso_source_voltage_overrides_substation(ctx_factory, monkeypatch):
    # When the source carries its own POI voltage, it wins over the substation's voltage.
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_voltage.csv"))
    CaisoQueueFetcher().fetch(ctx)
    volt = ctx.con.execute("SELECT poi_voltage_kv FROM caiso_queue").fetchone()[0]
    assert volt == 66  # source "Voltage (kV)" = 66 beats PINETREE's 230


def test_caiso_county_scoping_matches_kern_as_whole_word(ctx_factory, monkeypatch, tmp_path):
    # County scoping must catch "Kern County" and multi-county strings like "Los Angeles, Kern"
    # (exact equality silently dropped them), while excluding non-Kern counties.
    ctx = _with_substations(ctx_factory(), monkeypatch)
    src = tmp_path / "counties.csv"
    src.write_text(
        "Queue ID,Project Name,Generation Type,Capacity (MW),County,State,Status,"
        "Transmission Owner,Interconnection Location\n"
        "K1,Kern Plain,Solar,10,Kern,CA,Active,PG&E,Pinetree Substation\n"
        "K2,Kern Suffixed,Solar,10,Kern County,CA,Active,PG&E,Pinetree Substation\n"
        'K3,Kern Multi,Solar,10,"Los Angeles, Kern",CA,Active,PG&E,Pinetree Substation\n'
        "X1,LA Only,Solar,10,Los Angeles,CA,Active,SCE,Vincent\n"
        "X2,Inyo Only,Solar,10,Inyo,CA,Active,SCE,Bishop\n"
    )
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(src))
    result = CaisoQueueFetcher().fetch(ctx)
    kept = {r[0] for r in ctx.con.execute("SELECT queue_id FROM caiso_queue").fetchall()}
    assert kept == {"K1", "K2", "K3"} and result.feature_count == 3


def test_caiso_requires_substations(ctx_factory, monkeypatch):
    # Without substations (GEO-6) built first, geolocation can't run -> SourceError.
    monkeypatch.setenv(config.COUNTY_SOURCE_ENV, str(FIXTURES / "kern_county.geojson"))
    ctx = ctx_factory()
    CountyBoundaryFetcher().fetch(ctx)  # county only; no substations
    monkeypatch.setenv(config.CAISO_QUEUE_SOURCE_ENV, str(FIXTURES / "caiso_queue_sample.csv"))
    with pytest.raises(SourceError):
        CaisoQueueFetcher().fetch(ctx)


def test_caiso_no_source_configured_raises(ctx_factory, monkeypatch):
    ctx = _with_substations(ctx_factory(), monkeypatch)
    monkeypatch.delenv(config.CAISO_QUEUE_SOURCE_ENV, raising=False)
    # No override -> the lazy `import gridstatus` is the only fallback. The autouse hermetic
    # fixture neutralizes that import (sys.modules["gridstatus"]=None), so this raises
    # SourceError deterministically and never makes a live call -- regardless of whether
    # gridstatus is actually installed in the image (it is, via requirements.txt).
    with pytest.raises(SourceError):
        CaisoQueueFetcher().fetch(ctx)
