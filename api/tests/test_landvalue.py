"""GEO-41 live land-affordability lookup (app.landvalue) — hermetic (no real network).

``urllib.request.urlopen`` is monkeypatched so these never hit FRED/Census. Covers the happy path,
the Census "no estimate" sentinel, total unreachability, and the hard rule that an API key never
leaks into a log line.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from app import landvalue


class _FakeResp:
    """A minimal context-manager stand-in for an ``http.client.HTTPResponse``."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fred_body(observations: list[dict]) -> bytes:
    return json.dumps({"observations": observations}).encode("utf-8")


def _census_body(value: str) -> bytes:
    return json.dumps(
        [["B25077_001E", "NAME", "state", "county"], [value, "Kern County, California", "06", "029"]]
    ).encode("utf-8")


def test_area_affordability_success(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fredkey")
    monkeypatch.setenv("CENSUS_API_KEY", "censuskey")

    def fake_urlopen(req, timeout=None):
        if "stlouisfed" in req.full_url:
            return _FakeResp(_fred_body([
                {"date": "2024-01-01", "value": "324.07"},
                {"date": "2023-01-01", "value": "309.89"},
            ]))
        return _FakeResp(_census_body("310600"))

    monkeypatch.setattr(landvalue.urllib.request, "urlopen", fake_urlopen)
    out = landvalue.area_affordability()
    assert out["ok"] is True
    assert out["median_home_value_usd"] == 310600
    assert out["hpi_index"] == 324.07
    assert out["price_trend_yoy_pct"] == pytest.approx(4.6, abs=0.1)
    assert out["hpi_as_of"] == "2024"
    assert out["acs_vintage"].endswith("ACS 5-year")
    assert len(out["sources"]) == 2


def test_area_affordability_fred_only_when_no_census(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fredkey")
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)

    def fake_urlopen(req, timeout=None):
        if "stlouisfed" in req.full_url:
            return _FakeResp(_fred_body([{"date": "2024-01-01", "value": "324.07"}]))
        # Census returns the "no estimate" sentinel.
        return _FakeResp(_census_body("-666666666"))

    monkeypatch.setattr(landvalue.urllib.request, "urlopen", fake_urlopen)
    out = landvalue.area_affordability()
    assert out["ok"] is True
    assert out.get("median_home_value_usd") is None  # census sentinel -> field absent
    assert out["hpi_index"] == 324.07


def test_area_affordability_all_unreachable(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fredkey")
    monkeypatch.setenv("CENSUS_API_KEY", "censuskey")

    def boom(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(landvalue.urllib.request, "urlopen", boom)
    out = landvalue.area_affordability()
    assert out["ok"] is False
    assert "error" in out


def test_landvalue_key_never_leaks(monkeypatch, caplog):
    sentinel = "FRED_SENTINEL_DO_NOT_LEAK_xyz789"
    monkeypatch.setenv("FRED_API_KEY", sentinel)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)

    def http_error(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(f"invalid api_key={sentinel}".encode())
        )

    monkeypatch.setattr(landvalue.urllib.request, "urlopen", http_error)
    with caplog.at_level(logging.INFO):
        out = landvalue.area_affordability()
    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert sentinel not in json.dumps(out)
