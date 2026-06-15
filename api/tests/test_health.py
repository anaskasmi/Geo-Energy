"""Tests for the GEO-15 FastAPI skeleton: lifespan, /api/health, per-request cursor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app


def test_health_ok_with_real_artifact(healthy_data_dir):
    """With a real artifact, the app boots and /api/health is 200 status=ok."""
    with TestClient(app) as client:  # `with` triggers lifespan startup/shutdown
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["spatial"] is True
        assert body["artifact"].endswith("/current/site.duckdb")


def test_tolerant_startup_when_artifact_missing(empty_data_dir):
    """No artifact -> app still boots (no crashloop) and /api/health is 503."""
    with TestClient(app) as client:
        # Boot succeeded: connection was left None, error recorded.
        assert app.state.con is None
        assert app.state.con_error

        resp = client.get("/api/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unavailable"
        assert body["detail"]


def test_root_endpoint(healthy_data_dir):
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["service"] == "api"


def test_per_request_cursor_concurrent_requests(healthy_data_dir):
    """Concurrent-ish requests each get their own cursor and don't error.

    The shared read-only connection is not thread-safe; the per-request cursor
    dependency is what makes parallel requests safe.
    """
    with TestClient(app) as client:
        def hit(_):
            return client.get("/api/health").status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(hit, range(24)))

        assert codes == [200] * 24


def test_openapi_schema_builds(healthy_data_dir):
    """The OpenAPI schema builds and the expected routes are registered."""
    with TestClient(app) as client:
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/api/health" in paths
        assert "/" in paths
