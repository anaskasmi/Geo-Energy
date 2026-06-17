"""Per-IP, per-mode USD budget cap + model pricing (GEO-44).

Hermetic: no network, no real key. The text-limit path is exercised WITHOUT a model call (the cap
is checked before any upstream request), and the voice paths hit the plain mint/usage endpoints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import budget, pricing
from app.main import app


@pytest.fixture
def client(scored_data_dir):
    with TestClient(app) as c:
        yield c


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        evt: dict = {"event": None, "data": None}
        for line in block.splitlines():
            if line.startswith("event:"):
                evt["event"] = line[len("event:"):].strip()
            elif line.startswith("data:"):
                evt["data"] = json.loads(line[len("data:"):].strip())
        events.append(evt)
    return events


# --- pricing ----------------------------------------------------------------------------------
@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


def test_text_cost_gemini_flash():
    # 1M input + 1M output at Gemini 3.5 Flash rates = $1.50 + $9.00 = $10.50.
    cost = pricing.text_cost_usd(_Usage(input_tokens=1_000_000, output_tokens=1_000_000), "google:gemini-3.5-flash")
    assert cost == pytest.approx(10.50, rel=1e-6)


def test_text_cost_discounts_cached_input():
    full = pricing.text_cost_usd(_Usage(input_tokens=1_000_000), "google:gemini-3.5-flash")
    cached = pricing.text_cost_usd(_Usage(input_tokens=1_000_000, cache_read_tokens=1_000_000), "google:gemini-3.5-flash")
    assert cached < full  # cached input is cheaper
    assert cached == pytest.approx(0.15, rel=1e-6)


def test_text_cost_unknown_model_falls_back():
    assert pricing.text_cost_usd(_Usage(input_tokens=1_000_000), "openai:some-future-model") > 0


def test_text_cost_handles_none_and_junk():
    assert pricing.text_cost_usd(None, "google:gemini-3.5-flash") == 0.0
    assert pricing.text_cost_usd(_Usage(), "google:gemini-3.5-flash") == 0.0


def test_realtime_cost_prices_modalities():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "input_token_details": {"audio_tokens": 1_000_000, "text_tokens": 0},
        "output_token_details": {"audio_tokens": 1_000_000, "text_tokens": 0},
    }
    # audio in (32 + 3 transcribe) + audio out (64) = $99 per (1M in, 1M out).
    assert pricing.realtime_cost_usd(usage) == pytest.approx(99.0, rel=1e-6)


def test_realtime_cost_defensive():
    assert pricing.realtime_cost_usd(None) == 0.0
    assert pricing.realtime_cost_usd({}) == 0.0
    assert pricing.realtime_cost_usd({"input_tokens": "oops"}) == 0.0


# --- budget ledger ----------------------------------------------------------------------------
def test_budget_add_and_exceeded(monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_USD", "3")
    budget.clear()
    assert not budget.exceeded("1.2.3.4", "text")
    budget.add("1.2.3.4", "text", 2.5)
    assert not budget.exceeded("1.2.3.4", "text")
    budget.add("1.2.3.4", "text", 0.6)  # now $3.1 ≥ $3
    assert budget.exceeded("1.2.3.4", "text")
    # The other mode and other IPs are independent.
    assert not budget.exceeded("1.2.3.4", "voice")
    assert not budget.exceeded("9.9.9.9", "text")


def test_budget_disabled_when_cap_nonpositive(monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_USD", "0")
    budget.clear()
    budget.add("1.2.3.4", "text", 1000.0)
    assert not budget.exceeded("1.2.3.4", "text")


def test_budget_no_ip_never_limited():
    assert not budget.exceeded(None, "text")
    assert budget.add(None, "text", 5.0) == 0.0


def test_client_ip_prefers_forwarded_for():
    class _Req:
        def __init__(self, headers, host):
            self.headers = headers
            self.client = type("C", (), {"host": host})()

    assert budget.client_ip(_Req({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, "10.0.0.1")) == "203.0.113.7"
    assert budget.client_ip(_Req({"x-real-ip": "198.51.100.2"}, "10.0.0.1")) == "198.51.100.2"
    assert budget.client_ip(_Req({}, "127.0.0.1")) == "127.0.0.1"


# --- integration: text agent refuses over-budget BEFORE any model call ------------------------
def test_agent_text_limit_event(client, monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_USD", "3")
    budget.clear()
    budget.add("testclient", "text", 100.0)  # TestClient's peer host is "testclient"

    resp = client.post("/api/agent", json={"message": "best solar near Mojave"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert types == ["limit", "done"]  # no upstream model call
    limit = events[0]["data"]
    assert "limit" in limit["message"].lower()
    assert limit["limitReached"] is True
    assert limit["limitUsd"] == 3.0


# --- integration: voice session mint refuses over-budget; usage accrues -----------------------
def test_realtime_session_limit(client, monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_USD", "3")
    budget.clear()
    budget.add("testclient", "voice", 100.0)
    body = client.post("/api/realtime/session").json()
    assert body["limitReached"] is True
    assert "value" not in body  # no ephemeral secret minted when over budget


def test_realtime_usage_accrues_and_caps(client, monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_USD", "3")
    budget.clear()
    # A turn that costs > $3: 1M audio in + 1M audio out ≈ $99.
    usage = {
        "input_token_details": {"audio_tokens": 1_000_000},
        "output_token_details": {"audio_tokens": 1_000_000},
    }
    body = client.post("/api/realtime/usage", json={"usage": usage}).json()
    assert body["spentUsd"] > 3.0
    assert body["limitReached"] is True
    # A subsequent mint is now refused for this IP.
    assert client.post("/api/realtime/session").json()["limitReached"] is True
