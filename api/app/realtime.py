"""OpenAI Realtime voice-session minting (GEO-40).

The browser cannot hold the OpenAI API key, so the SPA's voice mode asks THIS endpoint for a
short-lived *ephemeral client secret*; it then uses that secret to open a WebRTC session DIRECTLY
with OpenAI (no audio passes through our server). We mint the secret server-side from
``OPENAI_API_KEY`` — which never reaches the client and is never logged.

Voice mode is OPTIONAL. With no ``OPENAI_API_KEY`` set the endpoint returns ``{"configured": false}``
(HTTP 200) so the SPA shows a tidy "add a key to enable voice" state instead of an error. The
realtime model/voice are env-tunable (``OPENAI_REALTIME_MODEL`` / ``OPENAI_REALTIME_VOICE``).

Stdlib-only (``urllib``) so the runtime image needs no new dependency; the blocking call is meant to
run inside ``fastapi.concurrency.run_in_threadpool``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("api.realtime")

# Ephemeral-secret mint endpoint (2026 Realtime API). Returns a top-level ``value`` (ek_/sk_realtime_
# prefixed) the browser uses as the bearer when POSTing its SDP offer to /v1/realtime/calls.
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

DEFAULT_REALTIME_MODEL = "gpt-realtime"
DEFAULT_REALTIME_VOICE = "marin"
_REQUEST_TIMEOUT_S = 15


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def realtime_model() -> str:
    return os.environ.get("OPENAI_REALTIME_MODEL", "").strip() or DEFAULT_REALTIME_MODEL


def realtime_voice() -> str:
    return os.environ.get("OPENAI_REALTIME_VOICE", "").strip() or DEFAULT_REALTIME_VOICE


def is_configured() -> bool:
    """True iff an OpenAI key is present (voice mode can be offered)."""
    return bool(_api_key())


def _redact(text: str, key: str) -> str:
    """Scrub the API key from any text before it is logged (defence in depth)."""
    return text.replace(key, "[REDACTED]") if key else text


def mint_client_secret() -> dict:
    """Mint an ephemeral OpenAI Realtime client secret. Blocking — run in a threadpool.

    Never raises and never logs/returns the API key. Shape:
      - no key configured →  ``{"configured": False, "model", "voice"}``
      - success           →  ``{"configured": True, "value", "model", "voice"}``
      - upstream failure  →  ``{"configured": True, "error": <client-safe message>}``
    """
    key = _api_key()
    model = realtime_model()
    voice = realtime_voice()
    if not key:
        return {"configured": False, "model": model, "voice": voice}

    payload = json.dumps(
        {
            "session": {
                "type": "realtime",
                "model": model,
                "audio": {"output": {"voice": voice}},
            }
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_CLIENT_SECRETS_URL,
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - best-effort body read
            detail = ""
        log.warning("realtime mint failed: HTTP %s %s", exc.code, _redact(detail, key)[:300])
        return {
            "configured": True,
            "error": f"OpenAI rejected the voice session request ({exc.code}). "
            "Check OPENAI_API_KEY and OPENAI_REALTIME_MODEL.",
        }
    except Exception as exc:  # network / timeout / DNS
        log.warning("realtime mint error: %s", _redact(str(exc), key))
        return {"configured": True, "error": "Could not reach OpenAI to start voice mode."}

    # The ephemeral secret is the top-level ``value``; older shapes nest it under client_secret.value.
    value = data.get("value")
    if not value and isinstance(data.get("client_secret"), dict):
        value = data["client_secret"].get("value")
    if not value:
        log.warning("realtime mint: response missing client-secret value (keys=%s)", sorted(data))
        return {"configured": True, "error": "OpenAI returned an unexpected session response."}
    return {"configured": True, "value": value, "model": model, "voice": voice}
