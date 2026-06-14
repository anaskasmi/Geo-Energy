"""arcgis: FeatureServer pagination + primary→fallback (offline via MockTransport)."""

import json
from pathlib import Path

import httpx
import pytest

from pipeline import arcgis
from pipeline.sources import SourceError


def _feature(i):
    return {
        "type": "Feature",
        "properties": {"APN": f"{i:03d}"},
        "geometry": {"type": "Point", "coordinates": [-118.7, 35.3]},
    }


def _paged_handler(total, server_page):
    """A handler that pages `total` features `server_page` at a time, setting
    exceededTransferLimit until the last page (the real ArcGIS contract)."""

    def handler(request):
        params = request.url.params
        offset = int(params.get("resultOffset", "0"))
        page = [_feature(i) for i in range(offset, min(offset + server_page, total))]
        exceeded = offset + server_page < total
        return httpx.Response(
            200, json={"type": "FeatureCollection", "features": page, "exceededTransferLimit": exceeded}
        )

    return handler


def test_pagination_collects_all_features(tmp_path):
    dest = tmp_path / "p.geojson"
    n = arcgis.fetch_featureserver_geojson(
        "https://srv/FeatureServer/0", dest,
        page_size=2, transport=httpx.MockTransport(_paged_handler(total=5, server_page=2)),
    )
    assert n == 5
    fc = json.loads(dest.read_text())
    assert fc["type"] == "FeatureCollection"
    assert [f["properties"]["APN"] for f in fc["features"]] == ["000", "001", "002", "003", "004"]


def test_server_cap_smaller_than_page_size_still_completes(tmp_path):
    """If the server caps pages below our requested size, exceededTransferLimit drives paging."""
    dest = tmp_path / "p.geojson"
    n = arcgis.fetch_featureserver_geojson(
        "https://srv/FeatureServer/0", dest,
        page_size=1000, transport=httpx.MockTransport(_paged_handler(total=7, server_page=3)),
    )
    assert n == 7


def test_arcgis_error_payload_raises(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"error": {"code": 400, "message": "bad"}})

    with pytest.raises(SourceError):
        arcgis.fetch_featureserver_geojson(
            "https://srv/FeatureServer/0", tmp_path / "p.geojson",
            transport=httpx.MockTransport(handler),
        )


def test_max_pages_guard(tmp_path):
    def handler(request):  # never terminates (always exceeded, always full)
        return httpx.Response(200, json={"features": [_feature(0), _feature(1)], "exceededTransferLimit": True})

    with pytest.raises(SourceError):
        arcgis.fetch_featureserver_geojson(
            "https://srv/FeatureServer/0", tmp_path / "p.geojson",
            page_size=2, max_pages=3, transport=httpx.MockTransport(handler),
        )


def test_fallback_uses_second_source_when_first_fails(tmp_path):
    primary = "https://geodat/FeatureServer/0"
    mirror = "https://shafter/FeatureServer/0"

    def handler(request):
        if "geodat" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json={"features": [_feature(0)], "exceededTransferLimit": False})

    dest = tmp_path / "p.geojson"
    used, count = arcgis.fetch_with_fallback(
        [primary, mirror], dest, transport=httpx.MockTransport(handler), retries=1,
    )
    assert used == mirror
    assert count == 1


def test_fallback_skips_empty_source(tmp_path):
    primary = "https://geodat/FeatureServer/0"
    mirror = "https://shafter/FeatureServer/0"

    def handler(request):
        if "geodat" in request.url.host:
            return httpx.Response(200, json={"features": [], "exceededTransferLimit": False})
        return httpx.Response(200, json={"features": [_feature(0)], "exceededTransferLimit": False})

    used, count = arcgis.fetch_with_fallback(
        [primary, mirror], tmp_path / "p.geojson", transport=httpx.MockTransport(handler), retries=1,
    )
    assert used == mirror and count == 1


def test_fallback_all_fail_raises(tmp_path):
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(SourceError):
        arcgis.fetch_with_fallback(
            ["https://a/FeatureServer/0", "https://b/FeatureServer/0"],
            tmp_path / "p.geojson", transport=httpx.MockTransport(handler), retries=1,
        )
