"""GEO-21 agent loop + SSE route tests, and GEO-37 guards (oversized/concurrency/timing).

All hermetic: NO live network and NO real API key. Models are injected with
``get_agent().override(model=...)`` (verified to propagate through FastAPI's TestClient) using
Pydantic-AI's ``TestModel`` / ``FunctionModel``.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from app import agent as agent_mod
from app import agent_tools as at
from app import db, scoring
from app.main import app
from tests.conftest import SCORED_POLYGON  # noqa: F401 (documents the scored bbox shape)

_BBOX = "-119.05,35.28,-118.93,35.33"  # the conftest SCORED_POLYGON bbox; solar survivors = {1,2,7}


# --- helpers -----------------------------------------------------------------------------------
def parse_sse(text: str) -> list[dict]:
    """Parse an SSE body into ``[{"event": <type>, "data": <parsed json>}]`` in order."""
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


@pytest.fixture
def client(scored_data_dir):
    with TestClient(app) as c:  # lifespan opens the real scored artifact + loads zoning rules
        yield c


@pytest.fixture(autouse=True)
def _clear_store():
    at.area_store.clear()
    yield
    at.area_store.clear()


_AFFORD_OK = {
    "ok": True,
    "median_home_value_usd": 310600,
    "acs_vintage": "2023 ACS 5-year",
    "hpi_index": 324.07,
    "price_trend_yoy_pct": 4.6,
    "hpi_as_of": "2024",
    "sources": ["Census ACS5 B25077_001E", "FRED ATNHPIUS06029A"],
}


@pytest.fixture(autouse=True)
def _stub_landvalue(monkeypatch):
    """Keep every agent test hermetic: the live check_affordability call returns canned data.

    TestModel auto-calls EVERY registered tool, so without this the full-loop test would hit the
    real FRED/Census APIs over the network.
    """
    monkeypatch.setattr(at.landvalue, "area_affordability", lambda **kw: dict(_AFFORD_OK))
    yield


def _engine_fc(use_case: str = "utility_solar") -> dict:
    """The exact FeatureCollection the local engine produces for the bbox (for equality asserts)."""
    con = db.connect(db.artifact_path(), read_only=True)
    try:
        rules = scoring.load_zoning_rules(db.zoning_rules_path())
        return at.score_parcels(con.cursor(), area_ref=_BBOX, use_case=use_case, zoning_rules=rules)
    finally:
        con.close()


# --- 1) Full loop with TestModel -------------------------------------------------------------
def test_agent_full_loop_testmodel(client, monkeypatch):
    """TestModel auto-calls every tool; with 'a' made resolvable, score_parcels yields a real FC.

    Asserts: step events precede narrative tokens; a `result` event carries the engine's
    FeatureCollection (equal to a direct engine call, NOT model text); terminal `done`.
    """
    # TestModel fills string args with 'a'; make 'a' resolve to a box over the scored parcels.
    monkeypatch.setitem(at._PLACE_CENTERS, "a", (-119.0, 35.30))

    with agent_mod.get_agent().override(model=TestModel()):
        resp = client.post("/api/agent", json={"message": "score solar near a"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert types[-1] == "done"  # terminal
    assert "step" in types and "result" in types

    first_step = types.index("step")
    first_token = types.index("token") if "token" in types else len(types)
    assert first_step < first_token  # steps emitted before narrative (perceived latency)

    # The score_parcels step is present and uses the documented phase.
    score_steps = [e for e in events if e["event"] == "step" and e["data"]["tool"] == "score_parcels"]
    assert score_steps and score_steps[0]["data"]["phase"] == "scoring"

    # The result FeatureCollection equals the ENGINE output (assembled from the tool result).
    result = next(e for e in events if e["event"] == "result")
    fc = result["data"]["featureCollection"]
    assert fc["type"] == "FeatureCollection"
    assert {f["properties"]["id"] for f in fc["features"]} == {1, 2, 7}
    assert fc == _engine_fc("utility_solar")


# --- 2) Deterministic FunctionModel(stream_function) -----------------------------------------
def test_agent_deterministic_function_model(client):
    """Force resolve_area -> score_parcels -> narration and assert the streamed protocol."""
    calls = {"n": 0}

    async def stream_fn(messages: list, info: AgentInfo):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(name="resolve_area", json_args=json.dumps({"text": _BBOX}))}
        elif calls["n"] == 2:
            yield {0: DeltaToolCall(
                name="score_parcels",
                json_args=json.dumps({"area_ref": _BBOX, "use_case": "utility_solar"}),
            )}
        else:
            for chunk in ("Top ", "solar ", "parcels ", "ranked."):
                yield chunk

    with agent_mod.get_agent().override(model=FunctionModel(stream_function=stream_fn)):
        resp = client.post("/api/agent", json={"message": "rank solar parcels in the bbox"})
    assert resp.status_code == 200, resp.text

    events = parse_sse(resp.text)
    types = [e["event"] for e in events]

    # Tool steps come first, in order, before any narrative token.
    step_tools = [e["data"]["tool"] for e in events if e["event"] == "step"]
    assert step_tools == ["resolve_area", "score_parcels"]
    assert types.index("step") < types.index("token")

    # Narration streamed across deltas (initial chunk + subsequent deltas all captured).
    narration = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert narration == "Top solar parcels ranked."

    # FeatureCollection assembled from the score_parcels tool result == engine output.
    result = next(e for e in events if e["event"] == "result")
    fc = result["data"]["featureCollection"]
    assert {f["properties"]["id"] for f in fc["features"]} == {1, 2, 7}
    assert fc == _engine_fc("utility_solar")
    # resolve_area label surfaced on the result.
    assert result["data"]["area"] == "custom bounding box"
    assert types[-1] == "done"


# --- 2a) set_map_view relay: layer toggles + basemap reach the `result` event -----------------
def test_agent_set_map_view_relayed(client):
    """A set_map_view tool call is captured and emitted as a `result.mapView` relay payload.

    Mirrors the focus_parcel/export_pdf relay path: the engine does nothing; the browser ACTS on
    the payload. Asserts the documented `updating_map` step phase and the normalized canonical ids.
    """
    calls = {"n": 0}

    async def stream_fn(messages: list, info: AgentInfo):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(
                name="set_map_view",
                json_args=json.dumps({"show": "transmission", "hide": "flood", "basemap": "satellite"}),
            )}
        else:
            for chunk in ("Switched ", "the map."):
                yield chunk

    with agent_mod.get_agent().override(model=FunctionModel(stream_function=stream_fn)):
        resp = client.post("/api/agent", json={"message": "show transmission, hide flood, satellite"})
    assert resp.status_code == 200, resp.text

    events = parse_sse(resp.text)
    step = next(e for e in events if e["event"] == "step" and e["data"]["tool"] == "set_map_view")
    assert step["data"]["phase"] == "updating_map"

    result = next(e for e in events if e["event"] == "result")["data"]
    assert result["mapView"] == {
        "type": "MapView",
        "show": ["transmission"],
        "hide": ["sfha"],
        "basemap": "satellite",
    }
    assert [e["event"] for e in events][-1] == "done"


def test_agent_zoom_map_relayed(client):
    """A zoom_map tool call is captured and emitted as a `result.zoomMap` relay payload."""
    calls = {"n": 0}

    async def stream_fn(messages: list, info: AgentInfo):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(
                name="zoom_map",
                json_args=json.dumps({"direction": "out", "percent": 15}),
            )}
        else:
            for chunk in ("Zoomed ", "out a bit."):
                yield chunk

    with agent_mod.get_agent().override(model=FunctionModel(stream_function=stream_fn)):
        resp = client.post("/api/agent", json={"message": "zoom out a bit"})
    assert resp.status_code == 200, resp.text

    events = parse_sse(resp.text)
    step = next(e for e in events if e["event"] == "step" and e["data"]["tool"] == "zoom_map")
    assert step["data"]["phase"] == "zooming"

    result = next(e for e in events if e["event"] == "result")["data"]
    assert result["zoomMap"] == {"type": "ZoomMap", "direction": "out", "percent": 15.0}
    assert [e["event"] for e in events][-1] == "done"


# --- 2b) Affordability flow: resolve -> check_affordability -> score_parcels(affordability) ---
def test_agent_affordability_flow(client):
    """Force the affordability turn and assert the SSE protocol + blended result.

    The `result` event carries BOTH the ranked FeatureCollection AND the affordability summary;
    passing affordability_score=1.0 to score_parcels lifts every score above the plain engine.
    """
    calls = {"n": 0}

    async def stream_fn(messages: list, info: AgentInfo):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(name="resolve_area", json_args=json.dumps({"text": _BBOX}))}
        elif calls["n"] == 2:
            yield {0: DeltaToolCall(name="check_affordability", json_args=json.dumps({"area_ref": _BBOX}))}
        elif calls["n"] == 3:
            yield {0: DeltaToolCall(
                name="score_parcels",
                json_args=json.dumps(
                    {"area_ref": _BBOX, "use_case": "utility_solar", "affordability_score": 1.0}
                ),
            )}
        else:
            for chunk in ("Affordable ", "area."):
                yield chunk

    with agent_mod.get_agent().override(model=FunctionModel(stream_function=stream_fn)):
        resp = client.post("/api/agent", json={"message": "is this area affordable? rank solar"})
    assert resp.status_code == 200, resp.text

    events = parse_sse(resp.text)
    step_tools = [e["data"]["tool"] for e in events if e["event"] == "step"]
    assert step_tools == ["resolve_area", "check_affordability", "score_parcels"]
    aff_step = next(e for e in events if e["event"] == "step" and e["data"]["tool"] == "check_affordability")
    assert aff_step["data"]["phase"] == "checking_affordability"

    result = next(e for e in events if e["event"] == "result")["data"]
    assert result["affordability"]["type"] == "Affordability"
    assert result["affordability"]["median_home_value_usd"] == 310600

    fc = result["featureCollection"]
    assert {f["properties"]["id"] for f in fc["features"]} == {1, 2, 7}
    plain = {f["properties"]["id"]: f["properties"]["score"] for f in _engine_fc("utility_solar")["features"]}
    for f in fc["features"]:
        assert f["properties"]["score"] >= plain[f["properties"]["id"]]  # affordability lifted it
    assert [e["event"] for e in events][-1] == "done"


def test_agent_drawn_area_forwarded_to_model(client):
    """A drawn polygon in the request is resolved to a token and surfaced to the model.

    The model echoes the injected map-context area_ref into its narration, proving the SELECTED
    area reached it (no resolve_area needed).
    """
    seen = {"ref": None}

    async def stream_fn(messages: list, info: AgentInfo):
        # The first user message carries the injected "[map context] ... area_ref \"area_…\"" line.
        text = "".join(
            getattr(p, "content", "") for m in messages for p in getattr(m, "parts", []) if isinstance(getattr(p, "content", None), str)
        )
        import re

        m = re.search(r'area_ref is "(area_[0-9a-f]+)"', text)
        seen["ref"] = m.group(1) if m else None
        for chunk in ("Using your ", "selected area."):
            yield chunk

    geom = {
        "type": "Polygon",
        "coordinates": [[[-119.05, 35.28], [-118.93, 35.28], [-118.93, 35.33], [-119.05, 35.33], [-119.05, 35.28]]],
    }
    with agent_mod.get_agent().override(model=FunctionModel(stream_function=stream_fn)):
        resp = client.post("/api/agent", json={"message": "score this area", "area_geometry": geom})
    assert resp.status_code == 200, resp.text
    assert seen["ref"] is not None and seen["ref"].startswith("area_")
    # The token resolves back to the drawn geometry server-side.
    assert at.area_store.get(seen["ref"]) is not None


# --- 2c) /api/affordability endpoint (parity: voice agent reaches the same capability) -------
def test_affordability_endpoint(client):
    r = client.get("/api/affordability")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "Affordability"
    assert body["median_home_value_usd"] == 310600
    assert body["affordability_band"] == "affordable"
    assert 0.6 <= body["affordability_score"] <= 0.7


def test_affordability_endpoint_unavailable(client, monkeypatch):
    monkeypatch.setattr(at.landvalue, "area_affordability", lambda **kw: {"ok": False, "error": "down"})
    r = client.get("/api/affordability")
    assert r.status_code == 200  # degrades cleanly, never a 5xx
    body = r.json()
    assert body["available"] is False
    assert "error" in body


# --- 3) Provider switch: uninstalled provider -> graceful error (no 500/stacktrace) ----------
def test_agent_provider_switch_graceful_error(client, monkeypatch):
    """AGENT_MODEL=anthropic:... constructs (defer_model_check) but a run errors cleanly."""
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-opus-4-8")
    # No override: exercise the real (missing) provider path.
    resp = client.post("/api/agent", json={"message": "score solar near Mojave"})
    assert resp.status_code == 200, resp.text  # never a 500

    events = parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "error" in types
    assert types[-1] == "done"
    assert "result" not in types
    err = next(e for e in events if e["event"] == "error")
    assert isinstance(err["data"]["message"], str) and err["data"]["message"]
    # No stacktrace / internals leaked to the client.
    assert "Traceback" not in resp.text
    assert "claude-opus-4-8" not in resp.text


# --- 4) Secret redaction -----------------------------------------------------------------------
def test_agent_api_key_never_leaks(client, monkeypatch, caplog):
    """A sentinel GOOGLE_API_KEY must not appear in logs or any SSE event, even on error."""
    sentinel = "SENTINEL_KEY_DO_NOT_LEAK_abc123"
    monkeypatch.setenv("GOOGLE_API_KEY", sentinel)

    async def boom(messages: list, info: AgentInfo):
        raise RuntimeError(f"upstream auth failed using key={sentinel}")
        yield  # pragma: no cover — marks this an async generator

    with caplog.at_level(logging.INFO):
        with agent_mod.get_agent().override(model=FunctionModel(stream_function=boom)):
            resp = client.post("/api/agent", json={"message": "score solar near Mojave"})

    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"
    # The sentinel leaks nowhere.
    assert sentinel not in resp.text
    assert sentinel not in caplog.text


def test_redact_helper(monkeypatch):
    """Unit: _redact scrubs every configured secret value from a string."""
    monkeypatch.setenv("GOOGLE_API_KEY", "g-secret-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-secret-456")
    out = agent_mod._redact("error g-secret-123 and a-secret-456 here")
    assert "g-secret-123" not in out and "a-secret-456" not in out
    assert out.count("[REDACTED]") == 2


# --- 5) Oversized message -> 422 (before any model call) -------------------------------------
def test_agent_oversized_message_422(client):
    big = "x" * (agent_mod.max_message_chars() + 1)
    resp = client.post("/api/agent", json={"message": big})
    assert resp.status_code == 422


def test_agent_custom_message_cap_env(client, monkeypatch):
    """The cap is read at request time, so ops can tighten it without a restart."""
    monkeypatch.setenv("AGENT_MAX_MESSAGE_CHARS", "10")
    resp = client.post("/api/agent", json={"message": "this is definitely more than ten chars"})
    assert resp.status_code == 422


def test_agent_empty_message_422(client):
    assert client.post("/api/agent", json={"message": ""}).status_code == 422


# --- 6) Concurrency cap -> clean "busy" (no extra upstream call) ------------------------------
def test_agent_concurrency_cap_busy(monkeypatch):
    """With the per-process semaphore saturated, a new run is refused with an `error` event."""
    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "1")
    # Fresh semaphore bound to THIS test's event loop (avoid cross-loop reuse from TestClient runs).
    monkeypatch.setattr(agent_mod, "_semaphore", None)
    monkeypatch.setattr(agent_mod, "_semaphore_size", None)

    class _Req:
        async def is_disconnected(self):
            return False

    async def scenario():
        sem = agent_mod.get_semaphore()
        await sem.acquire()  # saturate the only slot
        try:
            return [
                chunk
                async for chunk in agent_mod.stream_agent(
                    message="score solar", request=_Req(), con=None, zoning_rules={},
                )
            ]
        finally:
            sem.release()

    chunks = asyncio.run(scenario())
    events = parse_sse("".join(chunks))
    types = [e["event"] for e in events]
    assert types == ["error", "done"]  # busy refusal, no result, no upstream call
    assert "busy" in next(e for e in events if e["event"] == "error")["data"]["message"].lower()


def test_agent_db_unavailable_error(monkeypatch):
    """con=None (tolerant startup, no artifact) -> clean error event, never a 500."""
    monkeypatch.setattr(agent_mod, "_semaphore", None)
    monkeypatch.setattr(agent_mod, "_semaphore_size", None)

    class _Req:
        async def is_disconnected(self):
            return False

    async def scenario():
        return [
            chunk
            async for chunk in agent_mod.stream_agent(
                message="hi", request=_Req(), con=None, zoning_rules={},
            )
        ]

    events = parse_sse("".join(asyncio.run(scenario())))
    assert [e["event"] for e in events] == ["error", "done"]
    assert "unavailable" in events[0]["data"]["message"].lower()


# --- 7) Request-timing observability middleware (GEO-37) -------------------------------------
def test_request_timing_log_line(client, caplog):
    with caplog.at_level(logging.INFO, logger="api.access"):
        assert client.get("/api/health").status_code == 200
    recs = [r for r in caplog.records if r.name == "api.access"]
    assert recs, "expected a request-timing log line"
    msg = recs[-1].getMessage()
    assert "method=GET" in msg
    assert "path=/api/health" in msg  # route template, low cardinality
    assert "status=200" in msg
    assert "duration_ms=" in msg


def test_request_timing_logs_x_cache(client, caplog):
    """A /api/score request surfaces the X-Cache header in the timing line."""
    with caplog.at_level(logging.INFO, logger="api.access"):
        r = client.post("/api/score", json={"geometry": SCORED_POLYGON, "use_case": "utility_solar"})
        assert r.status_code == 200
    msg = [r for r in caplog.records if r.name == "api.access"][-1].getMessage()
    assert "path=/api/score" in msg
    assert "x_cache=" in msg
