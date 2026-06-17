"""Per-IP, per-mode USD spending cap for the AI agents (GEO-44).

A small in-memory ledger that tracks how many dollars each client IP has spent on each agent MODE
("text" = the Gemini chat agent, "voice" = the OpenAI Realtime voice agent) and refuses further use
once the cap (default $3, env ``AGENT_BUDGET_USD``) is reached. The two modes are tracked SEPARATELY
— a user gets $3 of text AND $3 of voice — because their costs are wildly different per turn and
billed to different providers.

Scope/limits (deliberate, documented):
  * IN-MEMORY and PER-PROCESS — resets on restart and is not shared across replicas. This is a
    cost-guardrail for a single-host demo/research deployment, not a billing system. Swap the dict
    for Redis if you need durable, multi-replica accounting.
  * The client IP is taken from ``X-Forwarded-For`` / ``X-Real-IP`` (set by our nginx, which uses
    ``real_ip``), falling back to the socket peer — so it works both behind the proxy and locally.
  * Text spend is authoritative (measured server-side from the model's token usage). Voice spend is
    reported by the browser (audio runs browser↔OpenAI, never through us), so the voice cap is
    best-effort: a cooperative client is held to it; the mint-time check still blocks a NEW session
    once the reported total is over.

Thread-safe: a single lock guards the dict (FastAPI runs sync handlers in a threadpool).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Literal

Mode = Literal["text", "voice"]

DEFAULT_BUDGET_USD = 3.0

_lock = threading.Lock()
# (ip, mode) -> cumulative USD spent this process lifetime.
_spent: dict[tuple[str, str], float] = {}


def budget_usd() -> float:
    """The per-IP, per-mode USD cap, read from ``AGENT_BUDGET_USD`` at call time (ops-tunable).

    A non-positive or malformed value disables the cap (returns 0.0 -> never exceeded).
    """
    raw = os.environ.get("AGENT_BUDGET_USD", "").strip()
    if not raw:
        return DEFAULT_BUDGET_USD
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_USD
    return val if val > 0 else 0.0


def client_ip(request: Any) -> str:
    """Best client IP for ``request``: first X-Forwarded-For hop, else X-Real-IP, else socket peer.

    ``request`` is a Starlette/FastAPI ``Request`` (has ``.headers`` and ``.client``). Returns
    ``"unknown"`` if nothing is available (then everyone shares one bucket — fail safe, not open).
    """
    headers = getattr(request, "headers", None)
    if headers is not None:
        fwd = headers.get("x-forwarded-for")
        if fwd:
            first = fwd.split(",")[0].strip()
            if first:
                return first
        real = headers.get("x-real-ip")
        if real and real.strip():
            return real.strip()
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host or "unknown"


def spent(ip: str, mode: Mode) -> float:
    """USD already spent by ``ip`` on ``mode``."""
    with _lock:
        return _spent.get((ip, mode), 0.0)


def exceeded(ip: str | None, mode: Mode) -> bool:
    """True if ``ip`` has reached/passed the cap for ``mode``.

    A falsy ``ip`` (e.g. internal/test calls with no request) is never limited. A disabled cap
    (``budget_usd() == 0``) is never exceeded.
    """
    if not ip:
        return False
    cap = budget_usd()
    if cap <= 0:
        return False
    return spent(ip, mode) >= cap


def add(ip: str | None, mode: Mode, cost_usd: float) -> float:
    """Accrue ``cost_usd`` to ``ip``'s ``mode`` tally; return the new total. No-op for falsy ip/cost."""
    if not ip or not cost_usd or cost_usd <= 0:
        return spent(ip, mode) if ip else 0.0
    with _lock:
        key = (ip, mode)
        total = _spent.get(key, 0.0) + cost_usd
        _spent[key] = total
        return total


def status(ip: str | None, mode: Mode) -> dict:
    """A client-safe snapshot for ``ip``/``mode``: spent, limit, and whether the cap is reached."""
    cap = budget_usd()
    used = spent(ip, mode) if ip else 0.0
    return {
        "mode": mode,
        "spentUsd": round(used, 4),
        "limitUsd": round(cap, 2),
        "limitReached": exceeded(ip, mode),
    }


def clear() -> None:
    """Reset the entire ledger (used by tests to isolate cases)."""
    with _lock:
        _spent.clear()
