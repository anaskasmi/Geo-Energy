"""In-process agent tools (GEO-20): resolve_area, score_parcels, explain_parcel, grid_context.

Verified against the conftest scored artifact (7 parcels, known Stage-A outcomes). These are the
exact callables the Gemini/Pydantic-AI agent (GEO-21) wraps as tools — so the tests double as the
contract that the agent never needs geometry/scores of its own.
"""

from __future__ import annotations

import pytest

from app import agent_tools as at
from app import db, scoring
from tests.conftest import SCORED_POLYGON


@pytest.fixture
def con(scored_data_dir):
    c = db.connect(db.artifact_path(), read_only=True)
    try:
        yield c.cursor()
    finally:
        c.close()


@pytest.fixture
def zoning_rules(scored_data_dir):
    return scoring.load_zoning_rules(db.zoning_rules_path())


@pytest.fixture(autouse=True)
def _clear_store():
    at.area_store.clear()
    yield
    at.area_store.clear()


# --- resolve_area -----------------------------------------------------------------------------
def test_resolve_area_place_name():
    out = at.resolve_area("Mojave")
    assert out["area_ref"].startswith("area_")
    assert out["source"] == "gazetteer"
    assert out["approximate"] is True
    assert out["geometry"]["type"] == "Polygon"
    # centre near Mojave (~ -118.17, 35.05)
    assert -118.3 < out["centroid"][0] < -118.0
    assert 34.9 < out["centroid"][1] < 35.2


def test_resolve_area_substring_and_alias():
    assert at.resolve_area("near Mojave")["source"] == "gazetteer"
    assert at.resolve_area("Cal City")["label"].lower().startswith("california city")


def test_resolve_area_bbox_numbers():
    out = at.resolve_area("-119.05, 35.28, -118.93, 35.33")
    assert out["source"] == "bbox"
    assert out["bbox"] == [-119.05, 35.28, -118.93, 35.33]
    assert out["geometry"]["coordinates"][0][0] == [-119.05, 35.28]


def test_resolve_area_point_swaps_latlng():
    # "lat, lng" order is detected and swapped to GeoJSON lng,lat.
    out = at.resolve_area("35.30, -119.00")
    assert out["source"] == "point"
    assert -119.07 < out["centroid"][0] < -118.93
    assert 35.24 < out["centroid"][1] < 35.36


def test_resolve_area_county_fallback_without_table(con):
    # The conftest artifact has no county_boundary table -> falls back to the county bbox.
    out = at.resolve_area("Kern County", cur=con)
    assert out["source"] == "county_bbox"
    assert out["approximate"] is True
    assert out["bbox"] == [round(v, 6) for v in at.KERN_COUNTY_BBOX]


def test_resolve_area_unresolvable_raises():
    with pytest.raises(at.ToolError):
        at.resolve_area("Atlantis")
    with pytest.raises(at.ToolError):
        at.resolve_area("   ")


def test_resolve_area_ref_is_deterministic():
    assert at.resolve_area("Mojave")["area_ref"] == at.resolve_area("Mojave")["area_ref"]


# --- score_parcels ----------------------------------------------------------------------------
def _ids(fc: dict) -> set[int]:
    return {f["properties"]["id"] for f in fc["features"]}


def test_score_parcels_via_geometry(con, zoning_rules):
    fc = at.score_parcels(con, geometry=SCORED_POLYGON, use_case="utility_solar", zoning_rules=zoning_rules)
    assert fc["type"] == "FeatureCollection"
    # Survivors for solar: P3 slope>15, P4 acres<20, P5 sfha, P6 zoning E prohibited are excluded.
    assert _ids(fc) == {1, 2, 7}
    scores = [f["properties"]["score"] for f in fc["features"]]
    assert scores == sorted(scores, reverse=True)  # ranked desc
    assert [f["properties"]["rank"] for f in fc["features"]] == [1, 2, 3]


def test_score_parcels_via_area_ref(con, zoning_rules):
    ref = at.resolve_area("-119.05,35.28,-118.93,35.33")["area_ref"]
    fc = at.score_parcels(con, area_ref=ref, use_case="utility_solar", zoning_rules=zoning_rules)
    assert _ids(fc) == {1, 2, 7}


def test_score_parcels_area_ref_place_fallback_empty(con, zoning_rules):
    # A place name passed where an area_ref is expected is resolved; Mojave doesn't cover the
    # synthetic parcels, so the result is an empty (but valid) FeatureCollection — not an error.
    fc = at.score_parcels(con, area_ref="Mojave", use_case="data_center", zoning_rules=zoning_rules)
    assert fc["meta"]["count"] == 0


def test_score_parcels_data_center_use_case(con, zoning_rules):
    fc = at.score_parcels(con, geometry=SCORED_POLYGON, use_case="data_center", zoning_rules=zoning_rules)
    assert fc["meta"]["use_case"] == "data_center"
    assert _ids(fc)  # at least one survivor


def test_score_parcels_threshold_override(con, zoning_rules):
    # Drop min_acres to 0 and allow steeper slope: still excludes sfha(P5) & zoning(P6); P3 slope20
    # now allowed, P4 acres10 now allowed.
    fc = at.score_parcels(
        con, geometry=SCORED_POLYGON, use_case="utility_solar",
        min_acres=0, max_slope_pct=25, zoning_rules=zoning_rules,
    )
    assert _ids(fc) == {1, 2, 3, 4, 7}


def test_score_parcels_bad_use_case_raises(con):
    with pytest.raises(at.ToolError):
        at.score_parcels(con, geometry=SCORED_POLYGON, use_case="nope")


def test_score_parcels_unknown_area_ref_raises(con):
    with pytest.raises(at.ToolError):
        at.score_parcels(con, area_ref="area_deadbeefdeadbeef", use_case="utility_solar")


def test_score_parcels_requires_an_area(con):
    with pytest.raises(at.ToolError):
        at.score_parcels(con, use_case="utility_solar")


# --- check_affordability + affordability blend (GEO-41) ---------------------------------------
_AFFORD_OK = {
    "ok": True,
    "median_home_value_usd": 310600,
    "acs_vintage": "2023 ACS 5-year",
    "hpi_index": 324.07,
    "price_trend_yoy_pct": 4.6,
    "hpi_as_of": "2024",
    "sources": ["Census ACS5 B25077_001E", "FRED ATNHPIUS06029A"],
}


def test_check_affordability_returns_score(con, monkeypatch):
    monkeypatch.setattr(at.landvalue, "area_affordability", lambda **kw: dict(_AFFORD_OK))
    ref = at.resolve_area("-119.05,35.28,-118.93,35.33")["area_ref"]
    out = at.check_affordability(con, area_ref=ref)
    assert out["type"] == "Affordability"
    assert out["median_home_value_usd"] == 310600
    # median 310600 in [150k, 600k] -> aff = 1 - (310600-150000)/450000 ≈ 0.643
    assert out["affordability_score"] == pytest.approx(0.643, abs=0.01)
    assert out["affordability_band"] == "affordable"
    assert out["sources"]
    assert out["approximate"] is True


def test_check_affordability_unavailable_raises(con, monkeypatch):
    monkeypatch.setattr(
        at.landvalue, "area_affordability", lambda **kw: {"ok": False, "error": "unreachable"}
    )
    ref = at.resolve_area("Mojave")["area_ref"]
    with pytest.raises(at.ToolError):
        at.check_affordability(con, area_ref=ref)


def test_check_affordability_bad_area_ref_raises(con, monkeypatch):
    # A stale/unknown token is caught BEFORE any network attempt.
    monkeypatch.setattr(at.landvalue, "area_affordability", lambda **kw: dict(_AFFORD_OK))
    with pytest.raises(at.ToolError):
        at.check_affordability(con, area_ref="area_deadbeefdeadbeef")


def test_check_affordability_missing_median_unknown_band(con, monkeypatch):
    monkeypatch.setattr(
        at.landvalue,
        "area_affordability",
        lambda **kw: {"ok": True, "median_home_value_usd": None, "hpi_index": 324.07,
                      "sources": ["FRED ATNHPIUS06029A"]},
    )
    out = at.check_affordability(con, area_ref=at.resolve_area("Mojave")["area_ref"])
    assert out["affordability_score"] is None
    assert out["affordability_band"] == "unknown"


def test_score_parcels_affordability_blend_preserves_order(con, zoning_rules):
    base = at.score_parcels(con, geometry=SCORED_POLYGON, use_case="utility_solar", zoning_rules=zoning_rules)
    base_ids = [f["properties"]["id"] for f in base["features"]]
    base_scores = [f["properties"]["score"] for f in base["features"]]

    # affordability_score=1.0 (cheapest) lifts every score toward 100; order is preserved.
    blended = at.score_parcels(
        con, geometry=SCORED_POLYGON, use_case="utility_solar",
        zoning_rules=zoning_rules, affordability_score=1.0,
    )
    blend_ids = [f["properties"]["id"] for f in blended["features"]]
    blend_scores = [f["properties"]["score"] for f in blended["features"]]

    assert blend_ids == base_ids  # ranking unchanged (uniform affine blend)
    assert all(b >= a for a, b in zip(base_scores, blend_scores))
    assert blended["meta"]["affordability"] == {
        "applied": True, "affordability_score": 1.0, "weight": 0.12,
    }


def test_score_parcels_affordability_zero_pulls_down(con, zoning_rules):
    base = at.score_parcels(con, geometry=SCORED_POLYGON, use_case="utility_solar", zoning_rules=zoning_rules)
    blended = at.score_parcels(
        con, geometry=SCORED_POLYGON, use_case="utility_solar",
        zoning_rules=zoning_rules, affordability_score=0.0,
    )
    bs = [f["properties"]["score"] for f in base["features"]]
    zs = [f["properties"]["score"] for f in blended["features"]]
    assert all(z <= b for b, z in zip(bs, zs))  # most-expensive area can only reduce scores


def test_score_parcels_affordability_invalid_raises(con, zoning_rules):
    with pytest.raises(at.ToolError):
        at.score_parcels(
            con, geometry=SCORED_POLYGON, use_case="utility_solar",
            zoning_rules=zoning_rules, affordability_score=2.0,
        )


# --- focus_parcel + export_pdf (GEO-41 map control + PDF) -------------------------------------
def test_focus_parcel_returns_centroid(con):
    out = at.focus_parcel(con, parcel_id=1)
    assert out["type"] == "Focus"
    assert out["parcel_id"] == 1
    lng, lat = out["centroid"]
    # Parcel 1 centroid is near (-119.0375, 35.3025) in the conftest fixture.
    assert -119.06 < lng < -119.0 and 35.29 < lat < 35.31


def test_focus_parcel_not_found_raises(con):
    with pytest.raises(at.ToolError):
        at.focus_parcel(con, parcel_id=9999)


def test_focus_parcel_non_integer_raises(con):
    with pytest.raises(at.ToolError):
        at.focus_parcel(con, parcel_id="abc")


def test_export_pdf_parses_ids():
    out = at.export_pdf(parcel_ids="5, 12 ;7")
    assert out == {"type": "ExportPdf", "parcel_ids": [5, 12, 7]}


def test_export_pdf_empty_means_all():
    out = at.export_pdf(parcel_ids="")
    assert out == {"type": "ExportPdf", "parcel_ids": []}


def test_export_pdf_bad_id_raises():
    with pytest.raises(at.ToolError):
        at.export_pdf(parcel_ids="5,abc")


# --- explain_parcel ---------------------------------------------------------------------------
def test_explain_parcel_excluded(con, zoning_rules):
    out = at.explain_parcel(con, parcel_id=3, use_case="utility_solar", zoning_rules=zoning_rules)
    assert out["parcel_id"] == 3
    assert out["excluded"] is True
    assert out["exclusions"]["slope"] is True
    assert out["factors"]  # per-factor breakdown present


def test_explain_parcel_kept(con, zoning_rules):
    out = at.explain_parcel(con, parcel_id=1, use_case="utility_solar", zoning_rules=zoning_rules)
    assert out["excluded"] is False
    assert out["score"] is not None


def test_explain_parcel_not_found_raises(con):
    with pytest.raises(at.ToolError):
        at.explain_parcel(con, parcel_id=9999, use_case="utility_solar")


# --- grid_context -----------------------------------------------------------------------------
def test_grid_context(con):
    out = at.grid_context(con)
    assert out["county"].startswith("Kern County")
    assert out["total"]["n_projects"] == 42
    # by_type sorted by total_mw desc -> Solar (2500) before Battery (1500)
    assert [t["key"] for t in out["by_type"]] == ["Solar", "Battery"]


# --- provider-agnostic tool schemas (FLAT, Gemini-safe) ---------------------------------------
def test_tool_specs_present_and_named():
    names = {t["name"] for t in at.TOOL_SPECS}
    assert names == {
        "resolve_area", "score_parcels", "check_affordability", "explain_parcel", "grid_context",
        "focus_parcel", "export_pdf",
    }


def test_tool_specs_are_flat():
    """Every parameter is a scalar/enum — no nested objects/arrays (Gemini OpenAPI subset)."""
    for tool in at.TOOL_SPECS:
        for pname, schema in tool["parameters"]["properties"].items():
            assert schema["type"] in {"string", "number", "integer", "boolean"}, (tool["name"], pname)
            assert "items" not in schema and "properties" not in schema, (tool["name"], pname)


def test_score_parcels_schema_has_no_geometry():
    """The agent never receives coordinates: score_parcels takes area_ref, not a polygon."""
    score = next(t for t in at.TOOL_SPECS if t["name"] == "score_parcels")
    props = set(score["parameters"]["properties"])
    assert "area_ref" in props
    assert not props & {"geometry", "polygon", "coordinates", "geojson"}


# --- regression guards (from the GEO-19/20 adversarial review) --------------------------------
@pytest.mark.parametrize("txt", ["I-5 and CA-58", "0,0,1,1", "5, 50", "Atlantis"])
def test_resolve_area_rejects_out_of_region(txt):
    """Incidental numbers / non-Kern coords must NOT resolve to a confident off-region area —
    the numeric path is clamped to (padded) Kern County, so these fall through to a ToolError."""
    with pytest.raises(at.ToolError):
        at.resolve_area(txt)


def test_score_parcels_exclude_sfha_override(con, zoning_rules):
    """exclude_sfha=False re-admits parcel 5 (excluded ONLY by SFHA): survivors become {1,2,5,7}."""
    fc = at.score_parcels(
        con, geometry=SCORED_POLYGON, use_case="utility_solar",
        exclude_sfha=False, zoning_rules=zoning_rules,
    )
    assert _ids(fc) == {1, 2, 5, 7}


def test_score_parcels_rejects_bad_geometry(con):
    with pytest.raises(at.ToolError):
        at.score_parcels(con, geometry={"type": "Point", "coordinates": [-119.0, 35.3]}, use_case="utility_solar")
    with pytest.raises(at.ToolError):
        at.score_parcels(con, geometry={"type": "Polygon", "coordinates": []}, use_case="utility_solar")


def test_score_parcels_limit_offset_rank(con, zoning_rules):
    """The agent path threads offset into rank/meta the same way /api/score does."""
    fc = at.score_parcels(
        con, geometry=SCORED_POLYGON, use_case="utility_solar",
        zoning_rules=zoning_rules, limit=1, offset=1,
    )
    assert fc["meta"]["limit"] == 1
    assert fc["meta"]["offset"] == 1
    assert fc["meta"]["count"] == 1
    assert fc["features"][0]["properties"]["rank"] == 2  # second-ranked survivor


def test_score_parcels_non_integer_limit_raises(con):
    with pytest.raises(at.ToolError):
        at.score_parcels(con, geometry=SCORED_POLYGON, use_case="utility_solar", limit="lots")


def test_explain_parcel_non_integer_id_raises(con):
    with pytest.raises(at.ToolError):
        at.explain_parcel(con, parcel_id="abc")


def test_area_store_lru_bound_and_touch():
    """Bounded LRU: oldest evicted past maxsize, and get() touches (protects) an entry."""
    store = at.AreaStore(maxsize=2)

    def poly(x: float) -> dict:
        return {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}

    r0 = store.put(poly(0))
    r1 = store.put(poly(1))
    assert store.get(r0) is not None  # touch r0 -> r1 is now the LRU entry
    r2 = store.put(poly(2))  # evicts the LRU (r1), not the just-touched r0
    assert store.get(r1) is None
    assert store.get(r0) is not None
    assert store.get(r2) is not None
