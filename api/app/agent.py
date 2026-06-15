"""Site-selection agent loop + SSE streaming protocol (GEO-21).

ONE module-level Pydantic-AI :class:`~pydantic_ai.Agent` (cached per model id) wraps the four
in-process GEO-20 tools (:mod:`app.agent_tools`). The agent ORCHESTRATES and NARRATES only — it
never computes geometry, parcel ids, or scores itself; those always come from the local DuckDB
engine through the tools. The ranked GeoJSON ``FeatureCollection`` returned to the client is
assembled HERE in handler code from the captured ``score_parcels`` tool result, never from model
text.

Provider is chosen by the env var ``AGENT_MODEL`` (default ``google:gemini-3.5-flash``); flipping
it to ``anthropic:…`` / ``openai:…`` needs no code change (but those optional extras are not
installed by default — see requirements.txt). ``defer_model_check=True`` keeps the app importable
and the agent constructable even when the provider package or key is absent; only an actual run
errors, and it does so gracefully as an SSE ``error`` event (never a 500 / stacktrace).

SECURITY (hard constraints):
  * ``GOOGLE_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` are NEVER logged, repr'd, put
    in an error message, or emitted in any SSE event (see :func:`_redact`).
  * Network is used ONLY for the LLM call on this request path; the tools/engine stay fully local.
  * Tool wrappers catch :class:`ToolError` and hand the model a structured error so it can recover
    and narrate, instead of crashing the stream.

Key-exhaustion protection (GEO-37): an oversized message is rejected with 422 BEFORE any model
call (see :class:`AgentRequest`); a per-process :class:`asyncio.Semaphore` caps concurrent runs;
and an overall :func:`asyncio.timeout` bounds each run and aborts the upstream LLM request on
timeout or client disconnect (google-genai exposes no clean transport-close handle).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from app import agent_tools as at
from app.models import UseCase

log = logging.getLogger("api.agent")

# --- Configuration (env, read with safe defaults; secrets never logged) -----------------------
DEFAULT_AGENT_MODEL = "google:gemini-3.5-flash"

# Secret env vars we must scrub from any log/error line before it leaves the process.
_SECRET_ENV_VARS = ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("ignoring malformed %s=%r; using default %d", name, raw, default)
        return default


def agent_model_id() -> str:
    """The configured Pydantic-AI model id (provider switch point)."""
    return os.environ.get("AGENT_MODEL", DEFAULT_AGENT_MODEL).strip() or DEFAULT_AGENT_MODEL


def max_message_chars() -> int:
    return _env_int("AGENT_MAX_MESSAGE_CHARS", 4000)


def max_concurrency() -> int:
    return max(1, _env_int("AGENT_MAX_CONCURRENCY", 4))


def timeout_seconds() -> float:
    return float(_env_int("AGENT_TIMEOUT_S", 60))


def _redact(text: str) -> str:
    """Replace any configured secret value found in ``text`` with ``[REDACTED]``.

    Defence in depth: provider errors are already key-free, but we scrub before logging so a
    future/wrapped message can never leak the key.
    """
    for name in _SECRET_ENV_VARS:
        val = os.environ.get(name)
        if val:
            text = text.replace(val, "[REDACTED]")
    return text


# --- Per-process concurrency cap on /api/agent (protect paid keys) -----------------------------
_semaphore: asyncio.Semaphore | None = None
_semaphore_size: int | None = None


def get_semaphore() -> asyncio.Semaphore:
    """The process-wide agent-run semaphore, (re)built if the configured size changed.

    Rebuilt only when ``AGENT_MAX_CONCURRENCY`` changes (handy for tests); under normal operation
    it is created once and reused.
    """
    global _semaphore, _semaphore_size
    size = max_concurrency()
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore


# --- Request model (oversized message -> 422 before any model call) ----------------------------
class AgentRequest(BaseModel):
    """POST /api/agent body.

    The length cap (``AGENT_MAX_MESSAGE_CHARS``) is enforced in a validator that reads the env at
    REQUEST time (so ops/tests can change it without a restart); oversized -> 422 before any model
    call, protecting the paid key from large-prompt exhaustion.
    """

    model_config = {"extra": "forbid"}

    message: str = Field(..., min_length=1)

    @field_validator("message")
    @classmethod
    def _check_length(cls, value: str) -> str:
        cap = max_message_chars()
        if len(value) > cap:
            raise ValueError(f"message too long: {len(value)} chars exceeds {cap}")
        return value


# --- Agent dependencies (bound server-side; the model never supplies these) --------------------
@dataclass
class Deps:
    """What the tools read from :class:`~pydantic_ai.RunContext`.

    ``lock`` serialises the (blocking) DuckDB calls: Pydantic-AI runs sync tools in a threadpool
    and a model may request parallel tool calls, but the shared cursor is not safe for concurrent
    use — so every engine call is taken under this per-request lock.
    """

    cur: Any
    zoning_rules: dict
    lock: threading.Lock


_INSTRUCTIONS = (
    "You are a site-selection assistant for renewable-energy projects in Kern County, California. "
    "You orchestrate local geospatial tools and narrate their results; you NEVER invent geometry, "
    "coordinates, parcel ids, or suitability scores — always obtain them from the tools. "
    "To rank parcels you MUST first call resolve_area to turn the user's place or area into an "
    "area_ref token, then call score_parcels with that area_ref and the use_case. Use "
    "explain_parcel for a single parcel's per-factor breakdown, and grid_context for "
    "interconnection-queue background (never part of scoring). If a tool returns an 'error' field, "
    "briefly tell the user what went wrong and suggest a fix (e.g. a clearer place name). Keep "
    "answers concise — a few sentences. Do NOT dump raw JSON or coordinates; the UI renders the "
    "map and the ranked table from the tool results."
)


def _register_tools(agent: Agent) -> None:
    """Register the four GEO-20 tools as FLAT, Gemini-safe wrappers reading ``ctx.deps``.

    Each wrapper catches :class:`ToolError` and returns ``{"error": …}`` so the model can recover
    and narrate rather than crashing the stream. ``score_parcels`` returns the full ranked
    FeatureCollection so the SSE handler can capture it from the tool-result event.
    """

    @agent.tool
    def resolve_area(ctx: RunContext[Deps], text: str) -> dict:
        """Resolve a Kern County place/area to a search area + an opaque area_ref token.

        Accepts a city or area name (e.g. 'Mojave', 'Bakersfield'), 'Kern County' for the whole
        county, a 'minLng,minLat,maxLng,maxLat' bounding box, or a 'lng,lat' point. Returns the
        area_ref to pass to score_parcels. Call this BEFORE score_parcels.
        """
        try:
            with ctx.deps.lock:
                out = at.resolve_area(text, cur=ctx.deps.cur)
        except at.ToolError as exc:
            return {"error": str(exc)}
        # Hand the model only the small, useful fields — never the full coordinate array.
        return {
            "area_ref": out["area_ref"],
            "label": out["label"],
            "source": out["source"],
            "approximate": out["approximate"],
            "bbox": out["bbox"],
            "centroid": out["centroid"],
        }

    @agent.tool
    def score_parcels(
        ctx: RunContext[Deps],
        area_ref: str,
        use_case: UseCase = "utility_solar",
        min_acres: float | None = None,
        max_slope_pct: float | None = None,
        limit: int = 200,
    ) -> dict:
        """Rank parcels in a resolved area by suitability (0-100) for a use case.

        Geometry is NEVER passed here — resolve the area first and pass its area_ref. The local
        engine computes all geometry and scores. Returns a ranked GeoJSON FeatureCollection.
        """
        try:
            with ctx.deps.lock:
                return at.score_parcels(
                    ctx.deps.cur,
                    area_ref=area_ref,
                    use_case=use_case,
                    min_acres=min_acres,
                    max_slope_pct=max_slope_pct,
                    limit=limit,
                    zoning_rules=ctx.deps.zoning_rules,
                )
        except at.ToolError as exc:
            return {"error": str(exc)}

    @agent.tool
    def explain_parcel(
        ctx: RunContext[Deps],
        parcel_id: int,
        use_case: UseCase = "utility_solar",
    ) -> dict:
        """Explain one parcel's suitability: per-factor breakdown + which hard exclusions it fails."""
        try:
            with ctx.deps.lock:
                return at.explain_parcel(
                    ctx.deps.cur,
                    parcel_id=parcel_id,
                    use_case=use_case,
                    zoning_rules=ctx.deps.zoning_rules,
                )
        except at.ToolError as exc:
            return {"error": str(exc)}

    @agent.tool
    def grid_context(ctx: RunContext[Deps]) -> dict:
        """CAISO interconnection-queue summary for Kern County (background context, not scoring)."""
        try:
            with ctx.deps.lock:
                return at.grid_context(ctx.deps.cur)
        except at.ToolError as exc:
            return {"error": str(exc)}


def _thinking_model_settings(model_id: str) -> dict | None:
    """Provider-specific thinking minimisation for TTFT — a no-op for non-Google providers.

    Gemini 3.x exposes a ``thinking_level`` (use LOW); Gemini 2.x uses a ``thinking_budget`` (use a
    small budget). The key/shape is the google-genai ``ThinkingConfigDict`` carried by Pydantic-AI's
    ``google_thinking_config`` setting (verified against the installed pydantic_ai.models.google).
    ``parallel_tool_calls=False`` keeps tool calls sequential (one cursor at a time).
    """
    mid = model_id.lower()
    if not (mid.startswith("google") or "gemini" in mid):
        return None  # anthropic/openai/etc.: omit Google-only keys entirely
    if "gemini-2." in mid or "gemini-1." in mid:
        thinking = {"thinking_budget": 128}
    else:  # gemini-3.x (incl. the default) and anything newer that supports thinking_level
        thinking = {"thinking_level": "LOW"}
    return {"google_thinking_config": thinking, "parallel_tool_calls": False}


@lru_cache(maxsize=4)
def _build_agent(model_id: str) -> Agent:
    """Build (and cache) the agent for a model id. ``defer_model_check`` keeps it import-safe."""
    agent = Agent(
        model_id,
        deps_type=Deps,
        instructions=_INSTRUCTIONS,
        model_settings=_thinking_model_settings(model_id),
        retries=1,
        defer_model_check=True,
    )
    _register_tools(agent)
    return agent


def get_agent() -> Agent:
    """The cached agent for the currently-configured ``AGENT_MODEL``.

    Tests inject a hermetic model with ``get_agent().override(model=TestModel())`` — the route
    uses this same instance, so the override is honoured (verified through FastAPI's TestClient).
    """
    return _build_agent(agent_model_id())


# --- SSE protocol ------------------------------------------------------------------------------
# event: <type>\ndata: <json>\n\n  — types: step | token | result | done | error  (see module/route doc).
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable proxy buffering so the first token is not stalled
    "Connection": "keep-alive",
}

# Map a tool name to the coarse phase the UI shows in a `step` event.
_PHASE = {
    "resolve_area": "resolving_area",
    "score_parcels": "scoring",
    "explain_parcel": "explaining",
    "grid_context": "grid_context",
}


def _sse(event: str, data: dict) -> str:
    """One SSE frame: ``event: <type>\\ndata: <json>\\n\\n``."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_agent(
    *,
    message: str,
    request: Any,
    con: Any,
    zoning_rules: dict,
    agent: Agent | None = None,
) -> AsyncIterator[str]:
    """Drive one agent run and yield the SSE event stream.

    Self-contained and crash-proof: it acquires the concurrency semaphore, opens a per-request
    cursor on the shared read-only handle, bounds the run with a timeout, watches for client
    disconnect, and ALWAYS terminates with ``done`` (or ``error`` then ``done``) — never a 500.
    The ranked FeatureCollection is captured from the last ``score_parcels`` tool-result event and
    emitted in the ``result`` event (assembled here, not from model text).

    ``request`` only needs an awaitable ``is_disconnected()``; ``agent`` defaults to
    :func:`get_agent` (override-friendly for tests).
    """
    sem = get_semaphore()
    if sem.locked():  # at capacity — refuse cleanly instead of opening another upstream call
        log.info("agent: at concurrency cap (%d); refusing request", _semaphore_size)
        yield _sse("error", {"message": "the assistant is busy right now, please retry shortly"})
        yield _sse("done", {})
        return

    await sem.acquire()
    cur = None
    captured_fc: dict | None = None
    captured_area: str | None = None
    try:
        if con is None:  # tolerant-startup: no artifact -> clean error, never 503/500 mid-stream
            yield _sse("error", {"message": "the scoring database is unavailable"})
            yield _sse("done", {})
            return

        cur = con.cursor()
        the_agent = agent if agent is not None else get_agent()
        deps = Deps(cur=cur, zoning_rules=zoning_rules or {}, lock=threading.Lock())
        settings = _thinking_model_settings(agent_model_id())

        try:
            async with asyncio.timeout(timeout_seconds()):
                async with the_agent.iter(message, deps=deps, model_settings=settings) as run:
                    async for node in run:
                        if await request.is_disconnected():
                            log.info("agent: client disconnected; aborting run")
                            return  # exiting the `async with` cancels/closes the upstream run

                        if Agent.is_model_request_node(node):
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    # Narrative arrives on the initial TextPart AND subsequent deltas.
                                    if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                                        if ev.part.content:
                                            yield _sse("token", {"text": ev.part.content})
                                    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                                        if ev.delta.content_delta:
                                            yield _sse("token", {"text": ev.delta.content_delta})

                        elif Agent.is_call_tools_node(node):
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    if isinstance(ev, FunctionToolCallEvent):
                                        name = ev.part.tool_name
                                        yield _sse("step", {"phase": _PHASE.get(name, name), "tool": name})
                                    elif isinstance(ev, FunctionToolResultEvent):
                                        name = ev.part.tool_name
                                        content = ev.part.content
                                        if (
                                            name == "score_parcels"
                                            and isinstance(content, dict)
                                            and content.get("type") == "FeatureCollection"
                                        ):
                                            captured_fc = content  # last score_parcels wins
                                        elif name == "resolve_area" and isinstance(content, dict):
                                            captured_area = content.get("label") or captured_area
        except TimeoutError:
            log.warning("agent: run exceeded %.0fs timeout", timeout_seconds())
            yield _sse("error", {"message": "the request timed out, please try a narrower query"})
            yield _sse("done", {})
            return
        except Exception as exc:  # provider/key/transport failure -> graceful, key-safe error
            log.error("agent run failed: %s", _redact(f"{type(exc).__name__}: {exc}"))
            yield _sse("error", {"message": "the assistant is temporarily unavailable"})
            yield _sse("done", {})
            return

        if captured_fc is not None:
            payload: dict[str, Any] = {"featureCollection": captured_fc}
            if captured_area:
                payload["area"] = captured_area
            yield _sse("result", payload)
        yield _sse("done", {})
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:  # noqa: BLE001 — cursor close must never break the response
                pass
        sem.release()
