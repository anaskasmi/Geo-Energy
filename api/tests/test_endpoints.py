"""GEO-17 endpoint tests: /api/score, /api/explain/{id}, /api/context.

Run end to end through the ASGI app (lifespan opens the real scored artifact) with TestClient.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import EMPTY_POLYGON, SCORED_POLYGON


@pytest.fixture
def client(scored_data_dir):
    with TestClient(app) as c:  # __enter__ runs lifespan -> opens artifact + loads zoning rules
        yield c


def test_score_utility_solar(client):
    r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "utility_solar"})
    assert r.status_code == 200, r.text
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    ids = [f["id"] for f in fc["features"]]
    assert ids == [1, 2, 7] or sorted(ids) == [1, 2, 7]  # ranked; survivors are P1,P2,P7
    # Ranked by score desc, ranks are 1-based and contiguous.
    scores = [f["properties"]["score"] for f in fc["features"]]
    assert scores == sorted(scores, reverse=True)
    assert [f["properties"]["rank"] for f in fc["features"]] == [1, 2, 3]
    # Each feature carries geometry + per-factor raw props.
    f0 = fc["features"][0]
    assert f0["id"] == 1
    assert f0["geometry"]["type"] == "Polygon"
    assert "ghi" in f0["properties"]["factors"]
    assert f0["properties"]["centroid"] and len(f0["properties"]["centroid"]) == 2
    assert fc["meta"]["use_case"] == "utility_solar"
    assert fc["meta"]["prohibited_zoning"] == ["E"]
    assert fc["meta"]["count"] == 3


def test_score_data_center_keeps_small_parcel(client):
    r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "data_center"})
    assert r.status_code == 200
    ids = sorted(f["id"] for f in r.json()["features"])
    assert ids == [1, 2, 4, 7]  # min_acres=5 keeps P4 (10 ac)


def test_score_threshold_override(client):
    """Lowering min_acres to 0 lets P4 (10 ac) through for utility_solar too."""
    r = client.post("/api/score", json={
        "geometry": SCORED_POLYGON, "use_case": "utility_solar",
        "thresholds": {"min_acres": 0},
    })
    assert r.status_code == 200
    assert 4 in {f["id"] for f in r.json()["features"]}


def test_score_weight_override(client):
    r = client.post("/api/score", json={
        "geometry": SCORED_POLYGON, "use_case": "utility_solar",
        "weights": {"ghi": 1.0},
    })
    assert r.status_code == 200
    assert pytest.approx(sum(r.json()["meta"]["weights"].values()), abs=1e-3) == 1.0


def test_score_pagination(client):
    r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "utility_solar", "limit": 1})
    fc = r.json()
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["rank"] == 1
    r2 = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "utility_solar", "limit": 1, "offset": 1})
    fc2 = r2.json()
    assert len(fc2["features"]) == 1
    assert fc2["features"][0]["properties"]["rank"] == 2  # rank reflects global offset
    assert fc2["features"][0]["id"] != fc["features"][0]["id"]


def test_score_empty_polygon(client):
    r = client.post("/api/score", json={"geometry": EMPTY_POLYGON, "use_case": "utility_solar"})
    assert r.status_code == 200
    assert r.json()["features"] == []


def test_score_bad_geometry_422(client):
    r = client.post("/api/score", json={"geometry": {"type": "Point", "coordinates": [0, 0]}, "use_case": "utility_solar"})
    assert r.status_code == 422


def test_score_bad_weight_factor_422(client):
    r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "weights": {"nope": 1.0}})
    assert r.status_code == 422


def test_score_geometry_too_complex_422(client):
    """A pathologically dense polygon is rejected (DoS guard) before reaching DuckDB."""
    ring = [[-119.0 + i * 1e-7, 35.3] for i in range(50_002)]
    ring.append(ring[0])
    r = client.post("/api/score", json={"geometry": {"type": "Polygon", "coordinates": [ring]}})
    assert r.status_code == 422


def test_score_503_detail_is_generic(empty_data_dir):
    """503 must not leak the artifact path or raw engine internals to clients."""
    with TestClient(app) as c:
        body = c.post("/api/score", json={"geometry": SCORED_POLYGON}).json()
        assert body["detail"] == "database unavailable"
        assert "/" not in body["detail"]  # no filesystem path


def test_score_coords_rounded(client):
    r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "utility_solar"})
    coords = r.json()["features"][0]["geometry"]["coordinates"][0][0]
    for c in coords:
        assert round(c, 6) == c  # 6-decimal rounding applied


def test_explain_breakdown(client):
    r = client.get("/api/explain/1", params={"use_case": "utility_solar"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parcel_id"] == 1
    assert body["excluded"] is False
    # Per-factor breakdown sums (approx) to the score, sorted by contribution desc.
    contributions = [f["contribution"] for f in body["factors"]]
    assert contributions == sorted(contributions, reverse=True)
    assert abs(sum(contributions) - body["score"]) <= 0.2
    assert "ghi" in body["raw"] and "dist_tx_m" in body["raw"]


def test_explain_reports_exclusion(client):
    """P3 is slope-excluded; explain reports it without filtering it out."""
    r = client.get("/api/explain/3")
    assert r.status_code == 200
    body = r.json()
    assert body["excluded"] is True
    assert body["exclusions"]["slope"] is True


def test_explain_unknown_slope_neutral(client):
    """P7 (NULL slope) -> slope factor known=False, normalized 0.5."""
    r = client.get("/api/explain/7")
    body = r.json()
    slope = next(f for f in body["factors"] if f["key"] == "slope")
    assert slope["known"] is False
    assert slope["normalized"] == 0.5


def test_explain_not_found_404(client):
    r = client.get("/api/explain/999999")
    assert r.status_code == 404


def test_context(client):
    r = client.get("/api/context")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"]["n_projects"] == 42
    assert body["total"]["active_total_mw"] == 3600.0
    types = {t["key"] for t in body["by_type"]}
    assert {"Solar", "Battery"} <= types
    # by_type sorted by total_mw desc.
    mws = [t["total_mw"] for t in body["by_type"]]
    assert mws == sorted(mws, reverse=True)


def test_endpoints_503_without_artifact(empty_data_dir):
    """With no artifact, scoring endpoints report unavailable (503) via get_cursor."""
    with TestClient(app) as c:
        assert c.post("/api/score", json={"geometry": SCORED_POLYGON}).status_code == 503
        assert c.get("/api/explain/1").status_code == 503
        assert c.get("/api/context").status_code == 503
