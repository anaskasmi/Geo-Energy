"""GEO-18 performance pass: gzip, ETag/304 on idempotent GETs, LRU score cache."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import SCORED_POLYGON


@pytest.fixture
def client(scored_data_dir):
    with TestClient(app) as c:
        yield c


def test_gzip_large_response(client):
    """A sizeable JSON response is gzip-encoded when the client accepts it."""
    r = client.post(
        "/api/score",
        json={"geometry": SCORED_POLYGON, "use_case": "utility_solar"},
        headers={"Accept-Encoding": "gzip"},
    )
    assert r.status_code == 200
    # httpx transparently decodes; the header proves the wire was compressed.
    assert r.headers.get("content-encoding") == "gzip"


def test_etag_and_304_on_context(client):
    r1 = client.get("/api/context")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag
    r2 = client.get("/api/context", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    assert not r2.content  # 304 carries no body


def test_etag_on_explain(client):
    r1 = client.get("/api/explain/1")
    etag = r1.headers.get("etag")
    assert etag
    r2 = client.get("/api/explain/1", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    # A different parcel has a different representation -> different ETag.
    r3 = client.get("/api/explain/2")
    assert r3.headers.get("etag") != etag


def test_score_cache_hit_miss(client):
    body = {"geometry": SCORED_POLYGON, "use_case": "utility_solar"}
    r1 = client.post("/api/score", json=body)
    assert r1.headers.get("X-Cache") == "MISS"
    r2 = client.post("/api/score", json=body)
    assert r2.headers.get("X-Cache") == "HIT"
    assert r1.json()["features"] == r2.json()["features"]


def test_score_cache_key_varies(client):
    """Different use_case / page is a different cache key (own MISS)."""
    base = {"geometry": SCORED_POLYGON}
    client.post("/api/score", json={**base, "use_case": "utility_solar"})
    r = client.post("/api/score", json={**base, "use_case": "data_center"})
    assert r.headers.get("X-Cache") == "MISS"
    r2 = client.post("/api/score", json={**base, "use_case": "utility_solar", "offset": 1})
    assert r2.headers.get("X-Cache") == "MISS"


def test_health_etag_does_not_break(client):
    """ETag middleware must not disturb the health contract."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_etag_is_weak(client):
    """The validator is weak (computed pre-compression, stable across content-codings)."""
    etag = client.get("/api/context").headers.get("etag")
    assert etag and etag.startswith('W/"')


def test_if_none_match_star_and_304_carries_vary(client):
    """If-None-Match: * yields 304 (RFC 9110 13.1.2); the 304 carries Vary: Accept-Encoding."""
    r = client.get("/api/context", headers={"If-None-Match": "*"})
    assert r.status_code == 304
    assert "accept-encoding" in (r.headers.get("vary", "").lower())


def test_if_none_match_list(client):
    """A comma-separated If-None-Match list containing the etag yields 304."""
    etag = client.get("/api/context").headers["etag"]
    r = client.get("/api/context", headers={"If-None-Match": f'"x", {etag}'})
    assert r.status_code == 304
